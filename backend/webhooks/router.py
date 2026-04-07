"""RunPod webhook handler for GPU Pod job completion.

The container running in a RunPod Pod POSTs results to this endpoint when the
pipeline finishes (success or failure). The handler:
1. Validates the request (signature check in prod, open in dev).
2. Resolves the internal job by job_id from the payload.
3. Calculates GPU seconds from started_at timestamp.
4. Updates DB status and results.
5. Records billing via Stripe Billing Meters API.
6. Terminates the RunPod pod to stop billing.
7. Sends email notification via Resend.
8. Publishes SSE status event via Redis pub/sub.

Payload structure (POSTed by run_pipeline.py in the container):
    id       str   Kendrew job UUID
    pod_id   str   RunPod pod ID (for termination)
    status   str   "COMPLETED" or "FAILED"
    output   dict  Pipeline results (candidates, counts, etc.)
    error    dict  Error info if status is "FAILED"
"""

import datetime
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from billing.stripe_client import record_gpu_usage
from config import settings
from db.connection import get_db_pool
from gpu.runpod import RunPodProvider
from jobs.notifications import send_completion_email, send_failure_email
from worker.tasks import publish_status, update_job_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Map RunPod terminal statuses to internal JobStatus values.
_RUNPOD_STATUS_MAP: dict[str, str] = {
    "COMPLETED": "complete",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "TIMED_OUT": "failed",
}


