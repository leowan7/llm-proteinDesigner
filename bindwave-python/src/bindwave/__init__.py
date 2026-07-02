"""bindwave — Bindwave Public API Python SDK.

Phase 13, Plan 13-02 bootstrap. This module defines the PUBLIC SURFACE CONTRACT
(``from bindwave import Client, ...`` resolves) but the classes are placeholders:
each raises ``NotImplementedError`` referencing the downstream plan that ships the
real implementation. Plans 13-04 and 13-05 replace these inline placeholders with
the real _client / _async_client / types / resource modules.
"""


# --- Exception hierarchy -----------------------------------------------------
class BindwaveError(Exception):
    """Base class for all bindwave SDK errors."""


class BindwaveAPIError(BindwaveError):
    """Raised for non-2xx API responses not covered by a more specific class."""


class BindwaveAuthError(BindwaveError):
    """Raised on 401/403 — invalid or revoked API key, or insufficient role."""


class BindwaveRateLimitError(BindwaveError):
    """Raised on 429 — per-key rate limit exceeded."""


class BindwaveValidationError(BindwaveError):
    """Raised on 422 — request failed server-side validation."""


class BindwaveJobError(BindwaveError):
    """Raised when a job fails or ends in a non-recoverable state."""


# --- Type placeholders -------------------------------------------------------
class JobStatus:
    """Placeholder — Plan 13-04/13-05 implements the typed JobStatus enum."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "bindwave 0.1.0 not yet implemented — see Plan 13-04/13-05"
        )


class Job:
    """Placeholder — Plan 13-04/13-05 implements the typed Job model."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "bindwave 0.1.0 not yet implemented — see Plan 13-04/13-05"
        )


class Candidate:
    """Placeholder — Plan 13-04/13-05 implements the typed Candidate model."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "bindwave 0.1.0 not yet implemented — see Plan 13-04/13-05"
        )


class ApiKey:
    """Placeholder — Plan 13-04/13-05 implements the typed ApiKey model."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "bindwave 0.1.0 not yet implemented — see Plan 13-04/13-05"
        )


# --- Clients -----------------------------------------------------------------
class Client:
    """Placeholder — Plan 13-04 implements the synchronous HTTP client."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "bindwave 0.1.0 not yet implemented — see Plan 13-04/13-05"
        )


class AsyncClient:
    """Placeholder — Plan 13-04 implements the async HTTP client."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "bindwave 0.1.0 not yet implemented — see Plan 13-04/13-05"
        )


__version__ = "0.1.0"
__all__ = [
    "Client",
    "AsyncClient",
    "BindwaveError",
    "BindwaveAuthError",
    "BindwaveRateLimitError",
    "BindwaveValidationError",
    "BindwaveJobError",
    "BindwaveAPIError",
    "Job",
    "JobStatus",
    "Candidate",
    "ApiKey",
]
