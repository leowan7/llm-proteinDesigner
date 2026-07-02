"""JobsResource tests (Phase 13, Plan 13-04). respx mocks the /api/v1 surface.

The mock base_url is the client's origin; resource methods pass FULL /api/v1/...
paths, so the routes below match the published OpenAPI paths verbatim.
"""

import re

import httpx
import pytest
import respx

from bindwave import (
    BindwaveAPIError,
    BindwaveAuthError,
    BindwaveValidationError,
    Client,
    Job,
    JobListPage,
    JobStatus,
)

BASE = "https://api.bindwave.test"

_JOB_JSON = {
    "id": "j-1",
    "tool": "rfdiffusion",
    "status": "queued",
    "created_at": "2026-06-05T12:00:00+00:00",
}


def _client() -> Client:
    return Client(api_key="bw_test_x", base_url=BASE, max_retries=5)


@respx.mock
def test_submit_returns_job():
    respx.post(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(201, json=_JOB_JSON)
    )
    client = _client()
    job = client.jobs.submit(tool="rfdiffusion", parameters={"x": 1})
    assert isinstance(job, Job)
    assert job.id == "j-1"
    assert job.status is JobStatus.QUEUED
    client.close()


@respx.mock
def test_submit_auto_idempotency_key():
    route = respx.post(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(201, json=_JOB_JSON)
    )
    client = _client()
    client.jobs.submit(tool="rfdiffusion", parameters={})
    sent = route.calls.last.request
    key = sent.headers.get("Idempotency-Key")
    assert key is not None
    # uuid4().hex → 32 lowercase hex chars.
    assert re.fullmatch(r"[0-9a-f]{32}", key), key
    client.close()


@respx.mock
def test_submit_caller_idempotency_key():
    route = respx.post(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(201, json=_JOB_JSON)
    )
    client = _client()
    client.jobs.submit(tool="rfdiffusion", parameters={}, idempotency_key="my-key")
    assert route.calls.last.request.headers.get("Idempotency-Key") == "my-key"
    client.close()


@respx.mock
def test_get():
    respx.get(f"{BASE}/api/v1/jobs/j-1").mock(
        return_value=httpx.Response(200, json=_JOB_JSON)
    )
    client = _client()
    job = client.jobs.get("j-1")
    assert job.id == "j-1"
    client.close()


@respx.mock
def test_list():
    respx.get(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(
            200, json={"data": [_JOB_JSON], "next_cursor": "abc"}
        )
    )
    client = _client()
    page = client.jobs.list(limit=25)
    assert isinstance(page, JobListPage)
    assert len(page.data) == 1
    assert page.next_cursor == "abc"
    client.close()


@respx.mock
def test_cancel():
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
    job = client.jobs.cancel("j-1")
    assert job.status is JobStatus.CANCELLED
    # Cancel must NOT carry an Idempotency-Key (it is not a submit).
    assert "Idempotency-Key" not in respx.calls.last.request.headers
    client.close()


@respx.mock
def test_429_retries():
    route = respx.get(f"{BASE}/api/v1/jobs/j-1")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={}),
        httpx.Response(200, json=_JOB_JSON),
    ]
    client = _client()
    job = client.jobs.get("j-1")
    assert job.id == "j-1"
    assert route.call_count == 2
    client.close()


@respx.mock
def test_401_raises_BindwaveAuthError():
    respx.get(f"{BASE}/api/v1/jobs/j-1").mock(
        return_value=httpx.Response(
            401,
            headers={"content-type": "application/problem+json"},
            json={"type": "about:blank", "title": "Unauthorized", "detail": "Invalid API key"},
        )
    )
    client = _client()
    with pytest.raises(BindwaveAuthError) as exc:
        client.jobs.get("j-1")
    assert exc.value.status_code == 401
    client.close()


@respx.mock
def test_422_raises_BindwaveValidationError():
    respx.post(f"{BASE}/api/v1/jobs/").mock(
        return_value=httpx.Response(
            422,
            headers={"content-type": "application/problem+json"},
            json={
                "type": "validation-error",
                "title": "Unprocessable",
                "detail": "bad params",
                "errors": [{"field": "tool", "msg": "required"}],
            },
        )
    )
    client = _client()
    with pytest.raises(BindwaveValidationError) as exc:
        client.jobs.submit(tool="", parameters={})
    assert exc.value.errors == [{"field": "tool", "msg": "required"}]
    client.close()


@respx.mock
def test_5xx_retries_then_BindwaveAPIError():
    route = respx.get(f"{BASE}/api/v1/jobs/j-1").mock(
        return_value=httpx.Response(
            500,
            headers={"content-type": "application/problem+json"},
            json={"title": "Server Error", "detail": "boom"},
        )
    )
    # max_retries small so the test does not sleep through 2**n backoff for long.
    client = Client(api_key="bw_test_x", base_url=BASE, max_retries=2)
    with pytest.raises(BindwaveAPIError):
        client.jobs.get("j-1")
    # initial + 2 retries = 3 calls.
    assert route.call_count == 3
    client.close()
