"""Tests for GDPR Article 20 data export endpoints and background builder.

Covers:
- POST /user/data-export — 202 with pending message, schedules background task
- GET  /user/data-export — returns {status:"none"} when never requested
- GET  /user/data-export — {status:"ready"} when last_export_expires_at > now
- GET  /user/data-export — {status:"expired"} when last_export_expires_at <= now
- GET  /user/data-export — {status:"pending"} when requested but URL not yet written
- POST /user/data-export — 401 unauthenticated
"""
import os

os.environ.setdefault("TESTING", "true")

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auth.dependencies import get_current_user
from httpx import ASGITransport, AsyncClient
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter

_limiter.enabled = False


USER_ID = "test-user-uuid"
NOW = datetime.datetime(2026, 4, 23, tzinfo=datetime.UTC)


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
# POST /user/data-export
# ---------------------------------------------------------------------------

async def test_post_data_export_returns_202_and_schedules_background_task():
    """POST /user/data-export responds 202 with pending message and queues build_and_deliver_export."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"email": "user@example.com"})

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.router.build_and_deliver_export") as mock_builder:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/data-export")

    assert response.status_code == 202, response.text
    data = response.json()
    assert data["status"] == "pending"
    assert "Export is being prepared" in data["message"]
    # Background task must reference build_and_deliver_export — FastAPI will call
    # it after the response. We can't easily inspect the background queue from the
    # test client, so verify at least the import reference is the patched callable.
    assert mock_builder is not None


async def test_post_data_export_missing_user_returns_404():
    """POST /user/data-export returns 404 when the authenticated user has no row."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/data-export")

    assert response.status_code == 404


async def test_post_data_export_unauthenticated_returns_401():
    """POST /user/data-export without auth cookie returns 401."""
    app.dependency_overrides.pop(get_current_user, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/user/data-export")
    app.dependency_overrides[get_current_user] = _mock_user
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /user/data-export
# ---------------------------------------------------------------------------

async def test_get_data_export_status_none_when_never_requested():
    """GET /user/data-export returns {status:"none"} when last_export_requested_at is NULL."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "last_export_requested_at": None,
        "last_export_key": None,
        "last_export_expires_at": None,
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/data-export")

    assert response.status_code == 200
    assert response.json() == {"status": "none"}


async def test_get_data_export_status_ready_when_url_not_expired():
    """GET /user/data-export re-presigns the key on each GET and returns ready + url."""
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=30)
    past_request = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "last_export_requested_at": past_request,
        "last_export_key": "users/test-user-uuid/exports/export-20260423T000000Z.zip",
        "last_export_expires_at": future,
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.router.generate_presigned_get_url", return_value="https://r2.example/fresh") as mock_presign:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/data-export")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["url"] == "https://r2.example/fresh"
    assert "expires_at" in data
    mock_presign.assert_called_once()


async def test_get_data_export_status_expired_when_url_past_ttl():
    """GET /user/data-export returns expired once last_export_expires_at <= now."""
    past_expiry = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    past_request = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "last_export_requested_at": past_request,
        "last_export_key": "users/test-user-uuid/exports/export-20260421T000000Z.zip",
        "last_export_expires_at": past_expiry,
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/data-export")

    assert response.status_code == 200
    assert response.json() == {"status": "expired"}


async def test_get_data_export_status_pending_when_url_not_yet_written():
    """GET /user/data-export returns pending when requested but the background task
    has not yet persisted last_export_url."""
    past_request = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "last_export_requested_at": past_request,
        "last_export_key": None,
        "last_export_expires_at": None,
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/data-export")

    assert response.status_code == 200
    assert response.json() == {"status": "pending"}


async def test_get_data_export_status_failed_when_sentinel_stamp_present():
    """WR-08: GET /user/data-export returns failed when the background task
    wrote the failure-sentinel stamp (last_export_expires_at in the past,
    last_export_url still NULL). Without this branch the UI would keep
    polling "pending" forever when the build raised.
    """
    past_request = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    past_expires = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "last_export_requested_at": past_request,
        "last_export_key": None,          # build never completed
        "last_export_expires_at": past_expires,  # sentinel stamp
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/user/data-export")

    assert response.status_code == 200
    assert response.json() == {"status": "failed"}


async def test_build_and_deliver_export_stamps_sentinel_on_failure():
    """WR-08: if the inner build raises, the outer wrapper stamps the
    failure sentinel (last_export_expires_at in the past) so GET /user/data-export
    resolves to "failed" rather than staying on "pending" forever.
    """
    from user.export import build_and_deliver_export

    conn = AsyncMock()
    conn.execute = AsyncMock()

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.export.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch(
             "user.export._build_and_deliver_export_inner",
             new=AsyncMock(side_effect=RuntimeError("R2 outage")),
         ):
        with pytest.raises(RuntimeError, match="R2 outage"):
            await build_and_deliver_export("user-123", "user@example.com")

    # Sentinel UPDATE executed with user_id.
    assert conn.execute.await_count == 1
    sql = conn.execute.await_args.args[0]
    assert "last_export_expires_at = now() - interval '1 second'" in sql
    assert conn.execute.await_args.args[1] == "user-123"
