"""Synchronous bindwave HTTP client (Phase 13, Plan 13-04).

Design (RESEARCH §2.5 / D-01, D-05):
- ``Authorization: Bearer {api_key}`` on every request; NO ``X-Org-Id`` (the org
  is resolved server-side from the key).
- Reads ``BINDWAVE_API_KEY`` env when ``api_key=`` is not passed; ``BINDWAVE_BASE_URL``
  overrides the base URL.
- ``base_url`` defaults to the origin only (``https://app.bindwave.com``). Resource
  methods pass FULL ``/api/v1/...`` paths (see jobs.py / api_keys.py PATHS), so the
  request path strings match ``/api/openapi.json`` verbatim — the Plan 13-07 SDK⇄spec
  contract test asserts every SDK path appears in the published spec.
- 429 + 5xx auto-retry with exponential backoff capped at ``max_retries`` (5);
  ``Retry-After`` honored on 429.
- ``Idempotency-Key`` auto-generated (uuid4 hex) for ``POST /api/v1/jobs/`` when the
  caller doesn't supply one.
- Error responses (>= 400, after retries) route through ``parse_error_response`` to
  the typed exception hierarchy.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from bindwave._exceptions import parse_error_response
from bindwave._idempotency import generate_idempotency_key

_DEFAULT_BASE_URL = "https://app.bindwave.com"
_MAX_BACKOFF_SECONDS = 30
__version__ = "0.1.0"


class Client:
    """Synchronous client for the Bindwave public API."""

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
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"bindwave-python/{__version__}",
            },
        )

        # Resource facades. Imported here to avoid a circular import at module load.
        from bindwave.api_keys import ApiKeysResource
        from bindwave.jobs import JobsResource

        self.jobs = JobsResource(self)
        self.api_keys = ApiKeysResource(self)

    def _request(
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
            response = self._http.request(
                method, path, json=json_body, params=params, headers=headers
            )
        except httpx.NetworkError:
            if retry_attempt < self._max_retries:
                time.sleep(min(2**retry_attempt, _MAX_BACKOFF_SECONDS))
                return self._request(
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
            time.sleep(min(wait, _MAX_BACKOFF_SECONDS))
            return self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                idempotency_key=headers.get("Idempotency-Key"),
                retry_attempt=retry_attempt + 1,
            )

        # 5xx: exponential backoff.
        if 500 <= response.status_code < 600 and retry_attempt < self._max_retries:
            time.sleep(min(2**retry_attempt, _MAX_BACKOFF_SECONDS))
            return self._request(
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

    def close(self) -> None:
        """Close the underlying HTTP transport."""
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
