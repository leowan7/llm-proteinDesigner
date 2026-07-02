"""Cursor auto-paginator tests (Phase 13, Plan 13-05).

iter_all walks the cursor across mocked multi-page responses; the async variant
does the same via AsyncClient. respx mocks the /api/v1 surface; no real network.
"""

import httpx
import respx

from bindwave import AsyncClient, Client

BASE = "https://api.bindwave.test"


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "tool": "rfdiffusion",
        "status": "complete",
        "created_at": "2026-06-05T12:00:00+00:00",
    }


@respx.mock
def test_iter_all_walks_pages():
    """Two pages: first has next_cursor='abc', second has next_cursor=None.
    iter_all yields all items across both pages."""
    route = respx.get(f"{BASE}/api/v1/jobs/")
    route.side_effect = [
        httpx.Response(200, json={"data": [_job("j-1"), _job("j-2")], "next_cursor": "abc"}),
        httpx.Response(200, json={"data": [_job("j-3")], "next_cursor": None}),
    ]
    client = Client(api_key="bw_test_x", base_url=BASE)
    ids = [job.id for job in client.jobs.iter_all()]
    assert ids == ["j-1", "j-2", "j-3"]
    assert route.call_count == 2
    client.close()


@respx.mock
def test_iter_all_with_filters():
    """Filter kwargs propagate to the GET query params on every page."""
    route = respx.get(f"{BASE}/api/v1/jobs/")
    route.side_effect = [
        httpx.Response(200, json={"data": [_job("j-1")], "next_cursor": "abc"}),
        httpx.Response(200, json={"data": [_job("j-2")], "next_cursor": None}),
    ]
    client = Client(api_key="bw_test_x", base_url=BASE)
    ids = [job.id for job in client.jobs.iter_all(status="complete")]
    assert ids == ["j-1", "j-2"]
    # status filter present on every page; page 2 also carries the advanced cursor.
    for call in route.calls:
        assert call.request.url.params.get("status") == "complete"
    assert route.calls[1].request.url.params.get("cursor") == "abc"
    client.close()


@respx.mock
async def test_iter_all_async():
    """Async generator walks the cursor across pages via AsyncClient."""
    route = respx.get(f"{BASE}/api/v1/jobs/")
    route.side_effect = [
        httpx.Response(200, json={"data": [_job("j-1"), _job("j-2")], "next_cursor": "abc"}),
        httpx.Response(200, json={"data": [_job("j-3")], "next_cursor": None}),
    ]
    client = AsyncClient(api_key="bw_test_x", base_url=BASE)
    ids = [job.id async for job in client.jobs.iter_all_async()]
    assert ids == ["j-1", "j-2", "j-3"]
    assert route.call_count == 2
    await client.aclose()
