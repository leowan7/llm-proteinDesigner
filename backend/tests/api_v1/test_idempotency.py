"""Tests for the Stripe-style idempotency state machine (api/v1/idempotency.py).

Requirements: API-04 (idempotency replay, body mismatch 422, in-progress 409).
Schema reference: RESEARCH §2.9 (3-state lifecycle: pending | completed).

These are unit tests against the store functions with a mocked asyncpg conn — the
``api_key_idempotency`` table is not present in any reachable DB (13-01 Task 4 /
`supabase db push` pending), so we drive the 3-state logic by stubbing the conn's
fetchrow/execute return values. This exercises the exact decision inputs the
jobs router branches on (None / pending / body-mismatch / completed).
"""

from unittest.mock import AsyncMock

import pytest

from api.v1.idempotency import (
    canonicalize_body,
    hash_body,
    mark_complete,
    try_begin,
)


def test_canonicalize_body_is_sort_stable():
    """API-04: canonicalize_body sorts keys + strips whitespace for a stable hash."""
    assert canonicalize_body({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    # Reordered-but-same-content bodies canonicalize identically.
    assert canonicalize_body({"a": 1, "b": 2}) == canonicalize_body({"b": 2, "a": 1})


def test_hash_body_is_sha256_hex():
    """API-04: hash_body returns a 64-char sha256 hex digest."""
    h = hash_body({"a": 1})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    # Same canonical content -> same hash regardless of key order.
    assert hash_body({"a": 1, "b": 2}) == hash_body({"b": 2, "a": 1})


@pytest.mark.anyio
async def test_try_begin_claims_slot_returns_none():
    """API-04 (lifecycle step 1): a fresh key INSERTs 'pending' and returns None.

    ON CONFLICT DO NOTHING RETURNING status yields a row on insert -> caller
    proceeds to dispatch.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "pending"})  # INSERT succeeded

    result = await try_begin(conn, "key-id", "idem-1", "hash-1")

    assert result is None
    # Only the INSERT fetchrow ran; no second SELECT.
    assert conn.fetchrow.await_count == 1


@pytest.mark.anyio
async def test_try_begin_pending_returns_row():
    """API-04 (lifecycle step 3): existing pending row -> caller returns 409."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,  # INSERT ... ON CONFLICT DO NOTHING RETURNING -> None (conflict)
            {
                "status": "pending",
                "request_body_hash": "hash-1",
                "response_status": None,
                "response_body": None,
            },
        ]
    )

    existing = await try_begin(conn, "key-id", "idem-1", "hash-1")

    assert existing is not None
    assert existing["status"] == "pending"


@pytest.mark.anyio
async def test_try_begin_body_mismatch_row():
    """API-04 (lifecycle step 4): completed row with a different body hash -> 422."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,  # conflict
            {
                "status": "completed",
                "request_body_hash": "hash-OLD",
                "response_status": 201,
                "response_body": {"id": "job-1", "status": "queued"},
            },
        ]
    )

    existing = await try_begin(conn, "key-id", "idem-1", "hash-NEW")

    assert existing is not None
    assert existing["request_body_hash"] != "hash-NEW"


@pytest.mark.anyio
async def test_try_begin_replay_row():
    """API-04 (lifecycle step 5): completed row + matching body -> replay stored body."""
    stored = {"id": "job-1", "status": "queued"}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,  # conflict
            {
                "status": "completed",
                "request_body_hash": "hash-1",
                "response_status": 201,
                "response_body": stored,
            },
        ]
    )

    existing = await try_begin(conn, "key-id", "idem-1", "hash-1")

    assert existing is not None
    assert existing["status"] == "completed"
    assert existing["request_body_hash"] == "hash-1"
    assert existing["response_body"] == stored


@pytest.mark.anyio
async def test_mark_complete_updates_row():
    """API-04 (lifecycle step 2): mark_complete UPDATEs status + response fields."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    await mark_complete(conn, "key-id", "idem-1", 201, {"id": "job-1", "status": "queued"})

    assert conn.execute.await_count == 1
    sql = conn.execute.await_args.args[0]
    assert "status = 'completed'" in sql
    assert "response_status" in sql
    assert "response_body" in sql
    assert "completed_at = now()" in sql


# ---------------------------------------------------------------------------
# Router-level integration tests for POST /api/v1/jobs idempotency branches.
# These drive the full decision tree through HTTP with a mocked transaction conn.
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402

from auth.api_key_dependencies import get_current_api_key  # noqa: E402
from main import app  # noqa: E402


def _txn_conn_ctx(conn):
    """Wrap a conn as pool.acquire() ctx whose conn.transaction() is also a ctx."""
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    acq = AsyncMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    return acq


def _override_api_key(org_id="org-1", role="owner", api_key_id="key-1"):
    from fastapi import Request

    async def _dep(request: Request):
        request.state.api_key_id = api_key_id
        return (org_id, role)

    return _dep


async def _post_submit(conn):
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_txn_conn_ctx(conn))
    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/v1/jobs/",
                    json={"tool": "rfdiffusion", "parameters": {"x": 1}},
                    headers={
                        "Authorization": "Bearer bw_test_x",
                        "Idempotency-Key": "idem-1",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)


@pytest.mark.anyio
async def test_missing_idempotency_key_returns_400():
    """API-04: POST without Idempotency-Key -> 400 problem+json (bad-request)."""
    conn = AsyncMock()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_txn_conn_ctx(conn))
    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    "/api/v1/jobs/",
                    json={"tool": "rfdiffusion", "parameters": {"x": 1}},
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/bad-request")


@pytest.mark.anyio
async def test_pending_returns_409():
    """API-04 (lifecycle step 3): a pending row -> 409 idempotency-in-progress."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,  # try_begin INSERT ON CONFLICT DO NOTHING -> None (conflict)
            {  # try_begin SELECT existing
                "status": "pending",
                "request_body_hash": "whatever",
                "response_status": None,
                "response_body": None,
            },
        ]
    )
    r = await _post_submit(conn)
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/idempotency-in-progress")


@pytest.mark.anyio
async def test_body_mismatch_returns_422():
    """API-04 (lifecycle step 4): same key, different body -> 422 key-conflict."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,  # conflict
            {
                "status": "completed",
                "request_body_hash": "a-different-hash",
                "response_status": 201,
                "response_body": {"id": "job-old", "status": "queued"},
            },
        ]
    )
    r = await _post_submit(conn)
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/idempotency-key-conflict")


@pytest.mark.anyio
async def test_replay_returns_stored_response():
    """API-04 (lifecycle step 5): completed match -> replay stored body + replay header."""
    stored = {"id": "job-old", "status": "queued", "tool": "rfdiffusion"}
    # request_body_hash must equal hash_body({"tool":"rfdiffusion","parameters":{"x":1},"name":None}).
    from api.v1.idempotency import hash_body as _hb

    match_hash = _hb({"tool": "rfdiffusion", "parameters": {"x": 1}, "name": None})
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,  # conflict
            {
                "status": "completed",
                "request_body_hash": match_hash,
                "response_status": 201,
                "response_body": stored,
            },
        ]
    )
    r = await _post_submit(conn)
    assert r.status_code == 201
    assert r.headers.get("X-Idempotency-Replay") == "1"
    assert r.json() == stored
