"""Job API endpoints.

Provides:
- POST /jobs/launch           — payment gate check then dispatch a job
- GET  /jobs/              — list jobs for current user (paginated, filterable)
- GET  /jobs/{job_id}      — get single job with candidates
- GET  /jobs/{job_id}/status   — SSE stream of status events
- POST /jobs/{job_id}/cancel   — cancel a running job
- GET  /jobs/{job_id}/download — download all designs as a ZIP archive
"""

import datetime
import io
import json
import uuid as uuid_mod
import zipfile
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.jobspec import JobSpec
from auth.dependencies import get_current_user
from middleware.rate_limit import limiter
from billing.stripe_client import check_payment_method, get_or_create_customer, record_gpu_usage
from config import settings
from db.connection import get_db_pool
from gpu.runpod import RunPodProvider
from jobs.dispatch import launch_job
from storage.client import generate_presigned_get_url, generate_presigned_put_url, get_s3_client

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_SSE_PER_USER = settings.max_sse_connections_per_user


async def _check_sse_limit(user_id: str) -> None:
    """Check if user has exceeded max concurrent SSE connections.

    Uses a Redis key with TTL to track active SSE connections.
    Raises HTTPException 429 if limit exceeded.
    """
    r = aioredis.from_url(settings.redis_url)
    key = f"sse_count:{user_id}"
    count = await r.incr(key)
    await r.expire(key, 300)  # Auto-expire after 5 min (safety net)
    if count > MAX_SSE_PER_USER:
        await r.decr(key)
        await r.aclose()
        raise HTTPException(status_code=429, detail="Too many active connections")
    await r.aclose()


