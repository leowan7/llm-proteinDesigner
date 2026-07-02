"""Job.wait_until_complete / await_until_complete tests (Phase 13, Plan 13-05).

CRITICAL: these tests never sleep for real. time.sleep / asyncio.sleep are
monkeypatched to no-ops, poll_every=0 is used, and the TimeoutError case drives
a fake monotonic clock past the timeout so the loop exits instantly.
"""

import httpx
import pytest
import respx

from bindwave import AsyncClient, Client
from bindwave.types.job import Job, JobStatus

BASE = "https://api.bindwave.test"


def _job_json(status: str) -> dict:
    return {
        "id": "j-1",
        "tool": "rfdiffusion",
        "status": status,
        "created_at": "2026-06-05T12:00:00+00:00",
    }


def _seed_job(client, status: str = "queued") -> Job:
    """A Job with _client pinned, as a resource method would return it."""
    job = Job.model_validate(_job_json(status))
    job._client = client
    return job


@respx.mock
def test_wait_until_complete_returns_terminal_job(monkeypatch):
    """GET /jobs/j-1 returns queued → running → complete; wait_until_complete
    (poll_every=0, sleep patched out) returns the complete job."""
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    route = respx.get(f"{BASE}/api/v1/jobs/j-1")
    route.side_effect = [
        httpx.Response(200, json=_job_json("running")),
        httpx.Response(200, json=_job_json("complete")),
    ]
    client = Client(api_key="bw_test_x", base_url=BASE)
    job = _seed_job(client, "queued")
    result = job.wait_until_complete(poll_every=0)
    assert result is job
    assert result.status is JobStatus.COMPLETE
    assert route.call_count == 2  # polled twice: running, then complete
    client.close()


@respx.mock
def test_wait_until_complete_timeout(monkeypatch):
    """Job never leaves queued; a fake clock jumps past the timeout so the
    loop raises TimeoutError without any real waiting."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    # First monotonic() = start (0.0); second (the timeout check) = 5.0 > timeout=1.
    ticks = iter([0.0, 5.0])
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))
    respx.get(f"{BASE}/api/v1/jobs/j-1").mock(
        return_value=httpx.Response(200, json=_job_json("queued"))
    )
    client = Client(api_key="bw_test_x", base_url=BASE)
    job = _seed_job(client, "queued")
    with pytest.raises(TimeoutError):
        job.wait_until_complete(poll_every=0, timeout=1)
    client.close()


@respx.mock
async def test_await_until_complete(monkeypatch):
    """Async variant: queued → complete with asyncio.sleep patched out."""
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    route = respx.get(f"{BASE}/api/v1/jobs/j-1")
    route.side_effect = [
        httpx.Response(200, json=_job_json("running")),
        httpx.Response(200, json=_job_json("complete")),
    ]
    client = AsyncClient(api_key="bw_test_x", base_url=BASE)
    job = _seed_job(client, "queued")
    result = await job.await_until_complete(poll_every=0)
    assert result.status is JobStatus.COMPLETE
    assert route.call_count == 2
    await client.aclose()
