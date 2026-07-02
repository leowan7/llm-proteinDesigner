"""bindwave — Bindwave Public API Python SDK.

Phase 13. Plan 13-04 ships the REAL synchronous ``Client`` plus the jobs +
api-keys resources, the typed models, and the exception hierarchy. Plan 13-05
ships the REAL asynchronous ``AsyncClient``, the cursor auto-paginator
(``iter_all`` / ``iter_all_async``), and the ``Job.wait_until_complete`` /
``Job.download_results`` convenience methods.
"""

from bindwave._async_client import AsyncClient
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
from bindwave._pagination import iter_all, iter_all_async
from bindwave.api_keys import ApiKeysResource, AsyncApiKeysResource
from bindwave.jobs import AsyncJobsResource, JobListPage, JobsResource
from bindwave.types.api_key import ApiKey
from bindwave.types.job import Candidate, Job, JobStatus

__version__ = "0.1.0"
__all__ = [
    "Client",
    "AsyncClient",
    "JobsResource",
    "AsyncJobsResource",
    "ApiKeysResource",
    "AsyncApiKeysResource",
    "JobListPage",
    "iter_all",
    "iter_all_async",
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
