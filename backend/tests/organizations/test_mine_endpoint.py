"""Unit tests for GET /organizations/mine.

Verifies:

- Returns every org the caller is a member of with role + is_personal.
- Sorted with is_personal=True first (the org switcher default).
- Single-personal-org user returns one entry.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


def _build_app(user_id: str = "user-abc"):
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from organizations.router import router as orgs_router

    app = FastAPI()
    app.include_router(orgs_router)

    async def _user():
        return user_id

    app.dependency_overrides[get_current_user] = _user
    return app


def _pool_with_fetch(rows: list[dict]):
    async def _fetch(query, *args):
        return rows

    conn = AsyncMock()
    conn.fetch = _fetch

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def test_returns_all_memberships():
    """User in 3 orgs sees all 3 entries."""
    from httpx import ASGITransport, AsyncClient

    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    org_c = uuid.uuid4()
    rows = [
        {"id": org_a, "name": "Personal", "is_personal": True, "role": "owner"},
        {"id": org_b, "name": "Acme Bio", "is_personal": False, "role": "scientist"},
        {"id": org_c, "name": "Zeta Labs", "is_personal": False, "role": "viewer"},
    ]
    pool = _pool_with_fetch(rows)

    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/organizations/mine")

    assert r.status_code == 200, r.text
    data = r.json()
    assert "orgs" in data
    assert len(data["orgs"]) == 3
    # Roles round-trip
    roles = [o["role"] for o in data["orgs"]]
    assert "owner" in roles and "scientist" in roles and "viewer" in roles


async def test_orders_personal_first():
    """Personal org listed first regardless of name ordering."""
    from httpx import ASGITransport, AsyncClient

    # SQL does ORDER BY is_personal DESC, name -- simulate by passing the
    # already-sorted rows the DB would return.
    personal_id = uuid.uuid4()
    team_id = uuid.uuid4()
    rows = [
        {"id": personal_id, "name": "ZZ-Personal", "is_personal": True, "role": "owner"},
        {"id": team_id, "name": "AA-Team", "is_personal": False, "role": "scientist"},
    ]
    pool = _pool_with_fetch(rows)

    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/organizations/mine")

    assert r.status_code == 200
    orgs = r.json()["orgs"]
    assert orgs[0]["is_personal"] is True
    assert orgs[1]["is_personal"] is False


async def test_single_personal_org_only():
    """Solo user with only a personal org returns one entry."""
    from httpx import ASGITransport, AsyncClient

    personal_id = uuid.uuid4()
    rows = [{"id": personal_id, "name": "Personal", "is_personal": True, "role": "owner"}]
    pool = _pool_with_fetch(rows)

    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/organizations/mine")

    assert r.status_code == 200
    orgs = r.json()["orgs"]
    assert len(orgs) == 1
    assert orgs[0]["is_personal"] is True
    assert orgs[0]["role"] == "owner"
