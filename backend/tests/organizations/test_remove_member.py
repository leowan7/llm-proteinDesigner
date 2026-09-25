"""Unit tests for DELETE /organizations/{org_id}/members/{user_id}.

Verifies:

- Owner can remove any other member -> 200
- Scientist cannot remove someone other than self -> 403
- Self-removal is allowed for any role -> 200
- The protect_last_owner trigger fires when owner-removes-themselves and
  is the only owner -> 400 (check_violation translated)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

os.environ.setdefault("TESTING", "true")


# organization_memberships.organization_id is uuid, so a fake org id has
# to be one too: auth/org_dependencies.py rejects a non-uuid X-Org-Id
# with 400 before any query runs.
ORG_1 = "11111111-1111-4111-8111-111111111111"

pytestmark = pytest.mark.asyncio


def _build_app(caller_id: str = "user-caller", role: str = "owner", org_id: str = ORG_1):
    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from fastapi import FastAPI
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
    app = _build_app(caller_id="owner-id", role="owner", org_id=ORG_1)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                f"/organizations/{ORG_1}/members/scientist-id",
                headers={"X-Org-Id": ORG_1},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "removed"


async def test_scientist_cannot_remove_other_member_returns_403():
    """Scientist trying to remove a different user -> 403."""
    from httpx import ASGITransport, AsyncClient

    pool = _pool_with_delete()
    app = _build_app(caller_id="scientist-A", role="scientist", org_id=ORG_1)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                f"/organizations/{ORG_1}/members/scientist-B",
                headers={"X-Org-Id": ORG_1},
            )
    assert r.status_code == 403


async def test_self_removal_allowed_for_any_role():
    """Scientist removing self -> 200 (trigger handles last-owner protection)."""
    from httpx import ASGITransport, AsyncClient

    pool = _pool_with_delete()
    app = _build_app(caller_id="scientist-X", role="scientist", org_id=ORG_1)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                f"/organizations/{ORG_1}/members/scientist-X",
                headers={"X-Org-Id": ORG_1},
            )
    assert r.status_code == 200


async def test_owner_removing_last_owner_raises_trigger_error():
    """Last-owner DELETE raises check_violation -> 400.

    The trigger uses RAISE ... USING ERRCODE = 'check_violation', i.e. SQLSTATE
    23514, and asyncpg picks the exception class from the SQLSTATE. 23514 is
    CheckViolationError, which is NOT a subclass of RaiseError (P0001), so the
    handler has to name this exact class to translate it.
    """
    from httpx import ASGITransport, AsyncClient

    # Simulate the protect_last_owner trigger raising on DELETE.
    assert asyncpg.exceptions.CheckViolationError.sqlstate == "23514"
    trigger_error = asyncpg.exceptions.CheckViolationError(
        f"Cannot remove or demote last owner of organization {ORG_1}"
    )
    pool = _pool_with_delete(execute_raises=trigger_error)
    app = _build_app(caller_id="last-owner-id", role="owner", org_id=ORG_1)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(
                "DELETE",
                f"/organizations/{ORG_1}/members/last-owner-id",
                headers={"X-Org-Id": ORG_1},
            )
    assert r.status_code == 400
    assert "last owner" in r.json()["detail"].lower()
