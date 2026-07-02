"""AsyncClient + async resource tests (Phase 13, Plan 13-05).

Mirrors test_client.py + test_jobs.py + test_api_keys.py as async variants.
respx mocks the /api/v1 surface; asyncio_mode=auto (pyproject) runs async test
functions without an explicit marker. No test performs a real network call or a
real sleep.
"""

import re

import httpx
import pytest
import respx

from bindwave import AsyncClient, AsyncApiKeysResource, AsyncJobsResource
from bindwave import BindwaveAuthError, Job, JobListPage, JobStatus

BASE = "https://api.bindwave.test"

_JOB_JSON = {
    "id": "j-1",
    "tool": "rfdiffusion",
    "status": "queued",
    "created_at": "2026-06-05T12:00:00+00:00",
}


def _client() -> AsyncClient:
    return AsyncClient(api_key="bw_test_x", base_url=BASE, max_retries=5)


def test_async_constructor_requires_api_key(monkeypatch):
    """No api_key kwarg and no BINDWAVE_API_KEY env → ValueError."""
    monkeypatch.delenv("BINDWAVE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        AsyncClient()


async def test_async_authorization_header_set():
    """The Authorization: Bearer header carries the api_key; X-Org-Id absent."""
    async with AsyncClient(api_key="bw_test_x", base_url=BASE) as client:
        assert client._http.headers["Authorization"] == "Bearer bw_test_x"
        assert "X-Org-Id" not in client._http.headers
        assert isinstance(client._http, httpx.AsyncClient)
        assert isinstance(client.jobs, AsyncJobsResource)
        assert isinstance(client.api_keys, AsyncApiKeysResource)


@respx.mock
async def test_async_submit():
    route = respx.post(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(201, json=_JOB_JSON)
    )
    client = _client()
    job = await client.jobs.submit(tool="rfdiffusion", parameters={"x": 1})
    assert isinstance(job, Job)
    assert job.id == "j-1"
    assert job.status is JobStatus.QUEUED
    # Auto Idempotency-Key on submit (uuid4 hex).
    key = route.calls.last.request.headers.get("Idempotency-Key")
    assert re.fullmatch(r"[0-9a-f]{32}", key), key
    await client.aclose()


@respx.mock
async def test_async_get():
    respx.get(f"{BASE}/api/v1/jobs/j-1").mock(
        return_value=httpx.Response(200, json=_JOB_JSON)
    )
    client = _client()
    job = await client.jobs.get("j-1")
    assert job.id == "j-1"
    await client.aclose()


@respx.mock
async def test_async_list():
    respx.get(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(
            200, json={"data": [_JOB_JSON], "next_cursor": "abc"}
        )
    )
    client = _client()
    page = await client.jobs.list(limit=25)
    assert isinstance(page, JobListPage)
    assert len(page.data) == 1
    assert page.next_cursor == "abc"
    await client.aclose()


@respx.mock
async def test_async_cancel():
    respx.post(f"{BASE}/api/v1/jobs/j-1/cancel").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "j-1",
                "tool": "rfdiffusion",
                "status": "cancelled",
                "created_at": "2026-06-05T12:00:00+00:00",
            },
        )
    )
    client = _client()
    job = await client.jobs.cancel("j-1")
    assert job.status is JobStatus.CANCELLED
    assert "Idempotency-Key" not in respx.calls.last.request.headers
    await client.aclose()


@respx.mock
async def test_async_api_keys_list():
    route = respx.get(f"{BASE}/api/v1/api-keys/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "k-1",
                        "name": "ci",
                        "prefix": "bw_live_aaaa",
                        "created_at": "2026-06-05T12:00:00+00:00",
                        "last_used_at": None,
                    }
                ]
            },
        )
    )
    client = _client()
    keys = await client.api_keys.list()
    assert len(keys) == 1
    assert keys[0].id == "k-1"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer bw_test_x"
    assert "X-Org-Id" not in req.headers
    await client.aclose()


@respx.mock
async def test_async_429_retries():
    route = respx.get(f"{BASE}/api/v1/jobs/j-1")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={}),
        httpx.Response(200, json=_JOB_JSON),
    ]
    client = _client()
    job = await client.jobs.get("j-1")
    assert job.id == "j-1"
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_async_401_raises_BindwaveAuthError():
    respx.get(f"{BASE}/api/v1/jobs/j-1").mock(
        return_value=httpx.Response(
            401,
            headers={"content-type": "application/problem+json"},
            json={"type": "about:blank", "title": "Unauthorized", "detail": "Invalid API key"},
        )
    )
    client = _client()
    with pytest.raises(BindwaveAuthError) as exc:
        await client.jobs.get("j-1")
    assert exc.value.status_code == 401
    await client.aclose()


async def test_async_context_manager():
    """async with yields the client and closes the transport on exit."""
    client = AsyncClient(api_key="bw_test_x", base_url=BASE)
    async with client as c:
        assert c is client
        assert c._http.is_closed is False
    assert client._http.is_closed is True
