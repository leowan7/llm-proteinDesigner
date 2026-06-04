"""Unit tests for POST /organizations/{org_id}/members/transfer.

Verifies the atomic promote-then-demote contract from RESEARCH §5.3:

- Happy path: target is promoted first, then current owner is demoted.
- target_user_id == current_user_id -> 400.
- target is not a member -> 404.
- new_self_role not in {scientist, viewer} -> 400.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


def _build_app(user_id: str = "owner-id", org_id: str = "org-1"):
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from organizations.router import router as orgs_router

    app = FastAPI()
    app.include_router(orgs_router)

    async def _user():
        return user_id

    async def _active():
        return (org_id, "owner")

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_active_org] = _active
    return app


def _make_pool(target_membership_row=None):
    captured = {"execute_calls": []}

    async def _execute(query, *args):
        captured["execute_calls"].append((query, args))
        return "UPDATE 1"

    async def _fetchrow(query, *args):
        return target_membership_row

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchrow = _fetchrow

    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, captured


async def test_transfer_promotes_target_then_demotes_self():
    """Verify the UPDATE statements run in promote-then-demote order."""
    from httpx import ASGITransport, AsyncClient

    pool, captured = _make_pool(target_membership_row={"role": "scientist"})
    app = _build_app(user_id="owner-id")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/members/transfer",
                json={"target_user_id": "target-id", "new_self_role": "scientist"},
                headers={"X-Org-Id": "org-1"},
            )

    assert r.status_code == 200, r.text
    # Two UPDATEs should have run in the transaction
    updates = [c for c in captured["execute_calls"] if "UPDATE" in c[0]]
    assert len(updates) == 2
    # First sets target to owner, second sets self to scientist
    assert "'owner'::public.org_role" in updates[0][0]
    assert "target-id" in updates[0][1]
    assert "$3::public.org_role" in updates[1][0]
    assert "owner-id" in updates[1][1]
    assert "scientist" in updates[1][1]


async def test_transfer_to_self_returns_400():
    """target == current_user_id -> 400."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_pool(target_membership_row={"role": "owner"})
    app = _build_app(user_id="owner-id")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/members/transfer",
                json={"target_user_id": "owner-id", "new_self_role": "scientist"},
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 400
    assert "self" in r.json()["detail"].lower()


async def test_transfer_to_non_member_returns_404():
    """Target user has no membership row -> 404."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_pool(target_membership_row=None)
    app = _build_app(user_id="owner-id")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/members/transfer",
                json={"target_user_id": "stranger", "new_self_role": "viewer"},
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 404
    assert "not a member" in r.json()["detail"].lower()


async def test_transfer_new_self_role_must_be_scientist_or_viewer():
    """new_self_role='owner' (or anything else) is rejected by Pydantic -> 422."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_pool(target_membership_row={"role": "scientist"})
    app = _build_app(user_id="owner-id")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/members/transfer",
                json={"target_user_id": "target-id", "new_self_role": "owner"},
                headers={"X-Org-Id": "org-1"},
            )
    # Pydantic Literal[scientist, viewer] rejects "owner" at validation time -> 422
    # (the service-layer 400 guard is defensive only).
    assert r.status_code in (400, 422)
