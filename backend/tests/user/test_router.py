"""Tests for user API endpoints (/user/*).

Covers:
- GET  /user/usage    — billing period summary
- GET  /user/settings — user profile and notification preferences
- PUT  /user/settings — update display_name and notification preferences
- Unauthenticated access → 401

Uses FastAPI dependency_overrides to bypass get_current_user auth and patches
get_db_pool at the user.router module level.
"""
import os

os.environ.setdefault("TESTING", "true")

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auth.dependencies import get_current_user
from httpx import ASGITransport, AsyncClient
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter

_limiter.enabled = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_ID = "test-user-uuid"
NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _mock_user():
    """FastAPI dependency override: returns a fixed user_id string."""
    return USER_ID


def _make_ctx(conn):
    """Wrap a mock asyncpg connection in an async context manager.

    Args:
        conn: The mock asyncpg connection to wrap.

    Returns:
        AsyncMock configured for 'async with pool.acquire() as conn'.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def override_auth():
    """Override get_current_user for every test in this module."""
    app.dependency_overrides[get_current_user] = _mock_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# GET /user/usage
# ---------------------------------------------------------------------------

async def test_get_usage():
    """GET /user/usage returns 200 with period summary fields."""
    summary_row = {
        "job_count": 3,
        "total_spend": 12.50,
        "period_start": NOW,
    }
    charge_rows = [
        {
            "id": "job-1",
            "name": "Binder Design",
            "tool": "bindcraft",
            "completed_at": NOW,
            "gpu_cost_usd": 4.17,
        }
    ]

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=summary_row)
    conn.fetch = AsyncMock(return_value=charge_rows)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/usage")

    assert response.status_code == 200
    data = response.json()
    assert "job_count" in data
    assert "total_spend_usd" in data
    assert "recent_charges" in data
    assert data["job_count"] == 3
    assert data["total_spend_usd"] == 12.50


async def test_get_usage_unauthenticated():
    """GET /user/usage without auth returns 401."""
    # Remove the auth override so the real dependency runs
    app.dependency_overrides.pop(get_current_user, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/user/usage")

    # Restore for other tests (autouse fixture does this but belt + suspenders)
    app.dependency_overrides[get_current_user] = _mock_user
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /user/settings
# ---------------------------------------------------------------------------

async def test_get_settings():
    """GET /user/settings returns 200 with display_name and notification_preferences."""
    user_row = {
        "email": "test@example.com",
        "display_name": "Test User",
        "notification_preferences": json.dumps({"job_complete": True, "job_failure": True}),
        "is_admin": False,
        # Plan 10-02: /user/settings now also selects tos_version + data_retention_days.
        "tos_version": "2026-04-23",
        "data_retention_days": 90,
        # Plan 10-04: deletion_requested_at added to SELECT.
        "deletion_requested_at": None,
    }

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=user_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/settings")

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["display_name"] == "Test User"
    assert "notification_preferences" in data
    assert data["notification_preferences"]["job_complete"] is True


async def test_get_settings_user_not_found():
    """GET /user/settings returns 404 when user row is missing."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/settings")

    assert response.status_code == 404


async def test_get_settings_null_preferences_uses_default():
    """GET /user/settings returns default notification preferences when column is NULL."""
    user_row = {
        "email": "test@example.com",
        "display_name": "",
        "notification_preferences": None,  # NULL in DB
        "is_admin": False,
        # Plan 10-02 additions.
        "tos_version": None,
        "data_retention_days": 90,
        # Plan 10-04: deletion_requested_at added to SELECT.
        "deletion_requested_at": None,
    }

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=user_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/settings")

    assert response.status_code == 200
    data = response.json()
    # Default preferences should be applied
    assert data["notification_preferences"]["job_complete"] is True
    assert data["notification_preferences"]["job_failure"] is True


# ---------------------------------------------------------------------------
# PUT /user/settings
# ---------------------------------------------------------------------------

async def test_update_settings():
    """PUT /user/settings returns 200 with updated display_name and preferences."""
    updated_row = {
        "email": "test@example.com",
        "display_name": "Updated Name",
        "notification_preferences": json.dumps({"job_complete": False, "job_failure": True}),
    }

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=updated_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    update_body = {
        "display_name": "Updated Name",
        "notification_preferences": {"job_complete": False, "job_failure": True},
    }

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put("/user/settings", json=update_body)

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Updated Name"
    assert data["notification_preferences"]["job_complete"] is False


async def test_update_settings_user_not_found():
    """PUT /user/settings returns 404 when user does not exist."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    update_body = {
        "display_name": "Ghost",
        "notification_preferences": {"job_complete": True, "job_failure": True},
    }

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put("/user/settings", json=update_body)

    assert response.status_code == 404
