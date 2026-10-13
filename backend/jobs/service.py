"""Shared job business logic used by both user and admin routers.

Extracts cancellation logic from jobs/router.py into a reusable service
function so both the user-scoped cancel endpoint and the admin cancel endpoint
can call the same billing + DB + SSE update path.

Exports:
    cancel_job_by_id: Cancel a running job by ID, regardless of owner.
    TOOL_IMAGES: Mapping of tool name to RunPod Docker image (shared constant).
"""

import datetime
import json
import logging

import asyncpg
from billing.stripe_client import record_gpu_usage
from config import settings
from fastapi import HTTPException
from gpu import endpoint_for_tool, get_provider

# Legacy tool→RunPod image map. Retained for backward compatibility with admin
# and test code that imports ``TOOL_IMAGES`` directly. The active provider's
# endpoint identifier is resolved via ``gpu.endpoint_for_tool(tool)``.
TOOL_IMAGES: dict[str, str] = {
    "rfdiffusion": settings.runpod_image_rfdiffusion,
    "rfantibody": settings.runpod_image_rfantibody,
    "bindcraft": settings.runpod_image_bindcraft,
    "boltzgen": settings.runpod_image_boltzgen,
}

_log = logging.getLogger(__name__)


async def cancel_job_by_id(job_id: str, pool: asyncpg.Pool) -> dict:
    """Cancel a running or queued job regardless of owner.

    Extracts the business logic from the user cancel endpoint so it can be
    reused by the admin cancel endpoint without the ownership check.

    Steps:
    1. Fetch job row (no user_id ownership filter — works for both user and admin).
    2. Cancel on RunPod if runpod_job_id is set.
    3. Calculate partial GPU seconds and cost.
    4. Update DB status to "cancelled".
    5. Publish SSE terminal event.
    6. Record Stripe meter event if any GPU seconds accrued.

    Args:
        job_id: UUID string of the job to cancel.
        pool: asyncpg connection pool to use for DB queries.

    Returns:
        Dict with keys: status, gpu_seconds, gpu_cost_usd, user_id.

    Raises:
        HTTPException 404: If no running or queued job is found with the given ID.
    """
    # Fetch job row — no user_id filter so both user (ownership checked in router)
    # and admin can call this function.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT runpod_job_id, job_spec, started_at, user_id
               FROM public.jobs
               WHERE id = $1 AND status IN ('running', 'queued')""",
            job_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="No running job found")

    # Cancel on the active GPU provider (Modal by default, RunPod-emergency fallback).
    # endpoint_id semantics depend on the provider; ``endpoint_for_tool`` handles that.
    if row["runpod_job_id"]:
        provider = get_provider()
        spec = json.loads(row["job_spec"] or "{}")
        tool = spec.get("tool", "")
        try:
            endpoint_id = endpoint_for_tool(tool)
        except ValueError:
            # If no endpoint is configured (e.g. provider-less tool), skip provider
            # cancel and proceed to mark the job cancelled in the DB.
            endpoint_id = ""
        if endpoint_id:
            await provider.cancel_job(endpoint_id, row["runpod_job_id"])

    # Calculate partial GPU seconds from started_at.
    gpu_seconds = 0
    if row["started_at"]:
        elapsed = datetime.datetime.now(datetime.UTC) - row["started_at"]
        gpu_seconds = int(elapsed.total_seconds())

    gpu_cost_usd = round(
        gpu_seconds * settings.gpu_price_per_second * (1 + settings.gpu_markup_percent / 100),
        4,
    )

    user_id = str(row["user_id"])

    # Import worker tasks here to avoid circular imports at module load time.
    from worker.tasks import publish_status, update_job_status

    # Update DB status, and claim the job in the same write. The reaper now
    # meters a run it sweeps (worker/cleanup.py:376), so this route can no
    # longer settle a job another route has already settled: the flag makes
    # the status write conditional on the row being non-terminal and reports
    # whether it won, the protocol the webhook takes (webhooks/router.py:247)
    # and the reaper takes with its own 'running' guard (worker/cleanup.py:276).
    claimed = await update_job_status(
        job_id,
        "cancelled",
        stage="Cancelled",
        gpu_seconds=gpu_seconds,
        require_non_terminal=True,
    )
    if claimed:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.jobs SET gpu_cost_usd = $1 WHERE id = $2",
                gpu_cost_usd,
                job_id,
            )
    else:
        # The row went terminal between the fetch above and this write, so the
        # route that took it owns both the cost column and the meter.
        #
        # This cost write used to carry its own `AND status IN ('running',
        # 'queued')` guard, which the unconditional status write immediately
        # above had already falsified: by the time it ran the row read
        # 'cancelled', so it matched nothing and gpu_cost_usd was left NULL on
        # every cancelled job while Stripe was still metered below. The claim
        # replaces that guard, so the column is now written whenever this
        # route is the one that charges. Read off the two statements, not
        # reproduced against a live database.
        _log.warning(
            "cancel_job_by_id: job %s went terminal before this cancel could "
            "claim it; its cost and metering belong to the route that did",
            job_id,
        )

    # Publish SSE terminal event.
    await publish_status(job_id, "cancelled", "Cancelled")

    # Record partial billing if GPU time was consumed.
    #
    # Phase 12: the cancel path runs from both the user-scoped router and the
    # admin router. Neither can rely on the calling user being a member of the
    # job's org (admins are not org members), so we resolve the billing
    # customer the same way the webhook handler does: from the job's
    # organization_id through public.org_stripe_customer.
    if claimed and gpu_seconds > 0:
        async with pool.acquire() as conn:
            stripe_customer_id = await conn.fetchval(
                """SELECT public.org_stripe_customer(j.organization_id)
                   FROM public.jobs j
                   WHERE j.id = $1""",
                job_id,
            )
        if stripe_customer_id:
            record_gpu_usage(stripe_customer_id, job_id, gpu_seconds)

    return {
        "status": "cancelled",
        "gpu_seconds": gpu_seconds,
        "gpu_cost_usd": gpu_cost_usd,
        "user_id": user_id,
    }
