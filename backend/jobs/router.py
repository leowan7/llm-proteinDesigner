"""Job API endpoints.

Provides:
- POST /jobs/launch           — payment gate check then dispatch a job
- GET  /jobs/              — list jobs for current user (paginated, filterable)
- GET  /jobs/{job_id}      — get single job with candidates
- GET  /jobs/{job_id}/status   — SSE stream of status events
- POST /jobs/{job_id}/cancel   — cancel a running job
- GET  /jobs/{job_id}/download — download all designs as a ZIP archive
"""

import asyncio
import datetime
import json
import logging
import shutil
import uuid as uuid_mod
import zipfile
from collections.abc import Iterator

import redis.asyncio as aioredis
import sentry_sdk
from agent.jobspec import JobSpec
from auth.dependencies import get_current_user
from auth.org_dependencies import require_role
from billing.stripe_client import check_payment_method, get_or_create_customer
from config import settings
from db.connection import get_db_pool
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from middleware.rate_limit import limiter
from pydantic import BaseModel
from storage.client import (
    DESIGN_SUFFIXES,
    generate_presigned_get_url,
    generate_presigned_put_url,
    get_s3_client,
    list_job_design_keys,
)

from jobs.dispatch import launch_job
from jobs.service import TOOL_IMAGES, cancel_job_by_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_SSE_PER_USER = settings.max_sse_connections_per_user

#: Terminal states whose designs this module serves.
#:
#: ``complete`` reads them from ``job_candidates``. The other two have no row
#: there -- the webhook writes candidates only under its ``complete`` branch
#: (``webhooks/router.py``) -- but whatever the run uploaded before it stopped
#: is in Storage, so both serve a partial set listed from the bucket.
#:
#: ``cancelled`` is here because a cancel bills the GPU seconds already burned
#: (``jobs/service.py::cancel_job_by_id``, the ``if claimed and gpu_seconds > 0``
#: block at jobs/service.py:184),
#: and taking the money while the output stays unreachable is the same defect
#: as on the failed path.
_DELIVERS_DESIGNS: tuple[str, ...] = ("complete", "failed", "cancelled")