async def _release_sse_slot(user_id: str) -> None:
    """Decrement the SSE connection counter when a stream closes."""
    r = aioredis.from_url(settings.redis_url)
    key = f"sse_count:{user_id}"
    await r.decr(key)
    await r.aclose()


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
@limiter.limit("5/minute")
async def launch_job_endpoint(
    request: Request,
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
    # Validate job_id is a valid UUID format.
    try:
        uuid_mod.UUID(body.job_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid job_id format — must be a valid UUID")

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

# Status values accepted by the list endpoint (per D-17: All, Running, Complete, Failed).
# 'cancelled' and 'queued' are intentionally excluded.
ALLOWED_STATUS_FILTERS = {"running", "complete", "failed"}


@router.get("/")
async def list_jobs(
    limit: int = Query(default=25, ge=1, le=100),
    status: str | None = Query(default=None),
    before: str | None = Query(default=None),
    user_id: str = Depends(get_current_user),
):
    """Return paginated job history for the current user.

    Supports keyset pagination via the ``before`` cursor (ISO timestamp) and
    optional status filtering. Results are ordered by created_at descending so
    the newest jobs appear first.

    Args:
        limit: Maximum number of jobs to return (1–100, default 25).
        status: Optional status filter. Accepted values: ``running``,
            ``complete``, ``failed``. Omit for all statuses. Returns 400
            for any other value.
        before: ISO 8601 timestamp cursor. Only jobs created before this
            timestamp are returned, enabling keyset pagination. Returns 400
            if the value cannot be parsed as a timestamp.
        user_id: Injected by the auth dependency.

    Returns:
        Dict with ``jobs`` list and ``has_more`` bool.

    Raises:
        HTTPException 400: If ``status`` is not in ALLOWED_STATUS_FILTERS or
            ``before`` is not a valid ISO timestamp.
    """
    # Validate status filter.
    if status is not None and status not in ALLOWED_STATUS_FILTERS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status filter '{status}'. Allowed values: {sorted(ALLOWED_STATUS_FILTERS)}",
        )

    # Validate and parse the pagination cursor.
    before_dt: datetime.datetime | None = None
    if before is not None:
        try:
            before_dt = datetime.datetime.fromisoformat(before)
            # Ensure timezone-aware for comparison with timestamptz column.
            if before_dt.tzinfo is None:
                before_dt = before_dt.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid 'before' cursor '{before}'. Expected ISO 8601 timestamp.",
            )

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, tool, status, name, created_at, completed_at,
                      gpu_cost_usd, results->>'candidate_count' AS candidate_count,
                      session_id
               FROM public.jobs
               WHERE user_id = $1
                 AND ($2::text IS NULL OR status = $2)
                 AND ($3::timestamptz IS NULL OR created_at < $3)
               ORDER BY created_at DESC
               LIMIT $4""",
            user_id,
            status,
            before_dt,
            limit,
        )

    jobs = [
        {
            "id": str(r["id"]),
            "tool": r["tool"] or "",
            "status": r["status"],
            "name": r["name"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "gpu_cost_usd": float(r["gpu_cost_usd"]) if r["gpu_cost_usd"] else None,
            "candidate_count": int(r["candidate_count"]) if r["candidate_count"] else None,
            "session_id": str(r["session_id"]) if r["session_id"] else None,
        }
        for r in rows
    ]

    # has_more is true when exactly `limit` rows were returned — there may be more.
    return {"jobs": jobs, "has_more": len(rows) == limit}


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
    # Enforce per-user SSE connection limit.
    await _check_sse_limit(user_id)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, stage FROM public.jobs WHERE id = $1 AND user_id = $2",
            job_id,
            user_id,
        )
    if not row:
        await _release_sse_slot(user_id)
        raise HTTPException(status_code=404, detail="Job not found")

    async def _wrapped_sse():
        """Wrap SSE generator to release the connection slot on close."""
        try:
            async for event in _sse_event_generator(job_id, row["status"], row["stage"] or ""):
                yield event
        finally:
            await _release_sse_slot(user_id)

    return StreamingResponse(
        _wrapped_sse(),
        media_type="text/event-stream",
    )


@router.get("/{job_id}/download")
@limiter.limit("10/minute")
async def download_all_designs(request: Request, job_id: str, user_id: str = Depends(get_current_user)):
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


# ---------------------------------------------------------------------------
# On-demand upload URL endpoint (container auth via job token)
# ---------------------------------------------------------------------------

class UploadUrlsRequest(BaseModel):
    """Request body for POST /jobs/{job_id}/upload-urls."""

    filenames: list[str]


@router.post("/{job_id}/upload-urls")
async def get_upload_urls(job_id: str, body: UploadUrlsRequest, request: Request):
    """Generate fresh presigned PUT URLs for container file uploads.

    Authenticated via job_token (Bearer token in Authorization header),
    NOT via user JWT. The container receives the job_token as an env var.

    Args:
        job_id: Job UUID string.
        body: List of filenames to generate upload URLs for.

    Returns:
        {"urls": {"filename.pdb": "https://presigned-url", ...}}
    """
    # Extract job token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing job token")
    provided_token = auth_header[7:]  # Strip "Bearer "

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, status, job_token FROM public.jobs WHERE id = $1",
            job_id,
        )

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    if not row["job_token"] or provided_token != row["job_token"]:
        raise HTTPException(status_code=401, detail="Invalid job token")

    if row["status"] != "running":
        raise HTTPException(status_code=409, detail="Job is not running")

    # Generate presigned PUT URLs for each requested filename
    user_id = str(row["user_id"])
    output_prefix = f"users/{user_id}/jobs/{job_id}/outputs/"
    urls = {}
    for filename in body.filenames:
        # Sanitize filename — strip path separators to prevent traversal
        safe_name = filename.replace("/", "_").replace("\\", "_")
        key = f"{output_prefix}{safe_name}"
        urls[filename] = generate_presigned_put_url(
            key, expires_in=settings.upload_url_expiry_seconds
        )

    return {"urls": urls}


# ---------------------------------------------------------------------------
# Get single job
# ---------------------------------------------------------------------------

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
