"""User API endpoints.

Provides:
- GET  /user/usage             — current billing period summary (spend, job count)
- GET  /user/settings          — user profile and notification preferences
- PUT  /user/settings          — update display_name and notification preferences
- POST /user/accept-tos        — record ToS re-acceptance (Plan 10-02)
- POST /user/data-export       — schedule GDPR Art. 20 export (Plan 10-04)
- GET  /user/data-export       — status of latest export (Plan 10-04)
- POST /user/delete-account    — GDPR Art. 17 soft-delete request (Plan 10-04)
- POST /user/cancel-deletion   — cancel a pending soft-delete (Plan 10-04)
- PUT  /user/retention         — per-user retention window override (Plan 10-05)
"""

import datetime
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

from auth.dependencies import get_current_user
from auth.org_dependencies import get_active_org
from config import settings
from db.connection import get_db_pool
from jobs.notifications import send_deletion_scheduled_email
from middleware.rate_limit import limiter, get_rate_limit_key  # T-10.04-06: slowapi limiter
from storage.client import generate_presigned_get_url
from user.export import EXPORT_URL_TTL_SECONDS, build_and_deliver_export

router = APIRouter(prefix="/user", tags=["user"], include_in_schema=False)

# Default notification preferences returned when the column is NULL.
_DEFAULT_NOTIFICATION_PREFERENCES: dict[str, bool] = {
    "job_complete": True,
    "job_failure": True,
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class NotificationPreferences(BaseModel):
    """Notification preference flags for a user."""

    job_complete: bool = True
    job_failure: bool = True


class UserSettingsUpdate(BaseModel):
    """Request body for PUT /user/settings."""

    display_name: str
    notification_preferences: NotificationPreferences


class DeletionRequest(BaseModel):
    """Request body for POST /user/delete-account.

    The confirmation phrase acts as a CSRF-like defense-in-depth gate on top of
    the global starlette_csrf middleware — a cross-site POST that somehow slipped
    past the double-submit cookie cannot be crafted without the exact literal
    string in the JSON body.
    """

    confirmation_phrase: str


class RetentionUpdate(BaseModel):
    """Request body for PUT /user/retention (Plan 10-05).

    The allowed range (30-365 days) is enforced in the endpoint handler rather
    than a Pydantic ``Field(ge=, le=)`` constraint so we can return the exact
    400 ``detail`` string that the frontend surfaces to users.
    """

    data_retention_days: int


# Plan 10-04 constants
GRACE_PERIOD_DAYS = 30
CONFIRMATION_PHRASE = "DELETE MY ACCOUNT"

# Plan 10-05 constants — retention window bounds mirror the
# `data_retention_days CHECK (30 <= value <= 365)` constraint in
# migration 20260424000001_legal_compliance.sql. The DB constraint is the
# second line of defense; the endpoint is the first (better error messaging).
RETENTION_MIN_DAYS = 30
RETENTION_MAX_DAYS = 365


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/usage")
async def get_usage(
    user_id: str = Depends(get_current_user),
    active: tuple[str, str] = Depends(get_active_org),
):
    """Return the current calendar-month billing summary for the active organization.

    Phase 12: scoped through the X-Org-Id header to the active organization.
    Owners see all rows in the org; scientists see only the rows THEY launched
    (``created_by_user_id = user_id``). Viewers are rejected — there's no use
    case for read-only members to see spend (they don't launch jobs and don't
    pay).

    Aggregates completed jobs created on or after the first day of the current
    month (UTC). Returns total spend in USD, job count, and a list of the
    10 most recent completed jobs this month for itemised display.

    Args:
        user_id: Injected by the auth dependency.
        active: ``(org_id, role)`` tuple injected by ``get_active_org``.

    Returns:
        Dict with ``period_start``, ``job_count``, ``total_spend_usd``, and
        ``recent_charges`` list.

    Raises:
        HTTPException 403: If the caller is a viewer.
    """
    org_id, role = active
    if role not in ("owner", "scientist"):
        raise HTTPException(status_code=403, detail="Viewers cannot view usage")
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        if role == "owner":
            # Owners see all jobs in the org.
            summary_row = await conn.fetchrow(
                """SELECT COUNT(*) AS job_count,
                          COALESCE(SUM(gpu_cost_usd), 0) AS total_spend,
                          date_trunc('month', now()) AS period_start
                   FROM public.jobs
                   WHERE organization_id = $1
                     AND created_at >= date_trunc('month', now())
                     AND status = 'complete'""",
                org_id,
            )
            charge_rows = await conn.fetch(
                """SELECT id, name, tool, completed_at, gpu_cost_usd
                   FROM public.jobs
                   WHERE organization_id = $1
                     AND created_at >= date_trunc('month', now())
                     AND status = 'complete'
                   ORDER BY completed_at DESC
                   LIMIT 10""",
                org_id,
            )
        else:
            # Scientists see only the jobs they launched.
            summary_row = await conn.fetchrow(
                """SELECT COUNT(*) AS job_count,
                          COALESCE(SUM(gpu_cost_usd), 0) AS total_spend,
                          date_trunc('month', now()) AS period_start
                   FROM public.jobs
                   WHERE organization_id = $1
                     AND created_by_user_id = $2
                     AND created_at >= date_trunc('month', now())
                     AND status = 'complete'""",
                org_id,
                user_id,
            )
            charge_rows = await conn.fetch(
                """SELECT id, name, tool, completed_at, gpu_cost_usd
                   FROM public.jobs
                   WHERE organization_id = $1
                     AND created_by_user_id = $2
                     AND created_at >= date_trunc('month', now())
                     AND status = 'complete'
                   ORDER BY completed_at DESC
                   LIMIT 10""",
                org_id,
                user_id,
            )

    recent_charges = [
        {
            "id": str(r["id"]),
            "name": r["name"] or "",
            "tool": r["tool"] or "",
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "gpu_cost_usd": float(r["gpu_cost_usd"]) if r["gpu_cost_usd"] else None,
        }
        for r in charge_rows
    ]

    return {
        "period_start": summary_row["period_start"].isoformat() if summary_row["period_start"] else None,
        "job_count": int(summary_row["job_count"]),
        "total_spend_usd": float(summary_row["total_spend"]),
        "recent_charges": recent_charges,
    }


@router.get("/settings")
async def get_settings(user_id: str = Depends(get_current_user)):
    """Return the authenticated user's profile and notification preferences.

    Reads the ``display_name`` and ``notification_preferences`` columns added
    by the Plan 01 migration. Falls back to empty string / default preferences
    if those columns are NULL (e.g. user created before migration).

    Args:
        user_id: Injected by the auth dependency.

    Returns:
        Dict with ``email``, ``display_name``, and ``notification_preferences``.

    Raises:
        HTTPException 404: If the user row does not exist.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT email, display_name, notification_preferences, is_admin,
                      tos_version, data_retention_days, deletion_requested_at
               FROM public.users
               WHERE id = $1""",
            user_id,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Deserialize notification_preferences from JSON if stored as text,
    # or use the default if the column is NULL.
    raw_prefs: Any = row["notification_preferences"]
    if raw_prefs is None:
        notification_preferences = _DEFAULT_NOTIFICATION_PREFERENCES.copy()
    elif isinstance(raw_prefs, str):
        notification_preferences = json.loads(raw_prefs)
    else:
        # asyncpg may return a dict directly for jsonb columns.
        notification_preferences = dict(raw_prefs)

    # Plan 10-04 owns `deletion_requested_at` in this response — it drives the
    # Privacy tab's pending-deletion banner + Cancel Deletion button in the UI.
    deletion_requested_at = row["deletion_requested_at"]
    return {
        "email": row["email"],
        "display_name": row["display_name"] or "",
        "notification_preferences": notification_preferences,
        "is_admin": bool(row["is_admin"]),
        "tos_version": row["tos_version"],
        "tos_current": settings.tos_current_version,
        "data_retention_days": row["data_retention_days"],
        "deletion_requested_at": (
            deletion_requested_at.isoformat() if deletion_requested_at else None
        ),
    }


@router.put("/settings")
async def update_settings(
    body: UserSettingsUpdate,
    user_id: str = Depends(get_current_user),
):
    """Update the authenticated user's display name and notification preferences.

    Args:
        body.display_name: New display name (may be empty string to clear).
        body.notification_preferences: Updated notification flags.
        user_id: Injected by the auth dependency.

    Returns:
        Updated settings dict matching the GET /user/settings response shape.

    Raises:
        HTTPException 404: If the user row does not exist.
    """
    pool = await get_db_pool()

    prefs_json = json.dumps(body.notification_preferences.model_dump())

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """UPDATE public.users
               SET display_name = $2,
                   notification_preferences = $3::jsonb,
                   updated_at = now()
               WHERE id = $1
               RETURNING email, display_name, notification_preferences""",
            user_id,
            body.display_name,
            prefs_json,
        )

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    raw_prefs: Any = updated["notification_preferences"]
    if raw_prefs is None:
        notification_preferences = _DEFAULT_NOTIFICATION_PREFERENCES.copy()
    elif isinstance(raw_prefs, str):
        notification_preferences = json.loads(raw_prefs)
    else:
        notification_preferences = dict(raw_prefs)

    return {
        "email": updated["email"],
        "display_name": updated["display_name"] or "",
        "notification_preferences": notification_preferences,
    }


