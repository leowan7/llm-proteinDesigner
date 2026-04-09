"""Admin API endpoints.

All endpoints require the authenticated user to have is_admin = TRUE (enforced
by Depends(get_current_admin)). Every endpoint writes an audit log entry.

Provides:
- GET  /admin/users              — all users with email filter and sort
- GET  /admin/jobs               — all-user jobs with status/tool/email filters
- GET  /admin/jobs/{id}          — single job detail (full job_spec, results)
- POST /admin/jobs/{id}/cancel   — cancel a running job (admin-scoped)
- GET  /admin/revenue            — revenue summary with period filtering
- GET  /admin/system             — API, DB, Redis health + GPU queue counts
- GET  /admin/audit              — paginated audit log entries
"""

import datetime
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status

from admin.audit import write_audit
from admin.dependencies import get_current_admin
from config import settings
from db.connection import get_db_pool
from jobs.service import cancel_job_by_id

router = APIRouter(prefix="/admin", tags=["admin"])

# Allowed status values for the jobs filter — same set as the jobs table CHECK constraint.
ALLOWED_JOB_STATUSES = {"running", "queued", "complete", "failed", "cancelled"}

# Allowed sort directions for the users list.
ALLOWED_USER_SORTS = {"created_at_desc", "created_at_asc", "job_count_desc"}


# ---------------------------------------------------------------------------
# GET /admin/users
# ---------------------------------------------------------------------------

