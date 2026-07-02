"""JobsResource — the jobs facade on the sync Client (Phase 13, Plan 13-04).

Path constants are FULL ``/api/v1/...`` strings matching the published OpenAPI
spec verbatim (the Plan 13-07 SDK⇄spec contract test asserts each appears in
``/api/openapi.json``). Collection paths carry a trailing slash because the
backend endpoints are ``@router.get("/")`` / ``@router.post("/")`` under a
prefixed router.
"""

from __future__ import annotations

from bindwave._pagination import iter_all as _iter_all
from bindwave._pagination import iter_all_async as _iter_all_async
from bindwave.types.job import Job, JobListPage

# Full published paths (must match app.openapi()['paths'] verbatim — D-16 / 13-07).
JOBS_PATH = "/api/v1/jobs/"
JOB_PATH = "/api/v1/jobs/{job_id}"
JOB_CANCEL_PATH = "/api/v1/jobs/{job_id}/cancel"

__all__ = ["JobsResource", "AsyncJobsResource", "JobListPage"]


class JobsResource:
    """Submit, fetch, list, and cancel design jobs."""

    def __init__(self, client) -> None:
        self._client = client

    def submit(
        self,
        *,
        tool: str,
        parameters: dict,
        name: str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """Submit a job. The Idempotency-Key header is auto-generated (uuid4 hex)
        when ``idempotency_key`` is not supplied."""
        body: dict = {"tool": tool, "parameters": parameters}
        if name is not None:
            body["name"] = name
        response = self._client._request(
            "POST", JOBS_PATH, json_body=body, idempotency_key=idempotency_key
        )
        job = Job.model_validate(response.json())
        job._client = self._client
        return job

    def get(self, job_id: str) -> Job:
        """Fetch a single job (inline candidates + presigned URLs when complete)."""
        response = self._client._request("GET", JOB_PATH.format(job_id=job_id))
        job = Job.model_validate(response.json())
        job._client = self._client
        return job

    def list(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
        status: str | None = None,
        tool: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> JobListPage:
        """List jobs (cursor-paginated, org-scoped server-side)."""
        params = {
            k: v
            for k, v in {
                "limit": limit,
                "cursor": cursor,
                "status": status,
                "tool": tool,
                "created_after": created_after,
                "created_before": created_before,
            }.items()
            if v is not None
        }
        response = self._client._request("GET", JOBS_PATH, params=params)
        data = response.json()
        jobs = []
        for j in data.get("data", []):
            job = Job.model_validate(j)
            job._client = self._client
            jobs.append(job)
        return JobListPage(data=jobs, next_cursor=data.get("next_cursor"))

    def cancel(self, job_id: str) -> Job:
        """Cancel a running or queued job."""
        response = self._client._request("POST", JOB_CANCEL_PATH.format(job_id=job_id))
        job = Job.model_validate(response.json())
        job._client = self._client
        return job

    def iter_all(self, **filters):
        """Lazily yield every :class:`Job` across all pages (walks the cursor).

        ``filters`` (e.g. ``status="complete"``) are forwarded to ``list`` on
        every page. Returns a generator — no page is loaded until iterated.
        """
        return _iter_all(self, **filters)


class AsyncJobsResource:
    """Async variant of :class:`JobsResource` — submit, fetch, list, cancel.

    Byte-for-byte mirror of the sync class modulo async/await; issues requests
    through ``await self._client._request(...)``.
    """

    def __init__(self, client) -> None:
        self._client = client

    async def submit(
        self,
        *,
        tool: str,
        parameters: dict,
        name: str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """Submit a job. The Idempotency-Key header is auto-generated (uuid4 hex)
        when ``idempotency_key`` is not supplied."""
        body: dict = {"tool": tool, "parameters": parameters}
        if name is not None:
            body["name"] = name
        response = await self._client._request(
            "POST", JOBS_PATH, json_body=body, idempotency_key=idempotency_key
        )
        job = Job.model_validate(response.json())
        job._client = self._client
        return job

    async def get(self, job_id: str) -> Job:
        """Fetch a single job (inline candidates + presigned URLs when complete)."""
        response = await self._client._request("GET", JOB_PATH.format(job_id=job_id))
        job = Job.model_validate(response.json())
        job._client = self._client
        return job

    async def list(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
        status: str | None = None,
        tool: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> JobListPage:
        """List jobs (cursor-paginated, org-scoped server-side)."""
        params = {
            k: v
            for k, v in {
                "limit": limit,
                "cursor": cursor,
                "status": status,
                "tool": tool,
                "created_after": created_after,
                "created_before": created_before,
            }.items()
            if v is not None
        }
        response = await self._client._request("GET", JOBS_PATH, params=params)
        data = response.json()
        jobs = []
        for j in data.get("data", []):
            job = Job.model_validate(j)
            job._client = self._client
            jobs.append(job)
        return JobListPage(data=jobs, next_cursor=data.get("next_cursor"))

    async def cancel(self, job_id: str) -> Job:
        """Cancel a running or queued job."""
        response = await self._client._request(
            "POST", JOB_CANCEL_PATH.format(job_id=job_id)
        )
        job = Job.model_validate(response.json())
        job._client = self._client
        return job

    def iter_all_async(self, **filters):
        """Lazily yield every :class:`Job` across all pages (async generator).

        ``filters`` are forwarded to ``list`` on every page. Returns an async
        generator — ``async for job in client.jobs.iter_all_async(): ...``.
        """
        return _iter_all_async(self, **filters)
