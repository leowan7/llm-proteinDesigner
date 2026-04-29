"""Tests for admin API endpoints (/admin/*).

Covers all 7 endpoint groups:
- GET /admin/users
- GET /admin/jobs
- POST /admin/jobs/{id}/cancel
- GET /admin/revenue
- GET /admin/system
- GET /admin/audit
- Audit write_audit called on view_users

Uses FastAPI dependency_overrides to bypass get_current_admin auth.
Patches get_db_pool at the router level so no real DB is required.
"""
import os
os.environ.setdefault("TESTING", "true")

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from admin.dependencies import get_current_admin
from main import app

# Disable rate limiting for all admin router tests — the slowapi middleware
# connects to Redis on every request; there is no Redis in the test environment.
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_ID = "admin-test-id"


async def _mock_admin():
    """FastAPI dependency override that returns a fixed admin user_id."""
    return ADMIN_ID


def _make_ctx(conn):
    """Wrap a mock connection in an async context manager.

    Args:
        conn: The mock asyncpg connection to wrap.

    Returns:
        AsyncMock configured for 'async with pool.acquire() as conn'.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_pool_with_rows(rows, fetchrow_value=None):
    """Build a pool mock whose fetch() returns rows and fetchrow() returns fetchrow_value.

    Args:
        rows: List of dicts to return from conn.fetch().
        fetchrow_value: Dict or None to return from conn.fetchrow().

    Returns:
        AsyncMock pool with a single acquire() side.
    """
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchrow = AsyncMock(return_value=fetchrow_value)
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))
    pool.fetchval = AsyncMock(return_value=1)
    return pool


def _make_multi_pool(*conns):
    """Build a pool mock that cycles through multiple connections on acquire().

    Used when an endpoint makes multiple pool.acquire() calls in sequence.

    Args:
        *conns: AsyncMock connection objects in call order.

    Returns:
        AsyncMock pool with side_effect=[_make_ctx(c) for c in conns].
    """
    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[_make_ctx(c) for c in conns])
    pool.fetchval = AsyncMock(return_value=1)
    return pool


@pytest.fixture
def admin_client():
    """Yield an HTTPX AsyncClient with get_current_admin overridden.

    Sets up and tears down app.dependency_overrides so tests do not
    bleed into each other.
    """
    app.dependency_overrides[get_current_admin] = _mock_admin
    yield
    app.dependency_overrides.pop(get_current_admin, None)


# ---------------------------------------------------------------------------
# GET /admin/users
# ---------------------------------------------------------------------------

async def test_list_users(admin_client):
    """GET /admin/users returns 200 with 'users' list and 'has_more' flag."""
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    user_rows = [
        {
            "id": "uid-1",
            "email": "alice@example.com",
            "display_name": "Alice",
            "created_at": now,
            "stripe_customer_id": "cus_alice",
            "last_login": now,
            "job_count": 3,
            "total_spend": 1.23,
        }
    ]
    mock_pool = _make_pool_with_rows(user_rows)

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/users")

    assert response.status_code == 200
    data = response.json()
    assert "users" in data
    assert "has_more" in data


async def test_list_users_email_filter(admin_client):
    """GET /admin/users?email=test calls the DB query (returns 200)."""
    mock_pool = _make_pool_with_rows([])

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/users?email=test")

    assert response.status_code == 200
    data = response.json()
    assert "users" in data


# ---------------------------------------------------------------------------
# GET /admin/jobs
# ---------------------------------------------------------------------------

async def test_list_jobs(admin_client):
    """GET /admin/jobs returns 200 with 'jobs' key."""
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    job_rows = [
        {
            "id": "job-1",
            "email": "alice@example.com",
            "tool": "rfdiffusion",
            "status": "complete",
            "name": "Test job",
            "created_at": now,
            "completed_at": now,
            "gpu_seconds": 120,
            "gpu_cost_usd": 0.05,
            "error_category": None,
            "results": None,
            "session_id": None,
        }
    ]
    mock_pool = _make_pool_with_rows(job_rows)

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/jobs")

    assert response.status_code == 200
    assert "jobs" in response.json()


async def test_list_jobs_status_filter(admin_client):
    """GET /admin/jobs?status=running returns 200."""
    mock_pool = _make_pool_with_rows([])

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/jobs?status=running")

    assert response.status_code == 200


async def test_list_jobs_invalid_status(admin_client):
    """GET /admin/jobs?status=invalid returns 400."""
    with patch("admin.router.write_audit", new_callable=AsyncMock):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/jobs?status=invalid")

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /admin/jobs/{job_id}/cancel
# ---------------------------------------------------------------------------

async def test_cancel_job(admin_client):
    """POST /admin/jobs/{id}/cancel returns 200 with status='cancelled'.

    Verifies write_audit is called with action='cancel_job'.
    """
    mock_pool = _make_pool_with_rows([])
    mock_cancel_result = {
        "status": "cancelled",
        "gpu_seconds": 60,
        "gpu_cost_usd": 0.01,
        "user_id": "uid-1",
    }

    mock_write_audit = AsyncMock()

    # admin cancel validates job_id is a valid UUID (400 otherwise) — use a real UUID.
    job_uuid = "11111111-1111-1111-1111-111111111111"

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.cancel_job_by_id", new_callable=AsyncMock, return_value=mock_cancel_result),
        patch("admin.router.write_audit", mock_write_audit),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/admin/jobs/{job_uuid}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    # Verify audit was written with action='cancel_job'
    mock_write_audit.assert_called_once()
    call_kwargs = mock_write_audit.call_args
    assert call_kwargs[0][1] == "cancel_job"  # second positional arg is action


# ---------------------------------------------------------------------------
# GET /admin/revenue
# ---------------------------------------------------------------------------

async def test_get_revenue(admin_client):
    """GET /admin/revenue?period=this_month returns 200 with required keys."""
    summary_row = {
        "total_revenue": 100.0,
        "completed_jobs": 10,
        "running_jobs": 2,
        "failed_jobs": 1,
    }
    tool_rows = [
        {"tool": "rfdiffusion", "revenue": 60.0, "job_count": 6},
        {"tool": "bindcraft", "revenue": 40.0, "job_count": 4},
    ]

    # revenue endpoint calls conn.fetchrow (summary) then conn.fetch (by_tool)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=summary_row)
    conn.fetch = AsyncMock(return_value=tool_rows)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/revenue?period=this_month")

    assert response.status_code == 200
    data = response.json()
    assert "total_revenue" in data
    assert "by_tool" in data
    assert "cost_of_goods_usd" in data
    assert "margin_usd" in data


# ---------------------------------------------------------------------------
# GET /admin/system
# ---------------------------------------------------------------------------

async def test_get_system_health(admin_client):
    """GET /admin/system returns 200 with api, db, redis, running_jobs, queued_jobs keys."""
    queue_row = {"running": 3, "queued": 1}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=queue_row)
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
        patch("admin.router.aioredis.from_url", return_value=mock_redis),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/system")

    assert response.status_code == 200
    data = response.json()
    assert "api" in data
    assert "db" in data
    assert "redis" in data
    assert "running_jobs" in data
    assert "queued_jobs" in data


# ---------------------------------------------------------------------------
# GET /admin/audit
# ---------------------------------------------------------------------------

async def test_get_audit_log(admin_client):
    """GET /admin/audit returns 200 with 'entries' key."""
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    audit_rows = [
        {
            "id": "aud-1",
            "admin_email": "leo@ranomics.com",
            "action": "view_users",
            "target_id": None,
            "metadata": {},
            "created_at": now,
        }
    ]
    mock_pool = _make_pool_with_rows(audit_rows)

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin/audit")

    assert response.status_code == 200
    assert "entries" in response.json()


# ---------------------------------------------------------------------------
# Audit log written on view_users
# ---------------------------------------------------------------------------

async def test_audit_log_written_on_view(admin_client):
    """GET /admin/users calls write_audit with action='view_users'."""
    mock_pool = _make_pool_with_rows([])
    mock_write_audit = AsyncMock()

    with (
        patch("admin.router.get_db_pool", return_value=mock_pool),
        patch("admin.router.write_audit", mock_write_audit),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/admin/users")

    mock_write_audit.assert_called_once()
    # Second positional arg to write_audit is the action string
    assert mock_write_audit.call_args[0][1] == "view_users"
