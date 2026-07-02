"""Public /api/v1/jobs surface — the headline endpoints for the SDK (Phase 13).

Four endpoints:
  POST /api/v1/jobs               — submit (Idempotency-Key required, 3-state)
  GET  /api/v1/jobs               — cursor-paginated org-scoped list + filters
  GET  /api/v1/jobs/{job_id}      — inline candidates + 24h presigned URLs
  POST /api/v1/jobs/{job_id}/cancel — org-scoped ownership check + delegate

Auth: ``require_role_api(...)`` returns the caller's ``org_id`` and (via
``get_current_api_key``) writes ``request.state.api_key_id`` so ``api_v1_limiter``
keys the 60/min bucket per API key. RFC 7807 problem+json envelopes come from the
app-level handlers registered in main.py (api.v1.errors).
"""

import datetime
import json
import uuid as uuid_mod

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status as http_status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.jobspec import JobSpec
from api.v1.cursor import decode_cursor, encode_cursor
from api.v1.idempotency import hash_body, mark_complete, try_begin
from auth.api_key_dependencies import require_role_api
from config import settings
from db.connection import get_db_pool
from jobs.dispatch import launch_job
from jobs.serialize import serialize_job_with_candidates
from jobs.service import cancel_job_by_id
from middleware.rate_limit import api_v1_limiter

router = APIRouter(prefix="/jobs", tags=["api_v1_jobs"])

_ROLES = ("owner", "admin", "member")


class SubmitJobRequest(BaseModel):
    tool: str
    parameters: dict
    name: str | None = None


def _build_job_spec(body: SubmitJobRequest) -> JobSpec:
    """Assemble a JobSpec from the SDK submit body.

    The SDK contract body is ``{tool, parameters}``; the rest of the JobSpec
    fields (target/chain/hotspots/validation) are read from ``parameters`` with
    safe defaults so the dispatch worker + Modal session orchestrator get a
    well-formed spec.
    """
    params = body.parameters or {}
    return JobSpec(
        tool=body.tool,
        target_pdb_path=params.get("target_pdb_path", ""),
        target_chain=params.get("target_chain", ""),
        hotspot_residues=params.get("hotspot_residues", []),
        parameters=params,
        validation_results=[],
        estimated_cost_usd=float(params.get("estimated_cost_usd", 0) or 0),
        rationale=params.get("rationale", ""),
    )


