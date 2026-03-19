"""RunPod GPU provider implementation.

Implements the GPUProvider ABC using RunPod's REST API via httpx.AsyncClient.
RunPod API reference: https://docs.runpod.io/serverless/references/operations

RunPod-specific notes:
- Submit: POST /v2/{endpoint_id}/run
- Status (also contains output): GET /v2/{endpoint_id}/status/{job_id}
- Cancel: POST /v2/{endpoint_id}/cancel/{job_id}
- Results are embedded in the status response output field — there is no separate
  results endpoint on RunPod. get_results() re-fetches the status endpoint.
"""

import httpx

from gpu.provider import GPUJobStatus, GPUJobSubmission, GPUProvider

RUNPOD_API_BASE = "https://api.runpod.ai/v2"


class RunPodProvider(GPUProvider):
    """GPUProvider implementation for RunPod serverless endpoints."""

    def __init__(self, api_key: str) -> None:
        """Initialize the provider with an API key.

        Args:
            api_key: RunPod API key. Sent as Bearer token in all requests.
        """
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    async def submit_job(self, submission: GPUJobSubmission) -> str:
        """Submit a job to a RunPod serverless endpoint.

        Args:
            submission: Contains endpoint_id, input_payload, and webhook_url.

        Returns:
            RunPod-assigned job ID string.

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        url = f"{RUNPOD_API_BASE}/{submission.endpoint_id}/run"
        payload = {
            "input": submission.input_payload,
            "webhook": submission.webhook_url,
        }
        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["id"]

    async def get_status(self, endpoint_id: str, provider_job_id: str) -> GPUJobStatus:
        """Get current status of a RunPod job.

        Args:
            endpoint_id: RunPod endpoint identifier.
            provider_job_id: Job ID returned by submit_job.

        Returns:
            GPUJobStatus with RunPod's status string and output (if terminal).

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        url = f"{RUNPOD_API_BASE}/{endpoint_id}/status/{provider_job_id}"
        response = await self._client.get(url)
        response.raise_for_status()
        data = response.json()
        return GPUJobStatus(
            provider_job_id=provider_job_id,
            status=data.get("status", "UNKNOWN"),
            output=data.get("output"),
        )

    async def cancel_job(self, endpoint_id: str, provider_job_id: str) -> None:
        """Cancel a running RunPod job.

        Args:
            endpoint_id: RunPod endpoint identifier.
            provider_job_id: Job ID returned by submit_job.

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        url = f"{RUNPOD_API_BASE}/{endpoint_id}/cancel/{provider_job_id}"
        response = await self._client.post(url)
        response.raise_for_status()

    async def get_results(self, endpoint_id: str, provider_job_id: str) -> dict:
        """Retrieve job output from RunPod.

        RunPod embeds results in the status response's 'output' field; there is
        no separate results endpoint. This method fetches the status endpoint and
        returns the output dict (empty dict if output is absent).

        Args:
            endpoint_id: RunPod endpoint identifier.
            provider_job_id: Job ID returned by submit_job.

        Returns:
            Output dict from the RunPod status response.

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        url = f"{RUNPOD_API_BASE}/{endpoint_id}/status/{provider_job_id}"
        response = await self._client.get(url)
        response.raise_for_status()
        data = response.json()
        return data.get("output", {})
