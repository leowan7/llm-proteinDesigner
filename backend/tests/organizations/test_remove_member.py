"""Unit tests for DELETE /organizations/{org_id}/members/{user_id}.

Verifies:

- Owner can remove any other member -> 200
- Scientist cannot remove someone other than self -> 403
- Self-removal is allowed for any role -> 200
- The protect_last_owner trigger fires when owner-removes-themselves and
  is the only owner -> 400 (RaiseError translated)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


def _build_app(caller_id: str = "user-caller", role: str = "owner", org_id: str = "org-1"):
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from organizations.router import router as orgs_router

    app = FastAPI()
    app.include_router(orgs_router)

    async def _user():
        return caller_id

    async def _active():
        return (org_id, role)

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_active_org] = _active
    return app


def _pool_with_delete(execute_return="DELETE 1", execute_raises: Exception | None = None):
    async def _execute(query, *args):
        if execute_raises is not None:
            raise execute_raises
        return execute_return

    conn = AsyncMock()
    conn.execute = _execute

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def test_owner_removes_scientist():
    """Owner DELETE another member -> 200."""
    from httpx import ASGITransport, AsyncClient

    pool = _pool_with_delete()
    app = _build_app(caller_id="owner-id", role="owner", org_id="org-1")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                "/organizations/org-1/members/scientist-id",
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "removed"


async def test_scientist_cannot_remove_other_member_returns_403():
    """Scientist trying to remove a different user -> 403."""
    from httpx import ASGITransport, AsyncClient

    pool = _pool_with_delete()
    app = _build_app(caller_id="scientist-A", role="scientist", org_id="org-1")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                "/organizations/org-1/members/scientist-B",
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 403


async def test_self_removal_allowed_for_any_role():
    """Scientist removing self -> 200 (trigger handles last-owner protection)."""
    from httpx import ASGITransport, AsyncClient

    pool = _pool_with_delete()
    app = _build_app(caller_id="scientist-X", role="scientist", org_id="org-1")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                "/organizations/org-1/members/scientist-X",
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 200


async def test_owner_removing_last_owner_raises_trigger_error():
    """Last-owner DELETE raises check_violation (asyncpg.RaiseError) -> 400."""
    from httpx import ASGITransport, AsyncClient

    # Simulate the protect_last_owner trigger raising on DELETE.
    trigger_error = asyncpg.exceptions.RaiseError(
        "Cannot remove or demote last owner of organization org-1"
    )
    pool = _pool_with_delete(execute_raises=trigger_error)
    app = _build_app(caller_id="last-owner-id", role="owner", org_id="org-1")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                "/organizations/org-1/members/last-owner-id",
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 400
    assert "last owner" in r.json()["detail"].lower()
