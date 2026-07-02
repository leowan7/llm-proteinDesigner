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
