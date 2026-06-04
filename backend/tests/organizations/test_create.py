"""Unit tests for POST /organizations (create org via SECURITY DEFINER RPC).

The router calls ``public.create_organization($1)`` which is the canonical
chicken-and-egg sidestep (RESEARCH §4.2). These tests assert:

- 201 response with role='owner' on the happy path
- Empty name returns 422 (Pydantic min_length=1)
- Over-long name returns 422 (Pydantic max_length=100)
- Connection.execute is called with SET LOCAL request.jwt.claims so auth.uid()
  resolves inside the RPC
- Connection.fetchval is called with the exact RPC SQL string
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Mount the org router even though main.app was imported with the flag off.
# We build a fresh isolated FastAPI app per test.
os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(user_id: str = "user-abc"):
    """Build a minimal FastAPI app with the org router mounted and auth overridden."""
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from organizations.router import router as orgs_router

    app = FastAPI()
    app.include_router(orgs_router)

    async def _user():
        return user_id

    app.dependency_overrides[get_current_user] = _user
    return app


def _make_pool(fetchval_return, fetchrow_return):
    """Build an asyncpg-like pool whose conn captures execute + fetchval + fetchrow."""
    captured = {"execute_calls": [], "fetchval_calls": [], "fetchrow_calls": []}

    async def _execute(query, *args):
        captured["execute_calls"].append((query, args))
        return "OK"

    async def _fetchval(query, *args):
        captured["fetchval_calls"].append((query, args))
        return fetchval_return

    async def _fetchrow(query, *args):
        captured["fetchrow_calls"].append((query, args))
        return fetchrow_return

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchval = _fetchval
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_org_returns_201_with_owner_role():
    """POST /organizations -> 201 + role=owner."""
    from httpx import ASGITransport, AsyncClient

    new_org_id = uuid.uuid4()
    pool, _ = _make_pool(
        fetchval_return=new_org_id,
        fetchrow_return={"id": new_org_id, "name": "Acme Bio", "is_personal": False},
    )

    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/organizations", json={"name": "Acme Bio"})

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["role"] == "owner"
    assert data["name"] == "Acme Bio"
    assert data["is_personal"] is False


async def test_creates_with_owner_membership():
    """Verify the SET LOCAL request.jwt.claims is set so auth.uid() resolves inside the RPC."""
    from httpx import ASGITransport, AsyncClient

    new_org_id = uuid.uuid4()
    pool, captured = _make_pool(
        fetchval_return=new_org_id,
        fetchrow_return={"id": new_org_id, "name": "Acme Bio", "is_personal": False},
    )

    app = _build_app(user_id="user-jwt-sub")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/organizations", json={"name": "Acme Bio"})

    assert r.status_code == 201
    # Confirm SET LOCAL request.jwt.claims was emitted
    set_local_calls = [
        c for c in captured["execute_calls"]
        if "request.jwt.claims" in c[0] or "set_config" in c[0]
    ]
    assert set_local_calls, f"Expected request.jwt.claims setter; got: {captured['execute_calls']}"
    # The user_id should appear in the JSON arg
    jwt_arg = set_local_calls[0][1][0] if set_local_calls[0][1] else ""
    assert "user-jwt-sub" in jwt_arg


async def test_empty_name_returns_422():
    """Empty name -> Pydantic 422 (min_length=1)."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_pool(fetchval_return=None, fetchrow_return=None)
    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/organizations", json={"name": ""})
    assert r.status_code == 422


async def test_name_too_long_returns_422():
    """Name > 100 chars -> Pydantic 422 (max_length=100)."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_pool(fetchval_return=None, fetchrow_return=None)
    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/organizations", json={"name": "A" * 101})
    assert r.status_code == 422


async def test_calls_security_definer_rpc():
    """Verify fetchval is called with 'SELECT public.create_organization($1)' exactly."""
    from httpx import ASGITransport, AsyncClient

    new_org_id = uuid.uuid4()
    pool, captured = _make_pool(
        fetchval_return=new_org_id,
        fetchrow_return={"id": new_org_id, "name": "Acme", "is_personal": False},
    )

    app = _build_app()
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/organizations", json={"name": "Acme"})

    assert r.status_code == 201
    rpc_calls = [c for c in captured["fetchval_calls"] if c[0] == "SELECT public.create_organization($1)"]
    assert rpc_calls, f"Expected SELECT public.create_organization($1); got: {captured['fetchval_calls']}"
    # And the bound arg is the org name
    assert rpc_calls[0][1][0] == "Acme"
