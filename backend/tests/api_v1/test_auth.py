"""Tests for the API key authentication dependency.

Requirements: API-02 (org_id + role tuple returned, no X-Org-Id needed),
API-10 (request.state.api_key_id wiring for the per-key rate limiter).
Plan 13-02 ships auth.api_key_dependencies.get_current_api_key.
"""

import contextlib

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

import auth.api_key_dependencies as dep_module
from auth.api_key_dependencies import get_current_api_key, require_role_api

pytestmark = pytest.mark.anyio


class _FakeConn:
    """Minimal asyncpg-connection stand-in returning a fixed fetchrow row."""

    def __init__(self, row):
        self._row = row

    async def fetchrow(self, *args, **kwargs):
        return self._row

    async def execute(self, *args, **kwargs):
        return "UPDATE 0"


class _FakePool:
    def __init__(self, row):
        self._row = row

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield _FakeConn(self._row)


def _patch_pool(monkeypatch, row):
    """Patch get_db_pool (as imported into the dep module) to return a fake pool.

    The 13-01 synthetic_api_key fixture row omits last_used_at (not part of its
    contract), but the real SELECT includes it and the dep reads row['last_used_at']
    for the debounced touch. Default it to None so the fake row matches the query.
    """
    if row is not None and "last_used_at" not in row:
        row = {**row, "last_used_at": None}

    async def _fake_get_db_pool():
        return _FakePool(row)

    monkeypatch.setattr(dep_module, "get_db_pool", _fake_get_db_pool)


def _build_app():
    """Tiny app exercising the real dep + a role-guarded route."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(
        request: Request,
        identity: tuple[str, str] = Depends(get_current_api_key),
    ):
        org_id, role = identity
        # request.state.api_key_id is written by the dep BEFORE it returns.
        return {
            "org_id": org_id,
            "role": role,
            "api_key_id": getattr(request.state, "api_key_id", None),
        }

    @app.get("/owner-only")
    async def owner_only(org_id: str = Depends(require_role_api("owner"))):
        return {"org_id": org_id}

    return app


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_returns_org_role_tuple(monkeypatch, synthetic_api_key):
    """API-02: get_current_api_key returns (org_id, role) matching get_active_org shape."""
    plaintext, _prefix, _h, row = synthetic_api_key
    _patch_pool(monkeypatch, row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get("/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_id"] == row["organization_id"]
    assert body["role"] == "owner"


async def test_request_state_api_key_id_set(monkeypatch, synthetic_api_key):
    """API-10 (F3): the dep writes request.state.api_key_id = str(row['id'])."""
    plaintext, _prefix, _h, row = synthetic_api_key
    _patch_pool(monkeypatch, row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get("/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200
    assert resp.json()["api_key_id"] == str(row["id"])


async def test_missing_auth_header_401(monkeypatch, synthetic_api_key):
    """No Authorization header -> 401."""
    _, _, _, row = synthetic_api_key
    _patch_pool(monkeypatch, row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get("/whoami")
    assert resp.status_code == 401


async def test_non_bearer_bw_header_401(monkeypatch, synthetic_api_key):
    """A header that is not 'Bearer bw_...' -> 401 before any DB lookup."""
    _, _, _, row = synthetic_api_key
    _patch_pool(monkeypatch, row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get("/whoami", headers={"Authorization": "Bearer not_a_key"})
    assert resp.status_code == 401


async def test_revoked_key_rejects(monkeypatch, synthetic_api_key):
    """API-03: a revoked key (WHERE revoked_at IS NULL -> no row) is rejected 401."""
    plaintext, _prefix, _h, _row = synthetic_api_key
    _patch_pool(monkeypatch, None)  # fetchrow returns None for revoked keys
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get("/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


async def test_bad_hash_rejects(monkeypatch, synthetic_api_key):
    """A row whose bcrypt_hash does not match the plaintext -> 401."""
    plaintext, prefix, _h, row = synthetic_api_key
    bad_row = dict(row)
    bad_row["bcrypt_hash"] = "0" * 64
    _patch_pool(monkeypatch, bad_row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get("/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


async def test_require_role_api_allows_owner(monkeypatch, synthetic_api_key):
    """require_role_api('owner') returns org_id when the caller is an owner."""
    plaintext, _prefix, _h, row = synthetic_api_key
    _patch_pool(monkeypatch, row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get(
            "/owner-only", headers={"Authorization": f"Bearer {plaintext}"}
        )
    assert resp.status_code == 200
    assert resp.json()["org_id"] == row["organization_id"]


async def test_require_role_api_rejects_member(monkeypatch, synthetic_api_key):
    """require_role_api('owner') raises 403 when the caller's role is 'member'."""
    plaintext, _prefix, _h, row = synthetic_api_key
    member_row = dict(row)
    member_row["role_at_creation"] = "member"
    _patch_pool(monkeypatch, member_row)
    app = _build_app()
    async with await _client(app) as ac:
        resp = await ac.get(
            "/owner-only", headers={"Authorization": f"Bearer {plaintext}"}
        )
    assert resp.status_code == 403
