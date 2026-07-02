"""Integration tests for GET /api/v1/jobs/{job_id}.

Requirements: API-06 (inline metadata + ranked candidates + 24h presigned URLs).

Mock-based: get_db_pool patched, auth dep overridden, presigned-URL generator
stubbed. The jobs / job_candidates tables are not present in any reachable DB.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

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


@pytest.mark.anyio
async def test_get_returns_inline_presigned_urls():
    """API-06: GET /api/v1/jobs/{id} returns candidates with 24h presigned download_url."""
    now = datetime.datetime(2026, 6, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # First acquire: ownership check in get_job (SELECT organization_id).
    owner_conn = AsyncMock()
    owner_conn.fetchrow = AsyncMock(return_value={"organization_id": "org-1"})

    # Second acquire: serialize_job_with_candidates (job row + candidates).
    serialize_conn = AsyncMock()
    serialize_conn.fetchrow = AsyncMock(
        return_value={
            "id": "job-1",
            "tool": "rfdiffusion",
            "status": "complete",
            "name": "run-1",
            "created_at": now,
            "completed_at": now,
            "results": None,
            "organization_id": "org-1",
            "gpu_cost_usd": 1.25,
        }
    )
    serialize_conn.fetch = AsyncMock(
        return_value=[
            {"rank": 1, "pdb_key": "users/u/jobs/job-1/outputs/rank1.pdb", "scores": {"iptm": 0.8}},
            {"rank": 2, "pdb_key": "users/u/jobs/job-1/outputs/rank2.pdb", "scores": {"iptm": 0.7}},
        ]
    )

    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[_make_ctx(owner_conn), _make_ctx(serialize_conn)])

    def _fake_presigned(key, expires_in=3600):
        assert expires_in == 86400  # API-06: 24h URLs
        return f"https://test/presigned/{key}"

    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with (
            patch("api.v1.jobs.get_db_pool", return_value=pool),
            patch("jobs.serialize.generate_presigned_get_url", side_effect=_fake_presigned),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/jobs/job-1",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "job-1"
    assert body["status"] == "complete"
    assert len(body["candidates"]) == 2
    for c in body["candidates"]:
        assert c["download_url"].startswith("https://test/presigned/")
    # Ranked ascending.
    assert body["candidates"][0]["rank"] == 1


@pytest.mark.anyio
async def test_get_cross_org_returns_404():
    """API-06 / T-13-03: a job in another org returns 404 (no existence disclosure)."""
    owner_conn = AsyncMock()
    owner_conn.fetchrow = AsyncMock(return_value={"organization_id": "org-OTHER"})
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(owner_conn))

    app.dependency_overrides[get_current_api_key] = _override_api_key(org_id="org-1")
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/jobs/job-1",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
