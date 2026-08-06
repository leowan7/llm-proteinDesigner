"""Tests for ToS-related user endpoints (Phase 10 Plan 02).

Covers:
- GET  /user/settings — response includes `tos_version`, `tos_current`,
  `data_retention_days`; does NOT include `deletion_requested_at`
  (that field is owned by Plan 10-04).
- POST /user/accept-tos — authenticated user updates `tos_accepted_at` +
  `tos_version` to settings.tos_current_version and receives
  {"accepted": true, "tos_version": "<current>"}.
- POST /user/accept-tos — unauthenticated request returns 401.
"""
import os

os.environ.setdefault("TESTING", "true")

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auth.dependencies import get_current_user
from config import settings
from httpx import ASGITransport, AsyncClient
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter

_limiter.enabled = False


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

USER_ID = "test-user-uuid"


async def _mock_user():
    return USER_ID


def _make_ctx(conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = _mock_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# GET /user/settings — new fields
# ---------------------------------------------------------------------------

async def test_get_settings_includes_tos_version_and_current_and_retention():
    """GET /user/settings returns tos_version, tos_current, data_retention_days."""
    user_row = {
        "email": "user@example.com",
        "display_name": "User",
        "notification_preferences": json.dumps(
            {"job_complete": True, "job_failure": True}
        ),
        "is_admin": False,
        "tos_version": "2026-04-23",
        "data_retention_days": 90,
        # Plan 10-04: /settings now also selects deletion_requested_at.
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

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tos_version"] == "2026-04-23"
    assert data["tos_current"] == settings.tos_current_version
    assert data["data_retention_days"] == 90


async def test_get_settings_includes_deletion_requested_at():
    """Plan 10-04 adds `deletion_requested_at` to the /settings response so the
    Privacy tab can render the pending-deletion banner + Cancel button. This
    test locks that contract (previously 10-02 asserted its ABSENCE)."""
    user_row = {
        "email": "user@example.com",
        "display_name": "",
        "notification_preferences": None,
        "is_admin": False,
        "tos_version": None,
        "data_retention_days": 90,
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
    # Key is present; value is None when the user has no pending deletion.
    assert "deletion_requested_at" in data
    assert data["deletion_requested_at"] is None


# ---------------------------------------------------------------------------
# POST /user/accept-tos
# ---------------------------------------------------------------------------

async def test_accept_tos_authenticated_updates_row_and_returns_current_version():
    """POST /user/accept-tos with auth returns {accepted: true, tos_version: <current>}."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/accept-tos")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["accepted"] is True
    assert data["tos_version"] == settings.tos_current_version

    # UPDATE ran with (user_id, tos_current_version).
    assert conn.execute.await_count == 1
    call = conn.execute.await_args_list[0]
    assert call.args[1] == USER_ID
    assert call.args[2] == settings.tos_current_version
    sql = call.args[0]
    assert "public.users" in sql
    assert "tos_accepted_at" in sql
    assert "tos_version" in sql


async def test_accept_tos_user_missing_returns_404():
    """If the UPDATE matches 0 rows (user not in public.users), return 404."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/accept-tos")

    assert response.status_code == 404


async def test_accept_tos_unauthenticated_returns_401():
    """POST /user/accept-tos without auth cookie returns 401."""
    # Drop the auth override so the real get_current_user runs and fails.
    app.dependency_overrides.pop(get_current_user, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/user/accept-tos")

    # Restore (the autouse fixture also handles this, belt + suspenders).
    app.dependency_overrides[get_current_user] = _mock_user
    assert response.status_code == 401
