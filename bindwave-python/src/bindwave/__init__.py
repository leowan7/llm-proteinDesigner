"""bindwave — Bindwave Public API Python SDK.

Phase 13. Plan 13-04 ships the REAL synchronous ``Client`` plus the jobs +
api-keys resources, the typed models, and the exception hierarchy. ``AsyncClient``
remains a placeholder until Plan 13-05 ships it (calling it raises
``NotImplementedError``).
"""

from bindwave._client import Client
from bindwave._exceptions import (
    BindwaveAPIError,
    BindwaveAuthError,
    BindwaveError,
    BindwaveJobError,
    BindwaveRateLimitError,
    BindwaveValidationError,
    parse_error_response,
)
from bindwave._idempotency import generate_idempotency_key
from bindwave.api_keys import ApiKeysResource
from bindwave.jobs import JobListPage, JobsResource
from bindwave.types.api_key import ApiKey
from bindwave.types.job import Candidate, Job, JobStatus


class AsyncClient:
    """Placeholder — Plan 13-05 ships the asynchronous HTTP client."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "AsyncClient is not yet implemented — see Plan 13-05"
        )


__version__ = "0.1.0"
__all__ = [
    "Client",
    "AsyncClient",
    "JobsResource",
    "ApiKeysResource",
    "JobListPage",
    "BindwaveError",
    "BindwaveAuthError",
    "BindwaveRateLimitError",
    "BindwaveValidationError",
    "BindwaveJobError",
    "BindwaveAPIError",
    "parse_error_response",
    "generate_idempotency_key",
    "Job",
    "JobStatus",
    "Candidate",
    "ApiKey",
]
