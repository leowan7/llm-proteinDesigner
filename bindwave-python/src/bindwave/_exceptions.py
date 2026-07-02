"""Exception hierarchy for the bindwave SDK (Phase 13, Plan 13-04, D-16).

The backend returns RFC 7807 ``application/problem+json`` bodies. ``parse_error_response``
reads the response's content-type + status code and routes to the concrete
exception class, attaching the problem body, the ``type`` URL slug, and the
human-facing message.

Class hierarchy (all inherit ``BindwaveError``):
  BindwaveAuthError        401 — invalid / revoked key, or insufficient role
  BindwaveRateLimitError   429 — per-key rate limit exceeded (carries retry_after)
  BindwaveValidationError  400 / 422 — request failed validation (carries errors)
  BindwaveJobError         other 4xx — e.g. 402 Payment Required, 409 Conflict, 404
  BindwaveAPIError         5xx — server error
"""

from __future__ import annotations

from typing import Any

import httpx


class BindwaveError(Exception):
    """Base class for all bindwave SDK errors.

    Attributes:
        status_code: HTTP status of the failing response (0 when not applicable).
        type_url: the RFC 7807 ``type`` URL slug, if present.
        problem_body: the parsed problem+json body, if present.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        type_url: str | None = None,
        problem_body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.type_url = type_url
        self.problem_body = problem_body


class BindwaveAuthError(BindwaveError):
    """Raised on 401 — invalid or revoked API key, or insufficient role."""


class BindwaveRateLimitError(BindwaveError):
    """Raised on 429 — per-key rate limit exceeded.

    Attributes:
        retry_after: seconds to wait before retrying, from the ``Retry-After``
            header (None when the header is absent).
    """

    def __init__(self, message: str, *, retry_after: int | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class BindwaveValidationError(BindwaveError):
    """Raised on 400 / 422 — request failed server-side validation.

    Attributes:
        errors: the ``errors`` list from the problem body (empty when absent).
    """

    def __init__(self, message: str, *, errors: list | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.errors = errors or []


class BindwaveJobError(BindwaveError):
    """Raised for other 4xx (402 Payment Required, 404 Not Found, 409 Conflict)."""


class BindwaveAPIError(BindwaveError):
    """Raised for 5xx server errors."""


def parse_error_response(response: httpx.Response) -> BindwaveError:
    """Map an error response to the concrete SDK exception class.

    Reads ``application/problem+json`` bodies (RFC 7807) when present; otherwise
    falls back to a bare status-code message. Routing is by status code:
      401 → BindwaveAuthError
      429 → BindwaveRateLimitError (retry_after from the Retry-After header)
      400 / 422 → BindwaveValidationError (errors from the body)
      other 4xx → BindwaveJobError
      5xx → BindwaveAPIError
    """
    status = response.status_code
    content_type = response.headers.get("content-type", "")

    body: dict[str, Any] = {}
    if content_type.startswith("application/problem+json") or content_type.startswith(
        "application/json"
    ):
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}

    type_url = body.get("type")
    message = body.get("detail") or body.get("title") or str(status)
    common = {"status_code": status, "type_url": type_url, "problem_body": body}

    if status == 401:
        return BindwaveAuthError(message, **common)
    if status == 429:
        retry_after_hdr = response.headers.get("Retry-After")
        retry_after = int(retry_after_hdr) if retry_after_hdr else None
        return BindwaveRateLimitError(message, retry_after=retry_after, **common)
    if status in (400, 422):
        return BindwaveValidationError(message, errors=body.get("errors", []), **common)
    if 400 <= status < 500:
        return BindwaveJobError(message, **common)
    return BindwaveAPIError(message, **common)
