"""Integration tests for cursor pagination on GET /api/v1/jobs.

Requirements: API-05 (opaque cursor, default limit 25, max 100, filters).

Mock-based: get_db_pool patched, auth dep overridden. The jobs table is not
present in any reachable DB. We assert (a) a full page yields a next_cursor,
(b) that cursor decodes to the last row's (created_at, id), and (c) the composite
keyset predicate + the decoded cursor bounds are passed to the query so a job
created after the cursor was taken cannot appear on the next page.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.v1.cursor import decode_cursor
from auth.api_key_dependencies import get_current_api_key
from main import app


def _make_ctx(conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _override_api_key(org_id="org-1", role="owner", api_key_id="key-1"):
    from fastapi import Request

    async def _dep(request: Request):
        request.state.api_key_id = api_key_id
        return (org_id, role)

    return _dep


def _row(i, ts):
    return {
        "id": f"00000000-0000-0000-0000-00000000000{i}",
        "tool": "rfdiffusion",
        "status": "complete",
        "name": f"job-{i}",
        "created_at": ts,
        "completed_at": ts,
        "gpu_cost_usd": 1.0,
        "organization_id": "org-1",
    }


@pytest.mark.anyio
async def test_cursor_stable_under_insert():
    """API-05: a full page returns a next_cursor bounding the next page below the last row."""
    base = datetime.datetime(2026, 6, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
    # limit=2, return exactly 2 rows (page full) -> next_cursor computed.
    rows = [_row(2, base - datetime.timedelta(minutes=1)), _row(1, base - datetime.timedelta(minutes=2))]

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))

    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/jobs/?limit=2",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 2
    assert body["next_cursor"] is not None

    # The cursor decodes to the LAST row's (created_at, id) — the keyset boundary.
    decoded = decode_cursor(body["next_cursor"])
    assert decoded is not None
    decoded_ts, decoded_id = decoded
    assert decoded_id == rows[-1]["id"]
    assert decoded_ts == rows[-1]["created_at"]

    # The keyset predicate + tiebreaker is in the SQL so newer same-timestamp rows
    # do not shift the page boundary.
    sql = conn.fetch.await_args.args[0]
    assert "(created_at, id) < ($6, $7::uuid)" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql


@pytest.mark.anyio
async def test_next_page_uses_cursor_bounds():
    """API-05: passing the cursor forwards its (created_at, id) into the query bounds."""
    base = datetime.datetime(2026, 6, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
    from api.v1.cursor import encode_cursor

    cursor = encode_cursor(base, "00000000-0000-0000-0000-000000000009")

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])  # empty second page
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))

    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    f"/api/v1/jobs/?limit=2&cursor={cursor}",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 200
    assert r.json()["next_cursor"] is None  # empty page -> no further cursor
    # $6 = cursor created_at, $7 = cursor id were bound into the query.
    call_args = conn.fetch.await_args.args
    assert call_args[6] == base  # cursor_created_at
    assert call_args[7] == "00000000-0000-0000-0000-000000000009"  # cursor_id


@pytest.mark.anyio
async def test_garbage_cursor_returns_400():
    """API-05: a garbage cursor returns 400 problem+json (never leaks other-org rows)."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))

    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/jobs/?cursor=not-a-real-cursor!!!",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
