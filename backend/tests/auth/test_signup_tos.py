"""Tests for ToS acceptance on /auth/signup (Phase 10 Plan 02).

Covers:
- POST /auth/signup with `tos_version == settings.tos_current_version` →
  200 and the signup handler writes tos_accepted_at + tos_version to public.users.
- POST /auth/signup with `tos_version = "wrong"` → 400 with a version-mismatch detail.
- POST /auth/signup with no `tos_version` field → 422 (Pydantic validation).

Uses patching on `auth.router._get_supabase` to stub the Supabase client call so we
do not hit a real auth server, and patches `auth.router.get_db_pool` so the post-signup
UPDATE runs against a mock asyncpg connection.
"""
import os

os.environ.setdefault("TESTING", "true")

from unittest.mock import AsyncMock, MagicMock, patch

from config import settings
from httpx import ASGITransport, AsyncClient
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter

_limiter.enabled = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NEW_USER_ID = "new-user-uuid"


def _make_ctx(conn):
    """Wrap a mock asyncpg connection in an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _fake_supabase_success():
    """Return a stub Supabase client whose sign_up succeeds with a new user id."""
    user = MagicMock()
    user.id = NEW_USER_ID
    result = MagicMock()
    result.user = user
    result.session = None  # email verification enabled — no session yet
    client = MagicMock()
    client.auth.sign_up = MagicMock(return_value=result)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_signup_with_matching_tos_version_persists_acceptance():
    """Signup with body.tos_version == settings.tos_current_version returns 200
    and runs an UPDATE against public.users with the new user id + current version."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("auth.router._get_supabase", return_value=_fake_supabase_success()), \
         patch("auth.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "new@example.com",
                    "password": "Passw0rd!12",
                    "tos_version": settings.tos_current_version,
                },
            )

    assert response.status_code == 200, response.text
    # Handler must have run an UPDATE with (user_id, tos_version) args.
    assert conn.execute.await_count >= 1
    call = conn.execute.await_args_list[0]
    assert call.args[1] == NEW_USER_ID
    assert call.args[2] == settings.tos_current_version
    # UPDATE SQL should target public.users and set tos_accepted_at + tos_version.
    sql = call.args[0]
    assert "public.users" in sql
    assert "tos_accepted_at" in sql
    assert "tos_version" in sql


async def test_signup_with_wrong_tos_version_returns_400():
    """Signup with body.tos_version != settings.tos_current_version returns 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "new@example.com",
                "password": "Passw0rd!12",
                "tos_version": "not-the-current-version",
            },
        )

    assert response.status_code == 400, response.text
    assert "Terms of Service version mismatch" in response.json().get("detail", "")


async def test_signup_without_tos_version_returns_422():
    """Signup with missing tos_version field returns 422 (Pydantic validation)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "new@example.com",
                "password": "Passw0rd!12",
            },
        )

    assert response.status_code == 422, response.text
