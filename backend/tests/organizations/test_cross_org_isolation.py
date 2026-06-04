"""Phase 12 Plan 12-03 — X-Org-Id spoofing returns 403; cross-org reads return empty.

Covers ORG-03 (cross-org isolation):
- A user with no membership in org X who calls GET /jobs with X-Org-Id=org-X
  gets a 403 from get_active_org's membership cross-check
- A user with a membership row succeeds
- The X-Org-Id is re-validated on EVERY request (no caching)

These tests exercise the real get_active_org dependency (only get_current_user
is overridden), so the membership SELECT against organization_memberships is
the actual gate under test.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


def _build_app(user_id: str = "user-attacker"):
    """Build a FastAPI app with jobs router + only get_current_user overridden.

    get_active_org runs unmodified so the membership cross-check actually fires.
    """
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from jobs.router import router as jobs_router
    from middleware.rate_limit import limiter as _limiter

    _limiter.enabled = False
    app = FastAPI()
    app.state.limiter = _limiter
    app.include_router(jobs_router)

    async def _user():
        return user_id

    app.dependency_overrides[get_current_user] = _user
    return app


def _membership_pool(membership_role: str | None):
    """Build a pool that returns the given membership row (or None) on fetchrow."""
    async def _fetchrow(query, *args):
        # get_active_org fetches:
        #   SELECT role::text AS role FROM public.organization_memberships
        #   WHERE organization_id = $1 AND user_id = $2
        if "organization_memberships" in query:
            return {"role": membership_role} if membership_role else None
        return None

    async def _fetch(query, *args):
        return []

    async def _execute(query, *args):
        return "OK"

    conn = AsyncMock()
    conn.fetchrow = _fetchrow
    conn.fetch = _fetch
    conn.execute = _execute

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def test_user_with_no_membership_in_org_x_gets_403_when_using_x_org_id():
    """get_active_org rejects requests whose caller is not in organization_memberships."""
    from httpx import ASGITransport, AsyncClient

    pool = _membership_pool(membership_role=None)
    app = _build_app()

    # Patch BOTH places get_db_pool can be reached from:
    #   - auth.org_dependencies (the membership lookup)
    #   - jobs.router (the org-scoped jobs lookup, in case it runs anyway)
    with patch("auth.org_dependencies.get_db_pool", return_value=pool), \
         patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/jobs/", headers={"X-Org-Id": "org-X-not-mine"})

    assert r.status_code == 403, r.text
    assert "Not a member" in r.json()["detail"]


async def test_user_with_membership_in_org_x_succeeds_with_x_org_id():
    """Membership row exists -> 200 with empty job list (mocked)."""
    from httpx import ASGITransport, AsyncClient

    pool = _membership_pool(membership_role="scientist")
    app = _build_app()

    with patch("auth.org_dependencies.get_db_pool", return_value=pool), \
         patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/jobs/", headers={"X-Org-Id": "org-X"})

    assert r.status_code == 200, r.text
    assert r.json() == {"jobs": [], "has_more": False}


async def test_changing_x_org_id_to_unauthorized_org_returns_403_per_request():
    """X-Org-Id is validated per request, not cached across requests."""
    from httpx import ASGITransport, AsyncClient

    # First call: caller IS a member of org-A. Membership lookup returns scientist.
    # Second call (same client): caller is NOT a member of org-B. Membership lookup returns None.
    call_count = {"n": 0}

    async def _fetchrow(query, *args):
        if "organization_memberships" in query:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"role": "scientist"}
            return None
        return None

    async def _fetch(query, *args):
        return []

    async def _execute(query, *args):
        return "OK"

    conn = AsyncMock()
    conn.fetchrow = _fetchrow
    conn.fetch = _fetch
    conn.execute = _execute

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)

    app = _build_app()
    with patch("auth.org_dependencies.get_db_pool", return_value=pool), \
         patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/jobs/", headers={"X-Org-Id": "org-A"})
            r2 = await client.get("/jobs/", headers={"X-Org-Id": "org-B"})

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 403, r2.text
    # Confirm both requests touched the membership lookup (no caching).
    assert call_count["n"] == 2
