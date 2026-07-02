"""Tests for API key creation, format, verification, and revocation.

Requirements: API-01 (creation + hash-at-rest), API-03 (revocation).
Plan 13-02 ships auth.api_keys + auth.api_key_dependencies.
Plan 13-04 ships the web endpoint POST /user/api-keys.
"""

import datetime
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from auth import api_keys as api_keys_module
from auth.api_keys import generate_api_key, verify_api_key
from auth.api_key_dependencies import get_current_api_key
from auth.dependencies import get_current_user
from auth.org_dependencies import get_active_org
from config import settings
from main import app


# ---------------------------------------------------------------------------
# Mock-based endpoint helpers (mirror tests/api_v1/test_pagination.py). The
# api_keys table is not present in any reachable DB (supabase db push not run),
# so every endpoint test patches get_db_pool + overrides the auth dep.
# ---------------------------------------------------------------------------

_NOW = datetime.datetime(2026, 6, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _make_ctx(conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _pool_with_conn(conn):
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))
    return pool


def _override_active_org(org_id="org-A", role="owner"):
    async def _dep():
        return (org_id, role)

    return _dep


def _override_user(user_id="user-1"):
    async def _dep():
        return user_id

    return _dep


def _override_api_key(org_id="org-A", role="owner", api_key_id="key-1"):
    from fastapi import Request

    async def _dep(request: Request):
        request.state.api_key_id = api_key_id
        return (org_id, role)

    return _dep


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_returns_plaintext():
    """API-01: POST /user/api-keys returns plaintext once; GET omits it.

    Two mocked calls: (1) POST INSERT ... RETURNING id, created_at → response
    carries `plaintext` + `prefix`; (2) GET SELECT → response omits `plaintext`.
    """
    key_id = str(uuid.uuid4())

    # POST: INSERT ... RETURNING id, created_at
    post_conn = AsyncMock()
    post_conn.fetchrow = AsyncMock(return_value={"id": key_id, "created_at": _NOW})

    app.dependency_overrides[get_current_user] = _override_user()
    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        with patch("user.api_keys.get_db_pool", return_value=_pool_with_conn(post_conn)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    "/user/api-keys/",
                    json={"name": "my ci key"},
                    headers={"X-Org-Id": "org-A"},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        # Plaintext shown EXACTLY ONCE, alongside prefix.
        assert "plaintext" in body
        assert body["plaintext"].startswith("bw_live_")
        assert body["prefix"] == body["plaintext"][:12]
        assert body["id"] == key_id
        assert body["name"] == "my ci key"

        # GET on the same row NEVER returns plaintext.
        get_conn = AsyncMock()
        get_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": key_id,
                    "name": "my ci key",
                    "prefix": body["prefix"],
                    "created_at": _NOW,
                    "last_used_at": None,
                    "role_at_creation": "owner",
                }
            ]
        )
        with patch("user.api_keys.get_db_pool", return_value=_pool_with_conn(get_conn)):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                g = await client.get("/user/api-keys/", headers={"X-Org-Id": "org-A"})
        assert g.status_code == 200, g.text
        rows = g.json()
        assert isinstance(rows, list) and len(rows) == 1
        assert "plaintext" not in rows[0]
        assert rows[0]["prefix"] == body["prefix"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)


@pytest.mark.anyio
async def test_create_blank_name_returns_422():
    """API-01: a blank name is rejected by Pydantic (422) BEFORE the DB CHECK."""
    app.dependency_overrides[get_current_user] = _override_user()
    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/user/api-keys/",
                json={"name": ""},
                headers={"X-Org-Id": "org-A"},
            )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)


@pytest.mark.anyio
async def test_revoked_key_rejects():
    """API-03: revoking a key flips revoked_at; a second revoke is a no-op → 404.

    Mocks the UPDATE result string: "UPDATE 1" (revoked) then "UPDATE 0" (already
    revoked / not found), exercising the `revoked_at IS NULL` idempotency guard.
    """
    key_id = str(uuid.uuid4())

    conn_ok = AsyncMock()
    conn_ok.execute = AsyncMock(return_value="UPDATE 1")

    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        transport = ASGITransport(app=app)
        with patch("user.api_keys.get_db_pool", return_value=_pool_with_conn(conn_ok)):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r1 = await client.post(
                    f"/user/api-keys/{key_id}/revoke",
                    headers={"X-Org-Id": "org-A"},
                )
        assert r1.status_code == 200, r1.text
        assert r1.json()["id"] == key_id

        conn_noop = AsyncMock()
        conn_noop.execute = AsyncMock(return_value="UPDATE 0")
        with patch("user.api_keys.get_db_pool", return_value=_pool_with_conn(conn_noop)):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r2 = await client.post(
                    f"/user/api-keys/{key_id}/revoke",
                    headers={"X-Org-Id": "org-A"},
                )
        assert r2.status_code == 404
    finally:
        app.dependency_overrides.pop(get_active_org, None)


