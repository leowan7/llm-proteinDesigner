"""RunPod webhook handler.

RunPod POSTs job completion/failure events to this endpoint. The handler:
1. Validates the HMAC-SHA256 signature to reject spoofed requests.
2. Resolves the internal job by runpod_job_id.
3. Calculates GPU seconds from started_at timestamp.
4. Updates DB status and results.
5. Records billing via Stripe Billing Meters API.
6. Sends email notification via Resend.
7. Publishes SSE status event via Redis pub/sub.

RunPod webhook status values:
    COMPLETED, FAILED, CANCELLED, TIMED_OUT
"""

import datetime
import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request

from billing.stripe_client import record_gpu_usage
from config import settings
from db.connection import get_db_pool
from jobs.notifications import send_completion_email, send_failure_email
from worker.tasks import publish_status, update_job_status

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Map RunPod terminal statuses to internal JobStatus values.
_RUNPOD_STATUS_MAP: dict[str, str] = {
    "COMPLETED": "complete",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "TIMED_OUT": "failed",
}


def validate_runpod_signature(body: bytes, signature: str | None) -> None:
    """Validate the HMAC-SHA256 signature sent by RunPod in the request header.

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
    """Handle RunPod job completion and failure webhook callbacks.

    Validates the HMAC signature, resolves the job, records billing,
    sends email notification, and publishes an SSE status event.

    Returns:
        {"received": True} on all valid requests (even unrecognised status values).
    """
    body = await request.body()
    validate_runpod_signature(body, request.headers.get("X-RunPod-Signature"))

    payload = json.loads(body)
    runpod_job_id: str = payload.get("id", "")
    runpod_status: str = payload.get("status", "")

    internal_status = _RUNPOD_STATUS_MAP.get(runpod_status)
    if not internal_status:
        # Non-terminal status or unrecognised value — acknowledge and ignore.
        return {"received": True}

    pool = await get_db_pool()

    # Resolve the internal job by the RunPod job ID.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, started_at FROM public.jobs WHERE runpod_job_id = $1",
            runpod_job_id,
        )

    if not row:
        return {"received": True}

    job_id = str(row["id"])
    user_id = row["user_id"]

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
        error_category = payload.get("error", {}).get("category", "Provider error")

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

    # Record billing for completed or cancelled jobs (user pays for consumed GPU time).
    if internal_status in ("complete", "cancelled") and gpu_seconds > 0:
        async with pool.acquire() as conn:
            cust_row = await conn.fetchrow(
                "SELECT stripe_customer_id FROM public.users WHERE id = $1",
                user_id,
            )
        if cust_row and cust_row["stripe_customer_id"]:
            record_gpu_usage(cust_row["stripe_customer_id"], gpu_seconds)

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
                tool=payload.get("input", {}).get("job_spec", {}).get("tool", "Unknown"),
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
