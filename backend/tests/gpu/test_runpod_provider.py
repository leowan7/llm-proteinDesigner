"""Tests for RunPodProvider (GPUProvider implementation — quarantined rollback path).

Covers the Pod-based RunPod REST API (/v1/pods) that replaced the legacy
serverless endpoint API during the Modal migration (Phase 8):

- submit_job POSTs to /v1/pods and returns the pod ID from the response
- cancel_job DELETEs /v1/pods/{pod_id}
- get_status GETs /v1/pods/{pod_id} and reports desiredStatus + runtime
- get_results GETs /v1/pods/{pod_id} and returns the raw pod dict
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestRunPodProvider:
    """Tests for the RunPod GPUProvider implementation (quarantined)."""

    def _make_response(self, json_data: dict, status_code: int = 200):
        """Build a mock httpx response."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json = MagicMock(return_value=json_data)
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @pytest.mark.anyio
    async def test_submit_job_returns_id(self):
        """submit_job POSTs to /v1/pods and returns the pod id."""
        mock_response = self._make_response({"id": "rp-pod-abc123"})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        from gpu.provider import GPUJobSubmission
        from gpu.runpod import RunPodProvider

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        submission = GPUJobSubmission(
            endpoint_id="ghcr.io/leowan7/kendrew-rfdiffusion:v11",
            input_payload={"job_spec": {"tool": "rfdiffusion"}, "job_token": "jt"},
            webhook_url="http://localhost:8000/webhooks/runpod",
            policy={"tool": "rfdiffusion", "job_id": "job-uuid-1234abcd"},
        )
        pod_id = await provider.submit_job(submission)

        assert pod_id == "rp-pod-abc123"
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert call_url.endswith("/v1/pods")
        # Ensure the image name (endpoint_id) made it into the request body.
        body = mock_client.post.call_args[1]["json"]
        assert body["imageName"] == "ghcr.io/leowan7/kendrew-rfdiffusion:v11"

    @pytest.mark.anyio
    async def test_cancel_job_sends_delete(self):
        """cancel_job DELETEs /v1/pods/{pod_id} (pod terminate stops billing)."""
        mock_response = self._make_response({})
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)

        from gpu.runpod import RunPodProvider

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        await provider.cancel_job("", "rp-pod-456")

        mock_client.delete.assert_called_once()
        call_url = mock_client.delete.call_args[0][0]
        assert call_url.endswith("/v1/pods/rp-pod-456")

    @pytest.mark.anyio
    async def test_get_status_returns_gpujobstatus(self):
        """get_status GETs /v1/pods/{pod_id} and maps desiredStatus -> status."""
        mock_response = self._make_response({
            "id": "rp-pod-789",
            "desiredStatus": "RUNNING",
            "runtime": {"uptimeInSeconds": 120, "gpus": [{"id": "gpu-0"}]},
        })
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        from gpu.provider import GPUJobStatus
        from gpu.runpod import RunPodProvider

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.get_status("", "rp-pod-789")

        assert isinstance(result, GPUJobStatus)
        assert result.provider_job_id == "rp-pod-789"
        assert result.status == "RUNNING"
        assert result.output == {"uptimeInSeconds": 120, "gpus": [{"id": "gpu-0"}]}
        mock_client.get.assert_called_once()
        assert mock_client.get.call_args[0][0].endswith("/v1/pods/rp-pod-789")

    @pytest.mark.anyio
    async def test_get_results_returns_pod_dict(self):
        """get_results GETs /v1/pods/{pod_id} and returns the full pod payload.

        In the pod-based API, authoritative job results flow through the
        webhook path — get_results returns the pod metadata for admin-side
        inspection, not the candidate list.
        """
        pod_payload = {
            "id": "rp-pod-results",
            "desiredStatus": "EXITED",
            "runtime": {"uptimeInSeconds": 3600},
        }
        mock_response = self._make_response(pod_payload)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        from gpu.runpod import RunPodProvider

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.get_results("", "rp-pod-results")

        assert result == pod_payload
        mock_client.get.assert_called_once()
        assert mock_client.get.call_args[0][0].endswith("/v1/pods/rp-pod-results")