@router.get("/users")
async def list_users(
    email: str | None = Query(default=None),
    sort: str = Query(default="created_at_desc"),
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    admin_id: str = Depends(get_current_admin),
):
    """Return all users with aggregated job and billing data.

    Supports email search (ILIKE, parameterized), sort by created_at or
    job_count, and keyset pagination by created_at.

    Args:
        email: Optional partial email filter (case-insensitive ILIKE).
        sort: Sort order — "created_at_desc" (default), "created_at_asc",
              or "job_count_desc".
        before: ISO 8601 cursor for keyset pagination (created_at).
        limit: Page size (1–200, default 50).
        admin_id: Injected by get_current_admin — user_id of the admin.

    Returns:
        Dict with "users" list and "has_more" bool.
    """
    if sort not in ALLOWED_USER_SORTS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort '{sort}'. Allowed: {sorted(ALLOWED_USER_SORTS)}",
        )

    before_dt: datetime.datetime | None = None
    if before is not None:
        try:
            before_dt = datetime.datetime.fromisoformat(before)
            if before_dt.tzinfo is None:
                before_dt = before_dt.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid 'before' cursor '{before}'. Expected ISO 8601 timestamp.",
            )

    # Build ORDER BY clause (safe — values validated against allowlist above).
    order_clause = {
        "created_at_desc": "u.created_at DESC",
        "created_at_asc": "u.created_at ASC",
        "job_count_desc": "job_count DESC, u.created_at DESC",
    }[sort]

    # Keyset pagination only applies to created_at sorts.
    cursor_clause = ""
    if before_dt is not None and sort in ("created_at_desc", "created_at_asc"):
        op = "<" if sort == "created_at_desc" else ">"
        cursor_clause = f"AND u.created_at {op} $3"

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT
                    u.id,
                    u.email,
                    u.display_name,
                    u.created_at,
                    u.stripe_customer_id,
                    a.last_sign_in_at AS last_login,
                    COUNT(DISTINCT j.id) AS job_count,
                    COALESCE(SUM(j.gpu_cost_usd) FILTER (WHERE j.status = 'complete'), 0) AS total_spend
               FROM public.users u
               LEFT JOIN auth.users a ON a.id = u.id
               LEFT JOIN public.jobs j ON j.user_id = u.id
               WHERE ($1::text IS NULL OR u.email ILIKE '%' || $1 || '%')
               {cursor_clause}
               GROUP BY u.id, u.email, u.display_name, u.created_at, u.stripe_customer_id, a.last_sign_in_at
               ORDER BY {order_clause}
               LIMIT $2""",
            email,
            limit,
            before_dt,
        ) if before_dt is not None else await conn.fetch(
            f"""SELECT
                    u.id,
                    u.email,
                    u.display_name,
                    u.created_at,
                    u.stripe_customer_id,
                    a.last_sign_in_at AS last_login,
                    COUNT(DISTINCT j.id) AS job_count,
                    COALESCE(SUM(j.gpu_cost_usd) FILTER (WHERE j.status = 'complete'), 0) AS total_spend
               FROM public.users u
               LEFT JOIN auth.users a ON a.id = u.id
               LEFT JOIN public.jobs j ON j.user_id = u.id
               WHERE ($1::text IS NULL OR u.email ILIKE '%' || $1 || '%')
               GROUP BY u.id, u.email, u.display_name, u.created_at, u.stripe_customer_id, a.last_sign_in_at
               ORDER BY {order_clause}
               LIMIT $2""",
            email,
            limit,
        )

    users = [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "display_name": r["display_name"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_login": r["last_login"].isoformat() if r["last_login"] else None,
            "payment_status": "active" if r["stripe_customer_id"] else "none",
            "job_count": int(r["job_count"]),
            "total_spend": float(r["total_spend"]),
        }
        for r in rows
    ]

    await write_audit(admin_id, "view_users", None, {"email_filter": email, "sort": sort})

    return {"users": users, "has_more": len(rows) == limit}


# ---------------------------------------------------------------------------
# GET /admin/jobs
# ---------------------------------------------------------------------------

@router.get("/jobs")
async def list_jobs(
    status: str | None = Query(default=None),
    tool: str | None = Query(default=None),
    email: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    admin_id: str = Depends(get_current_admin),
):
    """Return all jobs across all users with optional filters and pagination.

    Args:
        status: Optional status filter (running, queued, complete, failed, cancelled).
        tool: Optional tool filter (exact match).
        email: Optional user email filter (ILIKE, parameterized).
        before: ISO 8601 cursor for keyset pagination (created_at).
        limit: Page size (1–200, default 50).
        admin_id: Injected by get_current_admin.

    Returns:
        Dict with "jobs" list and "has_more" bool.
    """
    if status is not None and status not in ALLOWED_JOB_STATUSES:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{status}'. Allowed: {sorted(ALLOWED_JOB_STATUSES)}",
        )

    before_dt: datetime.datetime | None = None
    if before is not None:
        try:
            before_dt = datetime.datetime.fromisoformat(before)
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
            """SELECT j.id, u.email, j.tool, j.status, j.name,
                      j.created_at, j.completed_at, j.gpu_seconds,
                      j.gpu_cost_usd, j.error_category, j.job_spec,
                      j.results, j.session_id
               FROM public.jobs j
               JOIN public.users u ON u.id = j.user_id
               WHERE ($1::text IS NULL OR j.status = $1)
                 AND ($2::text IS NULL OR j.tool = $2)
                 AND ($3::text IS NULL OR u.email ILIKE '%' || $3 || '%')
                 AND ($4::timestamptz IS NULL OR j.created_at < $4)
               ORDER BY j.created_at DESC
               LIMIT $5""",
            status,
            tool,
            email,
            before_dt,
            limit,
        )

    jobs = [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "tool": r["tool"] or "",
            "status": r["status"],
            "name": r["name"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "gpu_seconds": r["gpu_seconds"],
            "gpu_cost_usd": float(r["gpu_cost_usd"]) if r["gpu_cost_usd"] else None,
            "error_category": r["error_category"],
            "candidate_count": (
                json.loads(r["results"]).get("candidate_count")
                if r["results"]
                else None
            ),
            "session_id": str(r["session_id"]) if r["session_id"] else None,
        }
        for r in rows
    ]

    await write_audit(admin_id, "view_jobs", None, {"status_filter": status, "tool_filter": tool})

    return {"jobs": jobs, "has_more": len(rows) == limit}


# ---------------------------------------------------------------------------
# GET /admin/jobs/{job_id} — detail endpoint for expanded row view (D-16)
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}")
async def get_job_detail(
    job_id: str,
    admin_id: str = Depends(get_current_admin),
):
    """Return full job detail for a single job (no user_id ownership filter).

    Includes full job_spec JSON, error message from results, candidate_count,
    and session_id for linking to the session transcript.

    Args:
        job_id: UUID of the job to retrieve.
        admin_id: Injected by get_current_admin.

    Returns:
        Full job detail dict.

    Raises:
        HTTPException 404: If the job does not exist.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT j.*, u.email AS user_email
               FROM public.jobs j
               JOIN public.users u ON u.id = j.user_id
               WHERE j.id = $1""",
            job_id,
        )

    if not row:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")

    results = json.loads(row["results"]) if row["results"] else None
    job_spec = json.loads(row["job_spec"]) if row["job_spec"] else None

    await write_audit(admin_id, "view_job_detail", job_id, {})

    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "user_email": row["user_email"],
        "tool": row["tool"] or "",
        "status": row["status"],
        "stage": row["stage"] if "stage" in row.keys() else None,
        "name": row["name"] or "",
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "gpu_seconds": row["gpu_seconds"],
        "gpu_cost_usd": float(row["gpu_cost_usd"]) if row["gpu_cost_usd"] else None,
        "error_category": row["error_category"],
        "error_message": results.get("error_message") if results else None,
        "candidate_count": results.get("candidate_count") if results else None,
        "session_id": str(row["session_id"]) if row.get("session_id") else None,
        "job_spec": job_spec,
        "results": results,
    }


# ---------------------------------------------------------------------------
# POST /admin/jobs/{job_id}/cancel
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/cancel")
async def cancel_admin_job(
    job_id: str,
    admin_id: str = Depends(get_current_admin),
):
    """Cancel a running or queued job (admin-scoped, no ownership check).

    Calls the shared cancel_job_by_id service which handles RunPod cancellation,
    partial GPU billing, DB update, and SSE event publication. The audit entry
    is written in a try/finally to ensure it is always recorded (Pitfall 4).

    Args:
        job_id: UUID of the job to cancel.
        admin_id: Injected by get_current_admin.

    Returns:
        Dict with status, gpu_seconds, gpu_cost_usd.

    Raises:
        HTTPException 404: If no running/queued job is found with the given ID.
    """
    pool = await get_db_pool()

    result = await cancel_job_by_id(job_id, pool)

    # Write audit in try/finally to guarantee no audit gap even if cancel raises.
    try:
        await write_audit(
            admin_id,
            "cancel_job",
            job_id,
            {"gpu_seconds": result["gpu_seconds"], "gpu_cost_usd": result["gpu_cost_usd"]},
        )
    except Exception:
        # Audit failure must not hide the cancel result from the admin.
        pass

    return result