class _ChunkSink:
    """Collects what ``ZipFile`` writes so a generator can hand it onward.

    ``ZipFile`` needs only ``write`` and ``flush`` from its output. Finding no
    ``tell``, it wraps this in its own ``_Tellable`` and writes data
    descriptors instead of seeking back to patch each entry's header, so the
    archive never has to be seekable -- and so never has to be held whole.
    That what comes out of that path still opens is pinned by
    ``tests/jobs/test_partial_delivery.py::test_the_archive_is_served_as_it_is_built``.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        """Everything written since the last drain, which it hands over."""
        joined = b"".join(self._chunks)
        self._chunks.clear()
        return joined



async def _check_sse_limit(user_id: str) -> None:
    """Check if user has exceeded max concurrent SSE connections.

    Uses a Redis key with TTL to track active SSE connections.
    Raises HTTPException 429 if limit exceeded.
    """
    r = aioredis.from_url(settings.redis_url)
    try:
        key = f"sse_count:{user_id}"
        count = await r.incr(key)
        await r.expire(key, 300)  # Auto-expire after 5 min (safety net)
        if count > MAX_SSE_PER_USER:
            await r.decr(key)
            raise HTTPException(status_code=429, detail="Too many active connections")
    finally:
        await r.aclose()


async def _release_sse_slot(user_id: str) -> None:
    """Decrement the SSE connection counter when a stream closes."""
    r = aioredis.from_url(settings.redis_url)
    key = f"sse_count:{user_id}"
    await r.decr(key)
    await r.aclose()


# Map tool name to RunPod image from settings (pod-based deployment).
# Imported from jobs.service so admin/router.py can share the same dict.
_TOOL_IMAGES = TOOL_IMAGES


# ---------------------------------------------------------------------------
# Launch endpoint — payment gate + dispatch
# ---------------------------------------------------------------------------

class LaunchRequest(BaseModel):
    """Request body for POST /jobs/launch.

    Phase 2 additions:
        job_tier: ``"pilot"`` (default) or ``"full_design"``. Pilot runs clamp
            parameters to a small validation preset; full-design requires that
            the user has already completed a successful pilot on the same tool.
        total_budget_hours: GPU hours cap (1-96). Only meaningful for
            ``full_design``. Ignored for pilot. Defaults to 4 if absent.
    """

    job_id: str
    job_name: str | None = None
    job_tier: str = "pilot"
    total_budget_hours: int = 4


class EstimateRequest(BaseModel):
    """Request body for POST /jobs/estimate.

    Returns predicted ``(seconds, dollars)`` for a hypothetical job before
    the user submits. Used by the frontend submit form to render the
    pilot-vs-full-design cost comparison.
    """

    tool: str
    job_tier: str = "pilot"
    total_budget_hours: int = 4
    parameters: dict = {}


@router.post("/launch")
@limiter.limit("5/minute")
async def launch_job_endpoint(
    request: Request,
    body: LaunchRequest,
    user_id: str = Depends(get_current_user),
    org_id: str = Depends(require_role("owner", "scientist")),
):
    """BILL-04 / JOB-01: Payment gate check then job dispatch.

    Phase 12: org-scoped via require_role("owner", "scientist"). Viewers cannot
    launch jobs. Stripe customer is resolved through the active organization,
    not the calling user. Audit field ``created_by_user_id`` records who
    clicked Launch.

    Args:
        body.job_id: UUID of an existing job row (created by the agent wizard).
        user_id: Injected by the auth dependency.
        org_id: Injected by require_role — the active organization.

    Returns:
        JSON with job_id and status="queued" on success.

    Raises:
        HTTPException 402: If the org has no payment method configured.
        HTTPException 403: If the user is a viewer (handled by require_role).
        HTTPException 404: If the job row does not exist in this org.
    """
    # Validate job_id is a valid UUID format.
    try:
        uuid_mod.UUID(body.job_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid job_id format — must be a valid UUID")

    pool = await get_db_pool()

    # Fetch job row scoped to the active org and get job_spec.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT job_spec FROM public.jobs WHERE id = $1 AND organization_id = $2",
            body.job_id,
            org_id,
        )
    if not row:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Resolve Stripe customer scoped to the org and check payment method (skip if Stripe not configured).
    if settings.stripe_secret_key:
        async with pool.acquire() as conn:
            org_row = await conn.fetchrow(
                "SELECT id, name FROM public.organizations WHERE id = $1", org_id,
            )
            if org_row is None:
                raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Organization not found")
            owner_row = await conn.fetchrow(
                """SELECT u.email FROM public.organization_memberships m
                   JOIN public.users u ON u.id = m.user_id
                   WHERE m.organization_id = $1 AND m.role = 'owner'
                   ORDER BY m.created_at ASC LIMIT 1""",
                org_id,
            )
        if owner_row is None:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Organization has no owner; cannot resolve billing contact",
            )

        stripe_customer_id = await get_or_create_customer(
            email=owner_row["email"],
            org_id=str(org_row["id"]),
            org_name=org_row["name"],
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

    # Validate job_tier + total_budget_hours.
    if body.job_tier not in ("pilot", "full_design"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid job_tier {body.job_tier!r}. Must be 'pilot' or 'full_design'.",
        )
    if not (1 <= body.total_budget_hours <= 96):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="total_budget_hours must be between 1 and 96 (hard cap).",
        )

    # Parse job_spec to get the tool for tier gating + tier-aware dispatch.
    spec_data = json.loads(row["job_spec"] or "{}")
    spec_data["job_tier"] = body.job_tier  # Stamp tier into the spec for pipeline.generate_config.
    tool = spec_data.get("tool", "")

    # Gate: full_design requires at least one previously-completed pilot on the
    # same tool within this organization. Org-scoped: any completed pilot in
    # the org qualifies any org member to launch a full-design (RESEARCH §13).
    if body.job_tier == "full_design":
        async with pool.acquire() as conn:
            pilot_row = await conn.fetchrow(
                """
                SELECT 1 FROM public.jobs
                WHERE organization_id = $1
                  AND job_tier = 'pilot'
                  AND status = 'complete'
                  AND job_spec::jsonb ->> 'tool' = $2
                LIMIT 1
                """,
                org_id,
                tool,
            )
        if not pilot_row:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Full-design submission for {tool!r} requires a successfully "
                    "completed pilot on that tool first. Run a pilot, then come back."
                ),
            )

    job_spec = JobSpec(**{k: v for k, v in spec_data.items() if k != "job_tier"})
    await launch_job(
        job_id=body.job_id,
        job_spec=job_spec,
        user_id=user_id,
        organization_id=org_id,
        created_by_user_id=user_id,
        pool=pool,
        job_tier=body.job_tier,
        total_budget_hours=body.total_budget_hours,
    )

    return {
        "job_id": body.job_id,
        "status": "queued",
        "job_tier": body.job_tier,
        "total_budget_hours": body.total_budget_hours,
    }


@router.post("/estimate")
@limiter.limit("30/minute")
async def estimate_job_endpoint(
    request: Request,
    body: EstimateRequest,
    user_id: str = Depends(get_current_user),
):
    """Return predicted runtime + cost for a hypothetical job.

    Used by the frontend submit form to show pilot-vs-full cost comparison
    before the user commits. Does not touch the DB or provider.

    Returns:
        ``{"seconds": int, "dollars": float, "gpu_sku": str, "pilot_clamped": bool}``.
    """
    from pipelines import PIPELINE_MAP

    pipeline = PIPELINE_MAP.get(body.tool)
    if not pipeline:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown tool {body.tool!r}. Valid tools: {sorted(PIPELINE_MAP.keys())}",
        )
    if body.job_tier not in ("pilot", "full_design"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid job_tier {body.job_tier!r}.",
        )

    # Build a minimal job_spec for the estimator.
    spec = {
        "tool": body.tool,
        "job_tier": body.job_tier,
        "total_budget_hours": body.total_budget_hours,
        "parameters": body.parameters or {},
    }
    seconds, dollars = pipeline.estimate_cost(spec)

    return {
        "seconds": seconds,
        "dollars": dollars,
        "gpu_sku": pipeline.gpu_sku,
        "pilot_clamped": body.job_tier == "pilot",
    }


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
    org_id: str = Depends(require_role("owner", "scientist", "viewer")),
):
    """Return paginated job history for the active organization.

    Phase 12: org-scoped — every member of the active org sees every org job,
    regardless of who launched it. The ``created_by_user_id`` + ``created_by_email``
    columns surface the launcher in the response so the UI can render
    "launched by Alice".

    Supports keyset pagination via the ``before`` cursor (ISO timestamp) and
    optional status filtering. Results are ordered by created_at descending so
    the newest jobs appear first.

    Args:
        limit: Maximum number of jobs to return (1-100, default 25).
        status: Optional status filter. Accepted values: ``running``,
            ``complete``, ``failed``. Omit for all statuses. Returns 400
            for any other value.
        before: ISO 8601 timestamp cursor. Only jobs created before this
            timestamp are returned, enabling keyset pagination. Returns 400
            if the value cannot be parsed as a timestamp.
        org_id: Injected by require_role — the active organization.

    Returns:
        Dict with ``jobs`` list and ``has_more`` bool. Each job row includes
        ``created_by_user_id`` and ``created_by_email``.

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
                before_dt = before_dt.replace(tzinfo=datetime.UTC)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid 'before' cursor '{before}'. Expected ISO 8601 timestamp.",
            )

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT j.id, j.tool, j.status, j.name, j.created_at, j.completed_at,
                      j.gpu_cost_usd, j.results->>'candidate_count' AS candidate_count,
                      j.session_id, j.created_by_user_id, u.email AS created_by_email
               FROM public.jobs j
               LEFT JOIN public.users u ON u.id = j.created_by_user_id
               WHERE j.organization_id = $1
                 AND ($2::text IS NULL OR j.status = $2)
                 AND ($3::timestamptz IS NULL OR j.created_at < $3)
               ORDER BY j.created_at DESC
               LIMIT $4""",
            org_id,
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
            "created_by_user_id": str(r["created_by_user_id"]) if r["created_by_user_id"] else None,
            "created_by_email": r["created_by_email"],
        }
        for r in rows
    ]

    # has_more is true when exactly `limit` rows were returned — there may be more.
    return {"jobs": jobs, "has_more": len(rows) == limit}


@router.get("/{job_id}/status")
async def job_status_stream(
    job_id: str,
    user_id: str = Depends(get_current_user),
    org_id: str = Depends(require_role("owner", "scientist", "viewer")),
):
    """Stream job status events via Server-Sent Events.

    Phase 12: org-scoped — any member of the active org can stream status for
    any job in that org. SSE limit is still tracked per user_id so a single
    user can't open many concurrent streams across orgs.

    Emits the current state immediately. For non-terminal jobs, subscribes to
    the Redis pub/sub channel and forwards events until a terminal status is
    received, then closes the stream.

    Args:
        job_id: Job UUID string.
        user_id: Injected by the auth dependency.
        org_id: Injected by require_role — the active organization.

    Returns:
        StreamingResponse with media_type="text/event-stream".
    """
    # Enforce per-user SSE connection limit.
    await _check_sse_limit(user_id)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, stage FROM public.jobs WHERE id = $1 AND organization_id = $2",
            job_id,
            org_id,
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
async def download_all_designs(
    request: Request,
    job_id: str,
    user_id: str = Depends(get_current_user),
    org_id: str = Depends(require_role("owner", "scientist", "viewer")),
):
    """Download a job's design outputs as a ZIP archive.

    Serves every state in ``_DELIVERS_DESIGNS``: a complete run, and a failed
    or cancelled one that still delivered designs. A run that stopped part way
    leaves whatever it had already uploaded under the same ``outputs/`` prefix
    this reads -- how much that is depends on the tool
    (``backend/storage/client.py::list_job_design_keys``). A non-complete
    run's archive is named ``_partial`` so the downloaded file says so on
    disk.

    Phase 12: org-scoped — any member of the active org can download outputs
    for any such job in that org. The S3 storage key prefix still uses
    the row's original ``user_id`` (the launcher) since storage paths are
    immutable; we read that from the job row, not from the caller.

    Args:
        job_id: Job UUID string.
        user_id: Injected by the auth dependency.
        org_id: Injected by require_role — the active organization.

    Returns:
        StreamingResponse with media_type="application/zip".

    Raises:
        HTTPException 404: If the job does not exist, is in a state outside
            ``_DELIVERS_DESIGNS``, or is a non-complete run with no design
            file in Storage.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, user_id FROM public.jobs WHERE id = $1 AND organization_id = $2",
            job_id,
            org_id,
        )
    if not row or row["status"] not in _DELIVERS_DESIGNS:
        raise HTTPException(status_code=404, detail="No completed job found")

    # Storage prefix is keyed to the original launcher's user_id, not the caller.
    storage_user_id = str(row["user_id"])
    s3 = get_s3_client()
    prefix = f"users/{storage_user_id}/jobs/{job_id}/outputs/"

    def _list_objects() -> list[dict]:
        # Paginated because a bare list_objects_v2 stops at 1000 keys and
        # num_designs caps far above that -- 20000 for rfantibody
        # (agent/wizard.py:114) and pxdesign (agent/wizard.py:166), 60000 for
        # boltzgen (agent/wizard.py:139), each enforced at
        # agent/tools.py:643 -- so a large run's archive was
        # silently short. It also reads every page list_job_design_keys does,
        # which puts every design GET /jobs/{id} lists in the archive.
        return [
            obj
            for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=settings.s3_bucket_name, Prefix=prefix
            )
            for obj in page.get("Contents") or []
        ]

    contents = await asyncio.to_thread(_list_objects)

    is_partial = row["status"] != "complete"
    if is_partial and not any(
        o["Key"].endswith(DESIGN_SUFFIXES) for o in contents
    ):
        # Nothing was streamed before the run died, so there is no partial set
        # to serve. A complete run with an empty prefix keeps its pre-existing
        # behaviour of an empty archive rather than a 404.
        raise HTTPException(status_code=404, detail="No designs found for this job")

    def _stream_archive() -> Iterator[bytes]:
        # Streamed rather than built in memory because paginating the listing
        # above removed the implicit 1000-key cap that used to bound this, and
        # num_designs allows far more (agent/wizard.py:114, agent/wizard.py:166
        # and agent/wizard.py:139): a
        # whole archive held in a BytesIO makes one download of a large run a
        # process-wide memory event. Each object arrives 64KiB at a time and
        # the sink is drained after every entry, so what is resident is one
        # design's compressed bytes and a read buffer rather than the archive
        # (tests/jobs/test_partial_delivery.py::test_the_archive_is_served_as_it_is_built).
        #
        # Starlette hands a sync iterator to iterate_in_threadpool
        # (starlette/responses.py:236 in starlette 1.3.1, the non-AsyncIterable
        # branch), so the blocking boto3 calls stay off the event loop as
        # they did when this was one asyncio.to_thread hop.
        sink = _ChunkSink()
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for obj in contents:
                with zf.open(obj["Key"].split("/")[-1], "w") as entry:
                    try:
                        shutil.copyfileobj(
                            s3.get_object(Bucket=settings.s3_bucket_name, Key=obj["Key"])["Body"],
                            entry,
                            65536,
                        )
                    except Exception as exc:
                        # The 200 and its headers went out before this
                        # generator was first pulled (Starlette sends
                        # `http.response.start`, then iterates:
                        # starlette/responses.py:249 at 1.3.1), so a fault cannot
                        # become a 500. The client keeps the bytes it already
                        # has and the archive ends with no central directory
                        # -- a file that will not open. Re-raised rather than
                        # swallowed, because a short archive must not look
                        # finished; captured first so the event names the job
                        # and the key, which a generic handler further up the
                        # stack would not.
                        logger.exception(
                            "Archive stream for job %s failed on key %s; the "
                            "client keeps a truncated zip",
                            job_id,
                            obj["Key"],
                        )
                        sentry_sdk.capture_exception(exc)
                        raise
                if chunk := sink.drain():
                    yield chunk
        if chunk := sink.drain():  # the central directory zipfile writes on close
            yield chunk

    suffix = "designs_partial" if is_partial else "designs"
    return StreamingResponse(
        _stream_archive(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="job_{job_id}_{suffix}.zip"'},
    )


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    org_id: str = Depends(require_role("owner", "scientist")),
):
    """Cancel a running job.

    Phase 12: org-scoped via require_role("owner", "scientist"). Viewers cannot
    cancel. Owners and scientists can cancel any running or queued job in the
    active org, regardless of who launched it.

    Verifies the job belongs to the active org, then delegates to the shared
    cancel_job_by_id service which handles RunPod cancellation, partial billing,
    DB update, and SSE event publication.

    Args:
        job_id: Job UUID string.
        org_id: Injected by require_role — the active organization.

    Returns:
        Dict with status, gpu_seconds consumed, and gpu_cost_usd charged.

    Raises:
        HTTPException 404: If no running or queued job is found in this org.
    """
    pool = await get_db_pool()

    # Org-scoped check — only org members can cancel; viewer blocked by require_role.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id FROM public.jobs
               WHERE id = $1 AND organization_id = $2 AND status IN ('running', 'queued')""",
            job_id,
            org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="No running job found")

    # Delegate business logic (RunPod cancel + billing + DB + SSE) to shared service.
    result = await cancel_job_by_id(job_id, pool)

    return {"status": result["status"], "gpu_seconds": result["gpu_seconds"], "gpu_cost_usd": result["gpu_cost_usd"]}


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
async def get_job(
    job_id: str,
    org_id: str = Depends(require_role("owner", "scientist", "viewer")),
):
    """Return full job data including candidates with presigned download URLs.

    Phase 12: org-scoped — any member of the active org can read any job in
    the org (jobs.organization_id is the access key, not user_id).

    Args:
        job_id: Job UUID string.
        org_id: Injected by require_role — the active organization.

    Returns:
        Dict with all job fields and a candidates list (empty if not complete).

    Raises:
        HTTPException 404: If the job does not exist in this org.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.jobs WHERE id = $1 AND organization_id = $2",
            job_id,
            org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    candidates = []
    partial = False
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
    elif row["status"] in _DELIVERS_DESIGNS:
        # A run that did not complete has no job_candidates rows to read. The
        # webhook collects a payload's candidates only inside ``if
        # internal_status == "complete"`` (``backend/webhooks/router.py:230``)
        # and writes them only below its claim on the row (the claim at
        # ``backend/webhooks/router.py:278``, the INSERT at
        # ``backend/webhooks/router.py:289``), so neither a FAILED post nor a
        # COMPLETED one that lost the claim leaves any behind;
        # the reaper that fails a timed-out run writes none
        # (``backend/worker/cleanup.py::detect_stale_jobs``); and a cancel
        # writes none (``jobs/service.py::cancel_job_by_id``).
        # Whatever the run uploaded before it stopped is still in Storage, so
        # Storage is the only record of it and this lists it directly.
        #
        # No scores: per-design metrics live in job_candidates.scores, and that
        # table is written only on the complete path (webhooks/router.py:230,
        # the INSERT at webhooks/router.py:289), so there is nothing to read
        # for this set. ``rank`` is the key
        # order from list_job_design_keys -- a presentation order, not a
        # quality ranking.
        def _list_and_sign() -> list[dict]:
            # Signed in here rather than in a comprehension on the event
            # loop because nothing in backend/ bounds this set. The complete
            # branch above signs the job_candidates rows a container chose
            # to report; this one signs whatever Storage holds under the
            # job's prefix, and every cap on that lives in a container's own
            # filter, under docker/, which no module in backend/ imports
            # (docker/pxdesign/run_pipeline.py:1867 and
            # docker/boltzgen/run_pipeline.py:2021 each upload a filtered
            # `passing` set, and boltzgen's size comes from its own budget
            # default, 60 outside the pilot tier and 5 inside it,
            # docker/boltzgen/run_pipeline.py:1850).
            # generate_presigned_get_url builds a fresh boto3 client per
            # call unless it is handed one (storage/client.py:62), so every key
            # in this unbounded set is signed on the one client built here
            # (tests/jobs/test_partial_delivery.py::test_the_partial_set_signs_on_one_client).
            # Signing is local HMAC, so no key costs a network hop, and the
            # thread keeps even that off the one event loop. This runs on every
            # view of a terminal job -- JobPage fetches it on mount -- so the
            # per-call client was paid again on every refresh.
            keys = list_job_design_keys(str(row["user_id"]), job_id)
            s3 = get_s3_client()
            return [
                {
                    "rank": i,
                    "pdb_key": k,
                    "scores": {},
                    "download_url": generate_presigned_get_url(k, client=s3),
                }
                for i, k in enumerate(keys, start=1)
            ]

        try:
            candidates = await asyncio.to_thread(_list_and_sign)
        except Exception as exc:
            # A Storage fault, listing or signing, degrades to the old empty
            # list rather than failing the whole job read, which also serves
            # the status and the error category the caller came for
            # (tests/jobs/test_partial_delivery.py::test_a_signing_failure_degrades_the_same_way_as_an_unreadable_bucket).
            # Captured explicitly because swallowing here stops Sentry's
            # FastApiIntegration from seeing it -- the reasoning spelled out
            # at webhooks/router.py:329-332.
            logger.exception("Could not list saved designs for job %s", job_id)
            sentry_sdk.capture_exception(exc)
            candidates = []
        partial = bool(candidates)

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
        # True only on a run that failed or was cancelled and still delivered
        # designs, so a caller can tell "these are all of them" from "these are
        # the ones that survived". Always False for a complete run.
        "partial": partial,
        "job_spec": json.loads(row["job_spec"]) if row["job_spec"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
