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
"""

import datetime
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

from auth.dependencies import get_current_user
from config import settings
from db.connection import get_db_pool
from jobs.notifications import send_deletion_scheduled_email
from middleware.rate_limit import limiter  # T-10.04-06: slowapi limiter
from user.export import build_and_deliver_export

router = APIRouter(prefix="/user", tags=["user"])

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


# Plan 10-04 constants
GRACE_PERIOD_DAYS = 30
CONFIRMATION_PHRASE = "DELETE MY ACCOUNT"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/usage")
async def get_usage(user_id: str = Depends(get_current_user)):
    """Return the current calendar-month billing summary for the authenticated user.

    Aggregates completed jobs created on or after the first day of the current
    month (UTC). Returns total spend in USD, job count, and a list of the
    10 most recent completed jobs this month for itemised display.

    Args:
        user_id: Injected by the auth dependency.

    Returns:
        Dict with ``period_start``, ``job_count``, ``total_spend_usd``, and
        ``recent_charges`` list.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Aggregate totals for the current calendar month.
        summary_row = await conn.fetchrow(
            """SELECT COUNT(*) AS job_count,
                      COALESCE(SUM(gpu_cost_usd), 0) AS total_spend,
                      date_trunc('month', now()) AS period_start
               FROM public.jobs
               WHERE user_id = $1
                 AND created_at >= date_trunc('month', now())
                 AND status = 'complete'""",
            user_id,
        )

        # Recent charges for itemised display (last 10 this month).
        charge_rows = await conn.fetch(
            """SELECT id, name, tool, completed_at, gpu_cost_usd
               FROM public.jobs
               WHERE user_id = $1
                 AND created_at >= date_trunc('month', now())
                 AND status = 'complete'
               ORDER BY completed_at DESC
               LIMIT 10""",
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
                      tos_version, data_retention_days
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

    # NOTE: `deletion_requested_at` is intentionally NOT included in this
    # response. Plan 10-04 (GDPR export + deletion, wave 2) owns adding that
    # field to both this payload and the frontend UserSettings interface.
    return {
        "email": row["email"],
        "display_name": row["display_name"] or "",
        "notification_preferences": notification_preferences,
        "is_admin": bool(row["is_admin"]),
        "tos_version": row["tos_version"],
        "tos_current": settings.tos_current_version,
        "data_retention_days": row["data_retention_days"],
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
@limiter.limit("1/hour")  # T-10.04-06 — GDPR Art. 12(5) permits rate-limiting excessive requests
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
    excessive" requests.
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

    Resolves to one of ``none`` (never requested), ``pending`` (requested but
    the background task has not yet written the presigned URL), ``ready`` (URL
    still within its 24hr TTL), or ``expired`` (URL is past its expiry).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT last_export_requested_at, last_export_url, last_export_expires_at
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
        return {
            "status": "ready",
            "url": row["last_export_url"],
            "expires_at": row["last_export_expires_at"].isoformat(),
        }
    if row["last_export_url"] is None:
        # Requested but not yet built (background task still running).
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
    cancel_url = f"{settings.app_base_url}/settings?tab=privacy"
    background_tasks.add_task(
        send_deletion_scheduled_email,
        updated["email"],
        scheduled_for.isoformat(),
        cancel_url,
    )
    return {"deletion_scheduled_for": scheduled_for.isoformat()}


@router.post("/cancel-deletion")
async def cancel_account_deletion(user_id: str = Depends(get_current_user)):
    """Clear a pending ``deletion_requested_at`` — works at any point during the grace period."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT deletion_requested_at FROM public.users WHERE id = $1",
            user_id,
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if row["deletion_requested_at"] is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending deletion",
        )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.users SET deletion_requested_at = NULL, updated_at = now() WHERE id = $1",
            user_id,
        )
    return {"cancelled": True}
