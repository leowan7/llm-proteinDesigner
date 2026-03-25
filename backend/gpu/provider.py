"""Abstract base class defining the GPU provider interface.

All GPU compute providers (RunPod, Modal, etc.) implement this ABC.
This allows swapping providers without changing job dispatch logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GPUJobSubmission:
    """Input data required to submit a job to a GPU provider."""

    endpoint_id: str        # Provider-specific endpoint/function identifier
    input_payload: dict     # Tool-specific input data (JSON-serializable)
    webhook_url: str        # URL for the provider to POST status updates
    policy: dict | None = None  # Optional RunPod execution policy (e.g. executionTimeout)


@dataclass
class GPUJobStatus:
    """Status response returned by the GPU provider."""

    provider_job_id: str    # Provider-assigned job identifier
    status: str             # Provider-native status string (e.g. "IN_QUEUE", "COMPLETED")
    output: dict | None = None  # Present when status is terminal and job succeeded


class GPUProvider(ABC):
    """Abstract interface for GPU compute providers.

    Implementations must be async-safe. All methods use httpx.AsyncClient
    or provider SDKs that support async operation.
    """

    @abstractmethod
    async def submit_job(self, submission: GPUJobSubmission) -> str:
        """Submit a job to the provider and return the provider job ID.

        Args:
            submission: Endpoint, payload, and webhook URL for the job.

        Returns:
            Provider-assigned job ID string (used for status polling / cancellation).
        """

    @abstractmethod
    async def get_status(self, endpoint_id: str, provider_job_id: str) -> GPUJobStatus:
        """Retrieve the current status of a submitted job.

        Args:
            endpoint_id: Provider-specific endpoint identifier.
            provider_job_id: Job ID returned by submit_job.

        Returns:
            GPUJobStatus with provider-native status and optional output dict.
        """

    @abstractmethod
    async def cancel_job(self, endpoint_id: str, provider_job_id: str) -> None:
        """Request cancellation of a running job.

        Args:
            endpoint_id: Provider-specific endpoint identifier.
            provider_job_id: Job ID returned by submit_job.
        """

    @abstractmethod
    async def get_results(self, endpoint_id: str, provider_job_id: str) -> dict:
        """Retrieve the output dict for a completed job.

        For providers (e.g. RunPod) that embed results in the status response,
        this method re-fetches the status endpoint and extracts the output field.
        For providers with a dedicated results endpoint (e.g. Modal), it calls
        that endpoint directly.

        Args:
            endpoint_id: Provider-specific endpoint identifier.
            provider_job_id: Job ID returned by submit_job.

        Returns:
            Provider-native output dict (structure is tool-dependent).
        """
