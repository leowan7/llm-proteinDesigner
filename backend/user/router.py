"""User API endpoints.

Provides:
- GET  /user/usage    — current billing period summary (spend, job count)
- GET  /user/settings — user profile and notification preferences
- PUT  /user/settings — update display_name and notification preferences
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.dependencies import get_current_user
from db.connection import get_db_pool

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
            """SELECT email, display_name, notification_preferences, is_admin
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

    return {
        "email": row["email"],
        "display_name": row["display_name"] or "",
        "notification_preferences": notification_preferences,
        "is_admin": bool(row["is_admin"]),
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
