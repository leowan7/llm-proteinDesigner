"""Job API endpoints.

Provides:
- POST /jobs/launch           — payment gate check then dispatch a job
- GET  /jobs/              — list jobs for current user
- GET  /jobs/{job_id}      — get single job with candidates
- GET  /jobs/{job_id}/status   — SSE stream of status events
- POST /jobs/{job_id}/cancel   — cancel a running job
- GET  /jobs/{job_id}/download — download all designs as a ZIP archive
"""

import datetime
import io
import json
import zipfile

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.jobspec import JobSpec
from auth.dependencies import get_current_user
from billing.stripe_client import check_payment_method, get_or_create_customer, record_gpu_usage
from config import settings
from db.connection import get_db_pool
from gpu.runpod import RunPodProvider
from jobs.dispatch import launch_job
from storage.client import generate_presigned_get_url, get_s3_client

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Map tool name to RunPod endpoint ID from settings.
_ENDPOINT_IDS: dict[str, str] = {
    "rfdiffusion": settings.runpod_endpoint_rfdiffusion,
    "rfantibody": settings.runpod_endpoint_rfantibody,
    "bindcraft": settings.runpod_endpoint_bindcraft,
    "boltzgen": settings.runpod_endpoint_boltzgen,
}


# ---------------------------------------------------------------------------
# Launch endpoint — payment gate + dispatch
# ---------------------------------------------------------------------------

class LaunchRequest(BaseModel):
    """Request body for POST /jobs/launch."""

    job_id: str
    job_name: str | None = None


@router.post("/launch")
async def launch_job_endpoint(
    body: LaunchRequest,
    user_id: str = Depends(get_current_user),
):
    """BILL-04 / JOB-01: Payment gate check then job dispatch.

    Verifies the authenticated user has a Stripe payment method on file before
    calling launch_job(). Returns 402 if no payment method is found so the
    frontend can redirect to Stripe Checkout.

    Args:
        body.job_id: UUID of an existing job row (created by the agent wizard).
        user_id: Injected by the auth dependency.

    Returns:
        JSON with job_id and status="queued" on success.

    Raises:
        HTTPException 402: If the user has no payment method configured.
        HTTPException 404: If the job row does not exist for this user.
    """
    pool = await get_db_pool()

    # Fetch job row to validate ownership and get job_spec.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT job_spec FROM public.jobs WHERE id = $1 AND user_id = $2",
            body.job_id,
            user_id,
        )
    if not row:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Resolve Stripe customer and check payment method (skip if Stripe not configured).
    if settings.stripe_secret_key:
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT email, stripe_customer_id FROM public.users WHERE id = $1",
                user_id,
            )
        if not user_row:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="User not found")

        stripe_customer_id = await get_or_create_customer(
            email=user_row["email"],
            user_id=user_id,
            pool=pool,
        )

        has_payment = check_payment_method(stripe_customer_id)
        if not has_payment:
            raise HTTPException(
                status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
                detail="payment_required",
            )

    # Save user-provided job name if given.
    if body.job_name:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.jobs SET name = $1 WHERE id = $2",
                body.job_name,
                body.job_id,
            )

    # Parse job_spec and dispatch.
    spec_data = json.loads(row["job_spec"] or "{}")
    job_spec = JobSpec(**spec_data)
    await launch_job(job_id=body.job_id, job_spec=job_spec, user_id=user_id, pool=pool)

    return {"job_id": body.job_id, "status": "queued"}


# ---------------------------------------------------------------------------
# SSE generator — extracted for testability
# ---------------------------------------------------------------------------

async def _sse_event_generator(job_id: str, current_status: str, current_stage: str):
    """Async generator that yields SSE-formatted events for a job.

    Emits the current state immediately, then subscribes to Redis pub/sub
    for live updates. Breaks on terminal status.

    Args:
        job_id: Job UUID string.
        current_status: Current status value from the DB.
        current_stage: Current stage label from the DB.

    Yields:
        SSE-formatted strings (``data: {json}\\n\\n``).
    """
    # Emit current state first so the client has something immediately.
    current = json.dumps({"job_id": job_id, "status": current_status, "stage": current_stage or ""})
    yield f"data: {current}\n\n"

    # If already terminal, no need to subscribe.
    if current_status in ("complete", "failed", "cancelled"):
        return

    r = aioredis.from_url(settings.redis_url)
    pubsub = r.pubsub()
    await pubsub.subscribe(f"job:{job_id}:status")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data_bytes = message["data"]
                payload_str = data_bytes.decode() if isinstance(data_bytes, bytes) else data_bytes
                yield f"data: {payload_str}\n\n"
                data = json.loads(payload_str)
                if data.get("status") in ("complete", "failed", "cancelled"):
                    break
    finally:
        await pubsub.unsubscribe(f"job:{job_id}:status")
        await r.aclose()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/")
async def list_jobs(user_id: str = Depends(get_current_user)):
    """Return the 50 most recent jobs for the current user.

    Args:
        user_id: Injected by the auth dependency.

    Returns:
        List of job summary dicts ordered by created_at descending.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, status, stage, job_spec, created_at, completed_at, gpu_cost_usd
               FROM public.jobs WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50""",
            user_id,
        )
    return [
        {
            "id": str(r["id"]),
            "status": r["status"],
            "tool": json.loads(r["job_spec"] or "{}").get("tool", ""),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "gpu_cost_usd": float(r["gpu_cost_usd"]) if r["gpu_cost_usd"] else None,
        }
        for r in rows
    ]


