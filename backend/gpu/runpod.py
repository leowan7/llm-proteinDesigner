"""RunPod GPU provider implementation using the Pod REST API.

Implements the GPUProvider ABC using RunPod's Pod API via httpx.AsyncClient.
This replaces the previous serverless endpoint approach.

RunPod Pod API reference: https://docs.runpod.io/api-reference/pods
- Create:    POST /v1/pods
- Get:       GET  /v1/pods/{id}
- Stop:      POST /v1/pods/{id}/stop
- Terminate: DELETE /v1/pods/{id}

Key differences from serverless:
- No handler requirement — runs any Docker image
- No UID mapping issues — root access by default
- No image size limits — configurable container disk
- Must explicitly terminate pods to stop billing
"""

import json
import logging

import httpx

from gpu.provider import GPUJobStatus, GPUJobSubmission, GPUProvider

logger = logging.getLogger(__name__)

RUNPOD_API_BASE = "https://rest.runpod.io/v1"


class RunPodProvider(GPUProvider):
    """GPUProvider implementation for RunPod GPU Pods."""

    def __init__(self, api_key: str) -> None:
        """Initialize the provider with an API key.

        Args:
            api_key: RunPod API key. Sent as Bearer token in all requests.
        """
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    async def submit_job(self, submission: GPUJobSubmission) -> str:
        """Create a RunPod GPU Pod to execute a job.

        The pod runs a Docker image with the job configuration passed as
        environment variables. The container runs the pipeline, POSTs results
        to the webhook, then exits. The backend terminates the pod upon
        receiving the webhook.

        Args:
            submission: Contains image name, input payload, webhook URL,
                        and pod configuration in the policy dict.

        Returns:
            RunPod pod ID string.

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        pod_config = submission.policy or {}

        # Serialize the full job payload as a JSON env var for the container.
        env_vars = {
            "JOB_PAYLOAD": json.dumps(submission.input_payload),
            "WEBHOOK_URL": submission.webhook_url,
            "JOB_ID": pod_config.get("job_id", ""),
            "JOB_TOKEN": submission.input_payload.get("job_token", ""),
        }

        body = {
            "name": f"kendrew-{pod_config.get('tool', 'job')}-{pod_config.get('job_id', '')[:8]}",
            "imageName": submission.endpoint_id,  # Repurpose endpoint_id as image name
            "gpuTypeIds": pod_config.get("gpu_type_ids", ["NVIDIA RTX A6000"]),
            "gpuCount": 1,
            "containerDiskInGb": pod_config.get("container_disk_gb", 20),
            "volumeInGb": 0,  # Use network volume, not per-pod volume
            "volumeMountPath": "/workspace",
            "env": env_vars,
            "dockerStartCmd": ["python3", "/opt/run_pipeline.py"],
            "cloudType": "SECURE",
        }

        # Attach network volume if configured.
        network_volume_id = pod_config.get("network_volume_id")
        if network_volume_id:
            body["networkVolumeId"] = network_volume_id

        # Attach container registry credentials if configured.
        registry_auth_id = pod_config.get("container_registry_auth_id")
        if registry_auth_id:
            body["containerRegistryAuthId"] = registry_auth_id

        logger.info(
            "Creating RunPod pod: image=%s gpu=%s",
            body["imageName"],
            body["gpuTypeIds"],
        )

        response = await self._client.post(f"{RUNPOD_API_BASE}/pods", json=body)
        response.raise_for_status()
        data = response.json()
        pod_id = data["id"]
        logger.info("RunPod pod created: %s", pod_id)
        return pod_id

    async def get_status(self, endpoint_id: str, provider_job_id: str) -> GPUJobStatus:
        """Get current status of a RunPod pod.

        Args:
            endpoint_id: Not used for pods (kept for ABC compatibility).
            provider_job_id: Pod ID returned by submit_job.

        Returns:
            GPUJobStatus with pod's desiredStatus and runtime info.

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        response = await self._client.get(f"{RUNPOD_API_BASE}/pods/{provider_job_id}")
        response.raise_for_status()
        data = response.json()
        return GPUJobStatus(
            provider_job_id=provider_job_id,
            status=data.get("desiredStatus", "UNKNOWN"),
            output=data.get("runtime"),
        )

    async def cancel_job(self, endpoint_id: str, provider_job_id: str) -> None:
        """Terminate a RunPod pod (stops billing immediately).

        Args:
            endpoint_id: Not used for pods (kept for ABC compatibility).
            provider_job_id: Pod ID returned by submit_job.

        Raises:
            httpx.HTTPStatusError: If RunPod returns a non-2xx response.
        """
        logger.info("Terminating RunPod pod: %s", provider_job_id)
        response = await self._client.delete(f"{RUNPOD_API_BASE}/pods/{provider_job_id}")
        response.raise_for_status()
        logger.info("Pod terminated: %s", provider_job_id)

    async def terminate_pod(self, pod_id: str) -> None:
        """Terminate a pod by ID. Convenience alias for cancel_job.

        Args:
            pod_id: RunPod pod ID to terminate.
        """
        await self.cancel_job("", pod_id)

    async def get_results(self, endpoint_id: str, provider_job_id: str) -> dict:
        """Get pod info. Results come via webhook, not from pod status.

        Args:
            endpoint_id: Not used for pods.
            provider_job_id: Pod ID.

        Returns:
            Pod runtime info dict (not job results — those come via webhook).
        """
        response = await self._client.get(f"{RUNPOD_API_BASE}/pods/{provider_job_id}")
        response.raise_for_status()
        return response.json()

    async def list_pods(self) -> list[dict]:
        """List all active pods. Used for orphan cleanup.

        Returns:
            List of pod info dicts from RunPod API.
        """
        response = await self._client.get(f"{RUNPOD_API_BASE}/pods")
        response.raise_for_status()
        return response.json()
