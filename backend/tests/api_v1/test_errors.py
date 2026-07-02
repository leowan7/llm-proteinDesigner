"""Integration tests for RFC 7807 problem+json error responses on /api/v1/* paths.

Requirements: API-07 (application/problem+json on /api/v1/*; web-flow keeps existing shape).

All tests are mock-based: the api_keys / jobs tables are not present in any
reachable DB (supabase db push pending), so get_db_pool is patched and the auth
dep is overridden. This exercises the exception-handler branching without a live
schema.
"""

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
    """Override get_current_api_key; also stamps request.state.api_key_id."""
    from fastapi import Request

    async def _dep(request: Request):
        request.state.api_key_id = api_key_id
        return (org_id, role)

    return _dep


@pytest.mark.anyio
async def test_problem_json():
    """API-07: a 404 on /api/v1/* returns application/problem+json with all 5 keys."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no job row -> 404
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))

    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        with patch("api.v1.jobs.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/jobs/nonexistent",
                    headers={"Authorization": "Bearer bw_test_x"},
                )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in body, f"missing {key} in {body}"
    assert body["type"].startswith("https://bindwave.com/errors/")
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/jobs/nonexistent"


@pytest.mark.anyio
async def test_validation_error_problem_json():
    """API-07: a RequestValidationError on /api/v1/* returns 422 problem+json + errors[]."""
    app.dependency_overrides[get_current_api_key] = _override_api_key()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Missing required 'tool' + 'parameters' -> Pydantic validation error.
            r = await client.post(
                "/api/v1/jobs/",
                json={"name": "no-tool"},
                headers={
                    "Authorization": "Bearer bw_test_x",
                    "Idempotency-Key": "idem-validate",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_api_key, None)

    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/validation-error")
    assert isinstance(body["errors"], list) and body["errors"]
    for e in body["errors"]:
        assert {"loc", "msg", "type"} <= set(e.keys())


@pytest.mark.anyio
async def test_web_flow_regression():
    """API-07: errors on non-/api/v1/* paths keep the default FastAPI JSON shape.

    A 404 raised inside a web-flow route (/jobs/{id}/cancel) must return
    Content-Type: application/json with the {"detail": ...} shape, NOT problem+json.
    """
    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org

    async def _u():
        return "user-abc"

    async def _org():
        return ("org-1", "scientist")

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no running job -> 404
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))

    app.dependency_overrides[get_current_user] = _u
    app.dependency_overrides[get_active_org] = _org
    try:
        with patch("jobs.router.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    "/jobs/web-job-1/cancel",
                    cookies={"access_token": "fake-token"},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)

    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "problem+json" not in r.headers["content-type"]
    body = r.json()
    assert set(body.keys()) == {"detail"}
