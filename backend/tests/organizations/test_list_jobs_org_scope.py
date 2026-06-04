"""Phase 12 Plan 12-03 — GET /jobs returns rows for the active org, not just the caller.

Covers ORG-03 (org-scoped job visibility):
- A job launched by user A in org X is visible to user B (also a member of org X)
- Response includes created_by_user_id + created_by_email fields surfaced by
  the LEFT JOIN public.users
- Jobs are ordered newest first

Uses isolated FastAPI sub-app per test and overrides require_role's
get_active_org dependency so the test focuses on the org-scoped SQL path,
not on the membership cross-check (which test_cross_org_isolation.py covers).
"""
from __future__ import annotations

import datetime
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


def _build_app(active_role: str = "scientist", user_id: str = "user-B", org_id: str = "org-X"):
    """Build an isolated FastAPI app with the jobs router mounted.

    Overrides get_current_user + get_active_org so require_role resolves to
    org_id without touching the membership table.
    """
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from jobs.router import router as jobs_router
    from middleware.rate_limit import limiter as _limiter

    _limiter.enabled = False

    app = FastAPI()
    # slowapi requires a limiter on app.state for limiter-decorated routes.
    app.state.limiter = _limiter
    app.include_router(jobs_router)

    async def _user():
        return user_id

    async def _active():
        return (org_id, active_role)

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_active_org] = _active
    return app


def _pool_with_rows(rows):
    """Build a pool whose conn.fetch returns the given rows."""
    async def _fetch(query, *args):
        return rows

    async def _fetchrow(query, *args):
        return None

    async def _execute(query, *args):
        return "OK"

    conn = AsyncMock()
    conn.fetch = _fetch
    conn.fetchrow = _fetchrow
    conn.execute = _execute

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def test_member_b_sees_member_a_jobs_in_same_org():
    """ORG-03: all org members see all org jobs.

    UserB calls GET /jobs with active_role=scientist + org=org-X. The mocked
    DB returns one row whose created_by_user_id is user-A. UserB sees it.
    """
    from httpx import ASGITransport, AsyncClient

    user_a_id = uuid.uuid4()
    job_id = uuid.uuid4()
    rows = [
        {
            "id": job_id,
            "tool": "bindcraft",
            "status": "complete",
            "name": "B-targets-IL6R",
            "created_at": datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            "completed_at": datetime.datetime(2026, 6, 1, 13, 0, 0, tzinfo=datetime.timezone.utc),
            "gpu_cost_usd": 0.42,
            "candidate_count": "5",
            "session_id": None,
            "created_by_user_id": user_a_id,
            "created_by_email": "alice@acme.bio",
        }
    ]
    pool = _pool_with_rows(rows)
    app = _build_app(active_role="scientist", user_id="user-B")
    with patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/jobs/", headers={"X-Org-Id": "org-X"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["jobs"]) == 1
    j = data["jobs"][0]
    assert j["id"] == str(job_id)
    assert j["created_by_user_id"] == str(user_a_id)
    assert j["created_by_email"] == "alice@acme.bio"


async def test_response_includes_created_by_user_id_and_email():
    """list_jobs response surfaces created_by_user_id + created_by_email fields."""
    from httpx import ASGITransport, AsyncClient

    rows = [
        {
            "id": uuid.uuid4(),
            "tool": "rfdiffusion",
            "status": "complete",
            "name": "test",
            "created_at": datetime.datetime(2026, 6, 1, 0, 0, 0, tzinfo=datetime.timezone.utc),
            "completed_at": None,
            "gpu_cost_usd": None,
            "candidate_count": None,
            "session_id": None,
            "created_by_user_id": uuid.uuid4(),
            "created_by_email": "scientist@org.test",
        }
    ]
    pool = _pool_with_rows(rows)
    app = _build_app()
    with patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/jobs/", headers={"X-Org-Id": "org-X"})
    assert r.status_code == 200
    j = r.json()["jobs"][0]
    assert "created_by_user_id" in j
    assert "created_by_email" in j
    assert j["created_by_email"] == "scientist@org.test"


async def test_response_orders_by_created_at_desc():
    """Jobs are ordered newest first — verified via SQL substring inspection."""
    from httpx import ASGITransport, AsyncClient

    captured_query: dict[str, str] = {}

    async def _fetch(query, *args):
        captured_query["sql"] = query
        return []

    async def _fetchrow(query, *args):
        return None

    async def _execute(query, *args):
        return "OK"

    conn = AsyncMock()
    conn.fetch = _fetch
    conn.fetchrow = _fetchrow
    conn.execute = _execute

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)

    app = _build_app()
    with patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/jobs/", headers={"X-Org-Id": "org-X"})

    assert r.status_code == 200
    assert "ORDER BY j.created_at DESC" in captured_query["sql"]
    # Confirm org-scoping (this also covers the "WHERE organization_id" requirement)
    assert "j.organization_id = $1" in captured_query["sql"]
    assert "LEFT JOIN public.users" in captured_query["sql"]
