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
from fastapi import HTTPException

from billing.stripe_client import record_gpu_usage
from config import settings
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
        elapsed = datetime.datetime.now(datetime.timezone.utc) - row["started_at"]
        gpu_seconds = int(elapsed.total_seconds())

    gpu_cost_usd = round(
        gpu_seconds * settings.gpu_price_per_second * (1 + settings.gpu_markup_percent / 100),
        4,
    )

    user_id = str(row["user_id"])

    # Import worker tasks here to avoid circular imports at module load time.
    from worker.tasks import publish_status, update_job_status

    # Update DB status and GPU cost.
    await update_job_status(job_id, "cancelled", stage="Cancelled", gpu_seconds=gpu_seconds)
    async with pool.acquire() as conn:
        update_result = await conn.execute(
            "UPDATE public.jobs SET gpu_cost_usd = $1 WHERE id = $2 AND status IN ('running', 'queued')",
            gpu_cost_usd,
            job_id,
        )
    # update_result is "UPDATE N" — if N=0 the job transitioned to a terminal
    # state (e.g. 'complete' via webhook) between our initial fetch and this
    # write. Log a warning so operators can investigate if billing appears wrong.
    rows_updated = int(update_result.split()[-1])
    if rows_updated == 0:
        _log.warning(
            "cancel_job_by_id: gpu_cost_usd not written for job %s — "
            "status transitioned before UPDATE (TOCTOU); verify billing.",
            job_id,
        )

    # Publish SSE terminal event.
    await publish_status(job_id, "cancelled", "Cancelled")

    # Record partial billing if GPU time was consumed.
    if gpu_seconds > 0:
        async with pool.acquire() as conn:
            cust_row = await conn.fetchrow(
                "SELECT stripe_customer_id FROM public.users WHERE id = $1",
                user_id,
            )
        if cust_row and cust_row["stripe_customer_id"]:
            record_gpu_usage(cust_row["stripe_customer_id"], job_id, gpu_seconds)

    return {
        "status": "cancelled",
        "gpu_seconds": gpu_seconds,
        "gpu_cost_usd": gpu_cost_usd,
        "user_id": user_id,
    }
