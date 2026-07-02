"""Asynchronous bindwave HTTP client (Phase 13, Plan 13-05).

A 1:1 async mirror of :mod:`bindwave._client`. Same auth header, env-var
fallbacks, base-url handling, retry policy, and ``Idempotency-Key`` auto-gen —
only the transport is ``httpx.AsyncClient`` and every IO method is ``async``
(``time.sleep`` → ``asyncio.sleep``).

Design (RESEARCH §2.5 / D-01, D-05), identical to the sync client:
- ``Authorization: Bearer {api_key}`` on every request; NO ``X-Org-Id``.
- Reads ``BINDWAVE_API_KEY`` / ``BINDWAVE_BASE_URL`` env vars.
- ``base_url`` defaults to the origin only; resource methods pass FULL
  ``/api/v1/...`` paths.
- 429 + 5xx auto-retry with exponential backoff capped at ``max_retries`` (5);
  ``Retry-After`` honored on 429.
- ``Idempotency-Key`` auto-generated (uuid4 hex) for ``POST /api/v1/jobs/`` when
  the caller doesn't supply one.
- Error responses (>= 400, after retries) route through ``parse_error_response``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from bindwave._exceptions import parse_error_response
from bindwave._idempotency import generate_idempotency_key

_DEFAULT_BASE_URL = "https://app.bindwave.com"
_MAX_BACKOFF_SECONDS = 30
__version__ = "0.1.0"


class AsyncClient:
    """Asynchronous client for the Bindwave public API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 5,
    ) -> None:
        api_key = api_key or os.environ.get("BINDWAVE_API_KEY")
        if not api_key:
            raise ValueError(
                "api_key is required (pass api_key= or set BINDWAVE_API_KEY env var)"
            )
        base_url = base_url or os.environ.get("BINDWAVE_BASE_URL") or _DEFAULT_BASE_URL

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"bindwave-python/{__version__}",
            },
        )

        # Resource facades. Imported here to avoid a circular import at module load.
        from bindwave.api_keys import AsyncApiKeysResource
        from bindwave.jobs import AsyncJobsResource

        self.jobs = AsyncJobsResource(self)
        self.api_keys = AsyncApiKeysResource(self)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
        retry_attempt: int = 0,
    ) -> httpx.Response:
        """Issue a request with retry + idempotency handling.

        Idempotency-Key is attached only for a job SUBMIT (POST to the /jobs
        collection with a body, not /cancel). It is threaded through retries so a
        replay reuses the same key.
        """
        headers: dict[str, Any] = {}
        is_job_submit = (
            method == "POST"
            and json_body is not None
            and path.endswith("/jobs/")
        )
        if is_job_submit:
            headers["Idempotency-Key"] = idempotency_key or generate_idempotency_key()

        try:
            response = await self._http.request(
                method, path, json=json_body, params=params, headers=headers
            )
        except httpx.NetworkError:
            if retry_attempt < self._max_retries:
                await asyncio.sleep(min(2**retry_attempt, _MAX_BACKOFF_SECONDS))
                return await self._request(
                    method,
                    path,
                    json_body=json_body,
                    params=params,
                    idempotency_key=headers.get("Idempotency-Key"),
                    retry_attempt=retry_attempt + 1,
                )
            raise

        # 429: honor Retry-After, capped.
        if response.status_code == 429 and retry_attempt < self._max_retries:
            wait = int(response.headers.get("Retry-After", "1") or "1")
            await asyncio.sleep(min(wait, _MAX_BACKOFF_SECONDS))
            return await self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                idempotency_key=headers.get("Idempotency-Key"),
                retry_attempt=retry_attempt + 1,
            )

        # 5xx: exponential backoff.
        if 500 <= response.status_code < 600 and retry_attempt < self._max_retries:
            await asyncio.sleep(min(2**retry_attempt, _MAX_BACKOFF_SECONDS))
            return await self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                idempotency_key=headers.get("Idempotency-Key"),
                retry_attempt=retry_attempt + 1,
            )

        if response.status_code >= 400:
            raise parse_error_response(response)

        return response

    async def aclose(self) -> None:
        """Close the underlying async HTTP transport."""
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