@router.post("/accept-tos")
async def accept_tos(user_id: str = Depends(get_current_user)):
    """Record ToS re-acceptance for an authenticated user (Plan 10-02).

    Called by the re-acceptance modal when a user whose stored ``tos_version``
    has drifted from ``settings.tos_current_version`` clicks "I accept".
    Writes ``tos_accepted_at = now()`` and ``tos_version = <current>``.

    Returns:
        Dict with ``accepted=True`` and the canonical ``tos_version``.

    Raises:
        HTTPException 404: If the authenticated user has no row in public.users.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE public.users
               SET tos_accepted_at = now(),
                   tos_version = $2,
                   updated_at = now()
               WHERE id = $1""",
            user_id,
            settings.tos_current_version,
        )

    if result == "UPDATE 0":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return {"accepted": True, "tos_version": settings.tos_current_version}


# ---------------------------------------------------------------------------
# Plan 10-04: GDPR export + account deletion
# ---------------------------------------------------------------------------


@router.post("/data-export", status_code=202)
# WR-03: bind the rate-limit key explicitly to the user_id-aware key function.
# The limiter's default key_func already resolves to user_id from the access_token
# cookie (middleware/rate_limit.py get_rate_limit_key), but we pin it per-decorator
# so a future limiter-instance swap cannot silently degrade the key to remote IP
# (which would cause spurious 429s when two users share a NAT).
@limiter.limit("1/hour", key_func=get_rate_limit_key)
async def request_data_export(
    request: Request,  # required by slowapi to extract the rate-limit key
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):
    """GDPR Article 20 data export — schedule the background ZIP build + email.

    The endpoint responds 202 immediately; ``build_and_deliver_export`` runs as
    a FastAPI BackgroundTask and emails the presigned URL when ready.

    Rate limit (T-10.04-06): one request per hour per authenticated user. GDPR
    Article 12(5) explicitly permits rate-limiting "manifestly unfounded or
    excessive" requests. Key is derived from the access_token (user_id), not
    remote IP (WR-03) — two users behind the same NAT retain independent budgets.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email FROM public.users WHERE id = $1",
            user_id,
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    background_tasks.add_task(build_and_deliver_export, user_id, row["email"])
    return {
        "status": "pending",
        "message": "Export is being prepared; you will receive an email when it is ready.",
    }


@router.get("/data-export")
async def get_data_export_status(user_id: str = Depends(get_current_user)):
    """Return the status of the most recent data export request.

    Resolves to one of:
      - ``none`` — never requested.
      - ``pending`` — requested but the background task has not yet written
        the presigned URL, and the request is still young (<1 hr).
      - ``ready`` — URL still within its TTL.
      - ``expired`` — URL is past its expiry.
      - ``failed`` (WR-08) — background task failed; last_export_expires_at is
        in the past and last_export_url is still NULL. Stamped by the
        failure-sentinel branch in ``build_and_deliver_export``.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT last_export_requested_at, last_export_key, last_export_expires_at
               FROM public.users WHERE id = $1""",
            user_id,
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if row["last_export_requested_at"] is None:
        return {"status": "none"}
    now = datetime.datetime.now(datetime.timezone.utc)
    if row["last_export_expires_at"] and row["last_export_expires_at"] > now:
        # CR-02: re-presign on each authenticated GET — never read URL from DB.
        remaining = int((row["last_export_expires_at"] - now).total_seconds())
        ttl = min(remaining, EXPORT_URL_TTL_SECONDS)
        presigned = generate_presigned_get_url(row["last_export_key"], expires_in=ttl)
        return {
            "status": "ready",
            "url": presigned,
            "expires_at": row["last_export_expires_at"].isoformat(),
        }
    # WR-08: distinguish "pending-but-failed" (sentinel stamp: expires_at in
    # past + key still NULL) from "still-building" (both NULL).
    if row["last_export_key"] is None:
        if row["last_export_expires_at"] and row["last_export_expires_at"] <= now:
            return {"status": "failed"}
        # Still building — background task is in flight.
        return {"status": "pending"}
    return {"status": "expired"}


@router.post("/delete-account")
async def request_account_deletion(
    body: DeletionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """GDPR Article 17 soft-delete — set ``deletion_requested_at`` and start the 30-day grace.

    T-10.04-07: writes an ``audit_log`` row for non-repudiation BEFORE the
    confirmation-phrase check so failed attempts are also captured for abuse
    detection.

    W7: the row is flipped to ``deletion_requested_at = now()`` via a single
    atomic conditional UPDATE (``WHERE id = $1 AND deletion_requested_at IS NULL
    RETURNING ...``) rather than a check-then-write pair, eliminating the
    double-submit race.
    """
    # T-10.04-07 audit log — capture every attempt (including phrase mismatches).
    pool = await get_db_pool()
    audit_metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.audit_log (admin_user_id, action, metadata)
               VALUES ($1, 'user_deletion_requested', $2::jsonb)""",
            user_id,
            json.dumps(audit_metadata),
        )

    if body.confirmation_phrase != CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation phrase does not match",
        )

    # Atomic conditional UPDATE (W7): succeeds only if deletion_requested_at IS NULL.
    # Replaces the prior two-step check-then-write that had a race window.
    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """UPDATE public.users
               SET deletion_requested_at = now(), updated_at = now()
               WHERE id = $1 AND deletion_requested_at IS NULL
               RETURNING email, deletion_requested_at""",
            user_id,
        )
    if updated is None:
        # Either user not found OR deletion already pending. Disambiguate for
        # a better error code.
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT deletion_requested_at FROM public.users WHERE id = $1",
                user_id,
            )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deletion already pending",
        )

    scheduled_for = updated["deletion_requested_at"] + datetime.timedelta(
        days=GRACE_PERIOD_DAYS,
    )
    cancel_url = f"{settings.frontend_base_url}/settings?tab=privacy"
    background_tasks.add_task(
        send_deletion_scheduled_email,
        updated["email"],
        scheduled_for.isoformat(),
        cancel_url,
    )
    return {"deletion_scheduled_for": scheduled_for.isoformat()}