@router.post("/", status_code=201)
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def submit_job(
    request: Request,
    body: SubmitJobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    org_id: str = Depends(require_role_api(*_ROLES)),
):
    """POST /api/v1/jobs — submit a job with Stripe-style idempotency (API-04)."""
    # (a) Idempotency-Key is required.
    if idempotency_key is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )

    # (b) api_key_id was written by get_current_api_key (Plan 13-02 dep). We only
    #     read it here; the dep is the single source of truth for that write.
    api_key_id = request.state.api_key_id

    # (c) Canonical body hash for the idempotency conflict check.
    body_dict = body.model_dump()
    body_hash = hash_body(body_dict)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await try_begin(conn, api_key_id, idempotency_key, body_hash)

            # (d) Branch on the idempotency state.
            if existing is not None:
                if existing["status"] == "pending":
                    exc = HTTPException(
                        status_code=http_status.HTTP_409_CONFLICT,
                        detail="Request is still being processed. Retry with the same idempotency key after a few seconds.",
                    )
                    exc.headers = {"X-Bindwave-Problem-Type": "idempotency-in-progress"}
                    raise exc
                if existing["request_body_hash"] != body_hash:
                    exc = HTTPException(
                        status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Idempotency key reused with different request body",
                    )
                    exc.headers = {"X-Bindwave-Problem-Type": "idempotency-key-conflict"}
                    raise exc
                # Completed match -> replay stored response byte-for-byte.
                # asyncpg returns JSONB as a str by default (no codec registered);
                # decode it back to the object so JSONResponse does not double-encode.
                stored_body = existing["response_body"]
                if isinstance(stored_body, str):
                    stored_body = json.loads(stored_body)
                return JSONResponse(
                    stored_body,
                    status_code=existing["response_status"],
                    headers={"X-Idempotency-Replay": "1"},
                )

            # (e) Proceed: create the job row + co-transactional dispatch.
            new_job_id = str(uuid_mod.uuid4())
            # Resolve the key creator so the NOT NULL user_id / created_by_user_id
            # columns are satisfied; the row is org-scoped for all v1 queries.
            key_row = await conn.fetchrow(
                "SELECT created_by_user_id FROM public.api_keys WHERE id = $1",
                api_key_id,
            )
            creator_user_id = str(key_row["created_by_user_id"]) if key_row else None
            await conn.execute(
                """INSERT INTO public.jobs
                       (id, user_id, organization_id, created_by_user_id,
                        tool, status, name, created_at)
                   VALUES ($1, $2::uuid, $3::uuid, $2::uuid, $4, 'pending', $5, NOW())""",
                new_job_id,
                creator_user_id,
                org_id,
                body.tool,
                body.name,
            )

            await launch_job(
                job_id=new_job_id,
                job_spec=_build_job_spec(body),
                user_id=None,
                pool=pool,
                conn=conn,
                organization_id=org_id,
            )

            # (f) Persist the response atomically with the job row.
            response = {
                "id": new_job_id,
                "status": "queued",
                "tool": body.tool,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            await mark_complete(conn, api_key_id, idempotency_key, 201, response)

    # (g) Return outside the transaction context — the row is committed.
    return JSONResponse(response, status_code=201)


@router.get("/")
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def list_jobs(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    tool: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    org_id: str = Depends(require_role_api(*_ROLES)),
):
    """GET /api/v1/jobs — cursor-paginated, org-scoped list with filters (API-05)."""
    # (a) Decode the opaque cursor; garbage -> 400.
    cursor_tuple = decode_cursor(cursor) if cursor else None
    if cursor and cursor_tuple is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
        )
    cursor_created_at = cursor_tuple[0] if cursor_tuple else None
    cursor_id = cursor_tuple[1] if cursor_tuple else None

    def _parse_ts(value: str | None) -> datetime.datetime | None:
        if value is None:
            return None
        try:
            dt = datetime.datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid timestamp '{value}'. Expected ISO 8601.",
            )

    after_dt = _parse_ts(created_after)
    before_dt = _parse_ts(created_before)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, tool, status, name, created_at, completed_at,
                      gpu_cost_usd, organization_id
               FROM public.jobs
               WHERE organization_id = $1
                 AND ($2::text IS NULL OR status = $2)
                 AND ($3::text IS NULL OR tool = $3)
                 AND ($4::timestamptz IS NULL OR created_at > $4)
                 AND ($5::timestamptz IS NULL OR created_at < $5)
                 AND ($6::timestamptz IS NULL OR (created_at, id) < ($6, $7::uuid))
               ORDER BY created_at DESC, id DESC
               LIMIT $8""",
            org_id,
            status_filter,
            tool,
            after_dt,
            before_dt,
            cursor_created_at,
            cursor_id,
            limit,
        )

    # (c) Compute next_cursor only when the page is full.
    next_cursor = None
    if len(rows) == limit and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last["created_at"], str(last["id"]))

    # (d) Serialize each row.
    data = [
        {
            "id": str(r["id"]),
            "tool": r["tool"],
            "status": r["status"],
            "name": r["name"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "gpu_cost_usd": float(r["gpu_cost_usd"]) if r["gpu_cost_usd"] else None,
            "organization_id": str(r["organization_id"]) if r["organization_id"] else None,
        }
        for r in rows
    ]
    return {"data": data, "next_cursor": next_cursor}


@router.get("/{job_id}")
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def get_job(
    request: Request,
    job_id: str,
    org_id: str = Depends(require_role_api(*_ROLES)),
):
    """GET /api/v1/jobs/{job_id} — inline candidates + 24h presigned URLs (API-06)."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT organization_id FROM public.jobs WHERE id = $1",
            job_id,
        )
    # 404 (not 403) on cross-org access to avoid existence disclosure (Phase 5 SC 3).
    if not row or str(row["organization_id"]) != str(org_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")

    result = await serialize_job_with_candidates(job_id, pool, expires_in=86400)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")
    return result


@router.post("/{job_id}/cancel")
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def cancel_job(
    request: Request,
    job_id: str,
    org_id: str = Depends(require_role_api(*_ROLES)),
):
    """POST /api/v1/jobs/{job_id}/cancel — org-scoped cancel (API-07)."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id FROM public.jobs
               WHERE id = $1 AND organization_id = $2 AND status IN ('running', 'queued')""",
            job_id,
            org_id,
        )
    if not row:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="No running job found")

    result = await cancel_job_by_id(job_id, pool)
    return {"id": job_id, "status": result["status"]}