@pytest.mark.anyio
async def test_api_v1_list_org_scoped():
    """API-03: GET /api/v1/api-keys returns only the caller's org rows.

    The org filter lives in the SQL (`WHERE organization_id = $1`). We assert the
    org_id bound into the query is the caller's, and only that org's rows return.
    """
    org_a_rows = [
        {
            "id": str(uuid.uuid4()),
            "name": "key-a",
            "prefix": "bw_live_aaaa",
            "created_at": _NOW,
            "last_used_at": None,
        }
    ]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=org_a_rows)

    app.dependency_overrides[get_current_api_key] = _override_api_key(org_id="org-A")
    try:
        with patch("api.v1.api_keys.get_db_pool", return_value=_pool_with_conn(conn)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/api-keys/",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert [k["name"] for k in body["data"]] == ["key-a"]
        # org_id bound into the SELECT is the caller's org.
        assert conn.fetch.await_args.args[1] == "org-A"
        sql = conn.fetch.await_args.args[0]
        assert "WHERE organization_id = $1" in sql
        assert "revoked_at IS NULL" in sql
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)


@pytest.mark.anyio
async def test_revoke_404_cross_org():
    """API-03: revoking a key that belongs to a different org returns 404 problem+json.

    The UPDATE is org-scoped (`AND organization_id = $2`), so a cross-org key
    matches 0 rows → "UPDATE 0" → 404 (not 403; avoids existence disclosure).
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")

    app.dependency_overrides[get_current_api_key] = _override_api_key(org_id="org-A")
    try:
        with patch("api.v1.api_keys.get_db_pool", return_value=_pool_with_conn(conn)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/api/v1/api-keys/{uuid.uuid4()}/revoke",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        # org_id bound into the UPDATE is the caller's org (org-scoped guard).
        assert conn.execute.await_args.args[2] == "org-A"
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)


def test_generate_api_key_format():
    """API-01: generate_api_key returns plaintext matching bw_<env>_<random> format."""
    plaintext, prefix, h = generate_api_key("live")
    assert plaintext.startswith("bw_live_")
    assert re.match(r"^bw_live_[A-Za-z0-9_-]{30,32}$", plaintext), plaintext
    assert len(prefix) == 12
    assert prefix == plaintext[:12]
    assert len(h) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", h)

    # env="test" switches the second segment.
    test_plain, _, _ = generate_api_key("test")
    assert test_plain.startswith("bw_test_")


def test_verify_api_key_constant_time():
    """API-01: verify_api_key round-trips a matching pair and rejects mismatches."""
    plaintext, _prefix, h = generate_api_key("test")
    assert verify_api_key(plaintext, h) is True
    # Tamper with the plaintext -> hash no longer matches.
    assert verify_api_key(plaintext + "x", h) is False
    # Tamper with the stored hash -> mismatch.
    assert verify_api_key(plaintext, h[:-1] + ("0" if h[-1] != "0" else "1")) is False


def test_verify_api_key_empty_pepper_fails_closed(monkeypatch):
    """API-01: with both peppers empty, verify returns False (no auth bypass)."""
    plaintext, _prefix, h = generate_api_key("test")
    monkeypatch.setattr(settings, "api_key_pepper", "")
    monkeypatch.setattr(settings, "api_key_pepper_prev", "")
    assert verify_api_key(plaintext, h) is False


def test_pepper_rotation(monkeypatch):
    """API-01: a key hashed with the OLD pepper still verifies after rotation.

    Simulate rotation: the key was minted under the original pepper; the operator
    rotates api_key_pepper to a new value and moves the original into
    api_key_pepper_prev. verify_api_key must fall back to the prev pepper.
    """
    original_pepper = settings.api_key_pepper
    # Mint under the original (current test) pepper.
    plaintext, _prefix, old_hash = generate_api_key("test")

    # Rotate: new current pepper, original demoted to prev.
    monkeypatch.setattr(settings, "api_key_pepper", "rotated_new_pepper_value")
    monkeypatch.setattr(settings, "api_key_pepper_prev", original_pepper)

    # New pepper alone would not match the old hash...
    new_hash = generate_api_key("test")[2]
    assert new_hash != old_hash
    # ...but the prev-pepper fallback still verifies the old hash.
    assert verify_api_key(plaintext, old_hash) is True


def test_pepper_rotation_logs_prev_match(monkeypatch, caplog):
    """API-01: a prev-pepper match emits a WARNING for the rotation runbook."""
    import logging

    original_pepper = settings.api_key_pepper
    plaintext, _prefix, old_hash = generate_api_key("test")
    monkeypatch.setattr(settings, "api_key_pepper", "rotated_new_pepper_value")
    monkeypatch.setattr(settings, "api_key_pepper_prev", original_pepper)

    with caplog.at_level(logging.WARNING, logger=api_keys_module.__name__):
        assert verify_api_key(plaintext, old_hash) is True
    assert any("PREV pepper" in r.message for r in caplog.records)