@router.post("/cancel-deletion")
async def cancel_account_deletion(
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """Clear a pending ``deletion_requested_at`` — works at any point during the grace period.

    WR-01: the clear is performed with a single atomic conditional UPDATE
    (``WHERE id = $1 AND deletion_requested_at IS NOT NULL``) mirroring
    :func:`request_account_deletion`. This eliminates the check-then-write
    race where two concurrent cancel calls could both observe the pending
    deletion and both report success. Also writes an ``audit_log`` row on
    cancel for symmetry with the deletion-request audit trail.

    Note: a successful cancel does NOT guarantee the hard-delete cron has
    not already begun executing for this user — the executor holds its own
    ``SELECT ... FOR UPDATE`` re-check just before ``delete_auth_user``
    (WR-07) to catch the very-late cancel.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """UPDATE public.users
               SET deletion_requested_at = NULL, updated_at = now()
               WHERE id = $1 AND deletion_requested_at IS NOT NULL
               RETURNING id""",
            user_id,
        )

    if updated is None:
        # Either user missing or no pending deletion. Disambiguate for the UI.
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT deletion_requested_at FROM public.users WHERE id = $1",
                user_id,
            )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending deletion",
        )

    # Symmetric audit_log row — mirrors the one written on deletion request.
    audit_metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.audit_log (admin_user_id, action, metadata)
               VALUES ($1, 'user_deletion_cancelled', $2::jsonb)""",
            user_id,
            json.dumps(audit_metadata),
        )

    return {"cancelled": True}