def validate_runpod_signature(body: bytes, signature: str | None) -> None:
    """Validate the HMAC-SHA256 signature sent in the request header.

    Skipped when runpod_webhook_secret is not configured (local dev).

    Args:
        body: Raw request body bytes.
        signature: Value of the X-RunPod-Signature header (may be None).

    Raises:
        HTTPException 401: When signature is missing or does not match.
    """
    if not settings.runpod_webhook_secret:
        return  # Skip validation in local dev

    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    expected = hmac.new(
        settings.runpod_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


@router.post("/runpod")
async def runpod_webhook(request: Request):
    """Handle pipeline completion webhooks from RunPod Pod containers.

    The container POSTs results here when the pipeline finishes. This handler
    processes results, records billing, terminates the pod, and notifies the user.

    Returns:
        {"received": True} on all valid requests.
    """
    body = await request.body()
    validate_runpod_signature(body, request.headers.get("X-RunPod-Signature"))

    payload = json.loads(body)
    job_id: str = payload.get("id", "")
    pod_id: str = payload.get("pod_id", "")
    runpod_status: str = payload.get("status", "")

    internal_status = _RUNPOD_STATUS_MAP.get(runpod_status)
    if not internal_status:
        # Non-terminal status or unrecognised value — acknowledge and ignore.
        return {"received": True}

    # Replay protection: reject payloads older than 5 minutes
    payload_timestamp = payload.get("timestamp")
    if payload_timestamp:
        try:
            sent_at = datetime.datetime.fromisoformat(payload_timestamp)
            age = datetime.datetime.now(datetime.timezone.utc) - sent_at
            if age.total_seconds() > 300:  # 5 minutes
                logger.warning("Rejected stale webhook: job_id=%s age=%.0fs", job_id, age.total_seconds())
                return {"received": True}
        except (ValueError, TypeError):
            pass  # If timestamp is malformed, skip replay check (don't break existing webhooks)

    pool = await get_db_pool()

    # Resolve the internal job by job UUID (pod webhook sends job_id directly).
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, started_at, runpod_job_id FROM public.jobs WHERE id = $1",
            job_id,
        )

    if not row:
        # Try fallback: look up by pod ID stored in runpod_job_id column.
        if pod_id:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, user_id, started_at, runpod_job_id FROM public.jobs WHERE runpod_job_id = $1",
                    pod_id,
                )
        if not row:
            logger.warning("Webhook received for unknown job_id=%s pod_id=%s", job_id, pod_id)
            return {"received": True}

    job_id = str(row["id"])
    user_id = row["user_id"]
    stored_pod_id = row["runpod_job_id"] or pod_id

    # Double-processing guard: skip if job is already in a terminal state
    async with pool.acquire() as conn:
        current_row = await conn.fetchrow(
            "SELECT status FROM public.jobs WHERE id = $1", job_id
        )
    if current_row and current_row["status"] in ("complete", "failed", "cancelled"):
        logger.info("Webhook skipped: job %s already in terminal state %s", job_id, current_row["status"])
        return {"received": True}

    # Calculate GPU seconds consumed since the job started running.
    gpu_seconds = 0
    if row["started_at"]:
        elapsed = datetime.datetime.now(datetime.timezone.utc) - row["started_at"]
        gpu_seconds = int(elapsed.total_seconds())

    gpu_cost_usd = round(
        gpu_seconds * settings.gpu_price_per_second * (1 + settings.gpu_markup_percent / 100),
        4,
    )

    # Build results payload for completed jobs.
    results_json: str | None = None
    output: dict = {}
    if internal_status == "complete":
        output = payload.get("output", {})
        candidate_count: int = output.get("candidate_count", 0)
        zero_output = candidate_count == 0
        results_json = json.dumps({
            "candidate_count": candidate_count,
            "next_steps": output.get("next_steps", ""),
            "zero_output": zero_output,
        })

        # Persist individual candidate rows.
        candidates = output.get("candidates", [])
        if candidates:
            async with pool.acquire() as conn:
                for c in candidates:
                    await conn.execute(
                        """INSERT INTO public.job_candidates (job_id, rank, pdb_key, scores)
                           VALUES ($1, $2, $3, $4::jsonb)""",
                        row["id"],
                        c["rank"],
                        c["pdb_key"],
                        json.dumps(c.get("scores", {})),
                    )

    error_category: str | None = None
    if internal_status == "failed":
        error_info = payload.get("error", {})
        error_category = error_info.get("category", "Pipeline error")

    # Update DB status.
    await update_job_status(
        job_id,
        internal_status,
        stage=internal_status.capitalize(),
        gpu_seconds=gpu_seconds,
        error_category=error_category,
    )

    # Persist cost and results.
    if results_json:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.jobs SET results = $1::jsonb, gpu_cost_usd = $2 WHERE id = $3",
                results_json,
                gpu_cost_usd,
                row["id"],
            )
    else:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.jobs SET gpu_cost_usd = $1 WHERE id = $2",
                gpu_cost_usd,
                row["id"],
            )

    # Publish SSE status update.
    await publish_status(job_id, internal_status, internal_status.capitalize())

    # ---------- Terminate the RunPod pod to stop billing ----------
    if stored_pod_id:
        try:
            provider = RunPodProvider(api_key=settings.runpod_api_key)
            await provider.terminate_pod(stored_pod_id)
            logger.info("Terminated pod %s for job %s", stored_pod_id, job_id)
        except Exception as exc:
            # Log but don't fail the webhook — orphan cleanup will catch it.
            logger.error("Failed to terminate pod %s: %s", stored_pod_id, exc)

    # Record billing for completed or cancelled jobs (user pays for consumed GPU time).
    if internal_status in ("complete", "cancelled") and gpu_seconds > 0:
        async with pool.acquire() as conn:
            cust_row = await conn.fetchrow(
                "SELECT stripe_customer_id FROM public.users WHERE id = $1",
                user_id,
            )
        if cust_row and cust_row["stripe_customer_id"]:
            record_gpu_usage(cust_row["stripe_customer_id"], job_id, gpu_seconds)

    # Send email notification.
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT email FROM auth.users WHERE id = $1", user_id
        )

    if user_row:
        if internal_status == "complete":
            await send_completion_email(
                to_email=user_row["email"],
                job_id=job_id,
                tool=payload.get("output", {}).get("job_spec", {}).get("tool", "Unknown"),
                num_designs=output.get("candidate_count", 0),
                runtime_min=gpu_seconds // 60,
            )
        elif internal_status == "failed":
            await send_failure_email(
                to_email=user_row["email"],
                job_id=job_id,
                error_category=error_category or "Unknown error",
            )

    return {"received": True}