@router.get("/{job_id}/status")
async def job_status_stream(job_id: str, user_id: str = Depends(get_current_user)):
    """Stream job status events via Server-Sent Events.

    Emits the current state immediately. For non-terminal jobs, subscribes to
    the Redis pub/sub channel and forwards events until a terminal status is
    received, then closes the stream.

    Args:
        job_id: Job UUID string.
        user_id: Injected by the auth dependency.

    Returns:
        StreamingResponse with media_type="text/event-stream".
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, stage FROM public.jobs WHERE id = $1 AND user_id = $2",
            job_id,
            user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        _sse_event_generator(job_id, row["status"], row["stage"] or ""),
        media_type="text/event-stream",
    )


@router.get("/{job_id}/download")
async def download_all_designs(job_id: str, user_id: str = Depends(get_current_user)):
    """Download all design outputs for a completed job as a ZIP archive.

    Fetches all objects under ``users/{user_id}/jobs/{job_id}/outputs/`` from
    S3/MinIO and returns them as a single ZIP file.

    Args:
        job_id: Job UUID string.
        user_id: Injected by the auth dependency.

    Returns:
        StreamingResponse with media_type="application/zip".

    Raises:
        HTTPException 404: If the job does not exist or is not complete.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM public.jobs WHERE id = $1 AND user_id = $2",
            job_id,
            user_id,
        )
    if not row or row["status"] != "complete":
        raise HTTPException(status_code=404, detail="No completed job found")

    s3 = get_s3_client()
    prefix = f"users/{user_id}/jobs/{job_id}/outputs/"
    objects = s3.list_objects_v2(Bucket=settings.s3_bucket_name, Prefix=prefix)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for obj in objects.get("Contents", []):
            body = s3.get_object(Bucket=settings.s3_bucket_name, Key=obj["Key"])["Body"].read()
            filename = obj["Key"].split("/")[-1]
            zf.writestr(filename, body)

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="job_{job_id}_designs.zip"'},
    )


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, user_id: str = Depends(get_current_user)):
    """Cancel a running job.

    Calls RunPod to stop the GPU job, calculates partial billing, updates the
    DB, publishes an SSE event, and records the Stripe meter event.

    Args:
        job_id: Job UUID string.
        user_id: Injected by the auth dependency.

    Returns:
        Dict with status, gpu_seconds consumed, and gpu_cost_usd charged.

    Raises:
        HTTPException 404: If no running job is found for this user.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT runpod_job_id, job_spec, started_at
               FROM public.jobs
               WHERE id = $1 AND user_id = $2 AND status = 'running'""",
            job_id,
            user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="No running job found")

    # Cancel on the GPU provider.
    if row["runpod_job_id"]:
        provider = RunPodProvider(api_key=settings.runpod_api_key)
        spec = json.loads(row["job_spec"] or "{}")
        tool = spec.get("tool", "")
        endpoint_id = _ENDPOINT_IDS.get(tool, "")
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

    # Update DB status.
    from worker.tasks import publish_status, update_job_status
    await update_job_status(job_id, "cancelled", stage="Cancelled", gpu_seconds=gpu_seconds)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.jobs SET gpu_cost_usd = $1 WHERE id = $2",
            gpu_cost_usd,
            job_id,
        )

    # Publish SSE terminal event.
    await publish_status(job_id, "cancelled", "Cancelled")

    # Record partial billing.
    if gpu_seconds > 0:
        async with pool.acquire() as conn:
            cust_row = await conn.fetchrow(
                "SELECT stripe_customer_id FROM public.users WHERE id = $1", user_id
            )
        if cust_row and cust_row["stripe_customer_id"]:
            record_gpu_usage(cust_row["stripe_customer_id"], job_id, gpu_seconds)

    return {"status": "cancelled", "gpu_seconds": gpu_seconds, "gpu_cost_usd": gpu_cost_usd}


@router.get("/{job_id}")
async def get_job(job_id: str, user_id: str = Depends(get_current_user)):
    """Return full job data including candidates with presigned download URLs.

    Args:
        job_id: Job UUID string.
        user_id: Injected by the auth dependency.

    Returns:
        Dict with all job fields and a candidates list (empty if not complete).

    Raises:
        HTTPException 404: If the job does not exist for this user.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.jobs WHERE id = $1 AND user_id = $2",
            job_id,
            user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    candidates = []
    if row["status"] == "complete":
        async with pool.acquire() as conn:
            cand_rows = await conn.fetch(
                "SELECT rank, pdb_key, scores FROM public.job_candidates WHERE job_id = $1 ORDER BY rank",
                row["id"],
            )
        candidates = [
            {
                "rank": r["rank"],
                "pdb_key": r["pdb_key"],
                "scores": json.loads(r["scores"]) if r["scores"] else {},
                "download_url": generate_presigned_get_url(r["pdb_key"]),
            }
            for r in cand_rows
        ]

    return {
        "id": str(row["id"]),
        "status": row["status"],
        "stage": row["stage"],
        "tool": json.loads(row["job_spec"] or "{}").get("tool", ""),
        "gpu_seconds": row["gpu_seconds"],
        "gpu_cost_usd": float(row["gpu_cost_usd"]) if row["gpu_cost_usd"] else None,
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "error_category": row["error_category"],
        "results": json.loads(row["results"]) if row["results"] else None,
        "candidates": candidates,
        "job_spec": json.loads(row["job_spec"]) if row["job_spec"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