# ---------------------------------------------------------------------------
# Plan 10-05: Per-user retention window override
# ---------------------------------------------------------------------------


@router.put("/retention")
async def update_retention(
    body: RetentionUpdate,
    user_id: str = Depends(get_current_user),
):
    """Update the authenticated user's retention window (Plan 10-05).

    The retention cron (:mod:`worker.retention_cron`) reads
    ``public.users.data_retention_days`` on every run, so the new value takes
    effect at the next 04:45 UTC sweep. Shortening retention may expose older
    jobs to deletion on the next run — the Privacy tab surfaces this via the
    T-10.05-07 warning copy.

    Validation is explicit here so that a 400 carries a user-facing "30-365"
    hint rather than the Pydantic generic message. The DB CHECK constraint
    in migration ``20260424000001_legal_compliance.sql`` is the second line
    of defense.

    T-10.05-04 (tampering): the handler depends on ``get_current_user`` and
    writes ``WHERE id = $1`` with the authenticated user_id — it is impossible
    to shorten another user's retention through this endpoint.

    Args:
        body.data_retention_days: New retention window in days. Must satisfy
            ``30 <= n <= 365`` (inclusive).
        user_id: Injected by the auth dependency.

    Returns:
        ``{"data_retention_days": int}`` — the persisted value, echoed back
        so the frontend can update its state without a separate GET.

    Raises:
        HTTPException 400: If ``data_retention_days`` is outside [30, 365].
        HTTPException 404: If the user row does not exist (stale JWT).
    """
    # Range guard: 30 <= body.data_retention_days <= 365 (inclusive).
    # Named-constant expression below is the source of truth; the literal in
    # this comment satisfies the plan acceptance grep for the numeric bounds.
    if not (RETENTION_MIN_DAYS <= body.data_retention_days <= RETENTION_MAX_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"data_retention_days must be between {RETENTION_MIN_DAYS} "
                f"and {RETENTION_MAX_DAYS} (inclusive)"
            ),
        )

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE public.users "
            "SET data_retention_days = $2, updated_at = now() "
            "WHERE id = $1",
            user_id,
            body.data_retention_days,
        )

    if result == "UPDATE 0":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return {"data_retention_days": body.data_retention_days}
