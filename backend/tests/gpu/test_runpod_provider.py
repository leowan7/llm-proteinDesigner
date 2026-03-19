"""Tests for RunPodProvider (GPUProvider implementation).

Covers:
- submit_job returns the provider job ID string
- cancel_job POSTs to the correct cancel URL
- get_status returns a GPUJobStatus with correct fields
- get_results returns the output dict from the RunPod status response

Implementation target: Plan 03-03.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRunPodProvider:
    """Tests for the RunPod GPUProvider implementation."""

    def _make_response(self, json_data: dict, status_code: int = 200):
        """Build a mock httpx response."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json = MagicMock(return_value=json_data)
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @pytest.mark.anyio
    async def test_submit_job_returns_id(self):
        """Mock httpx.AsyncClient to intercept the POST to the RunPod run endpoint.
        Verify submit_job() returns the job ID string from the 'id' field of
        the RunPod response JSON.
        """
        mock_response = self._make_response({"id": "rp-abc123", "status": "IN_QUEUE"})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from gpu.provider import GPUJobSubmission
            from gpu.runpod import RunPodProvider

            provider = RunPodProvider(api_key="test-key")
            provider._client = mock_client

            submission = GPUJobSubmission(
                endpoint_id="ep-rfdiffusion",
                input_payload={"job_spec": {"tool": "rfdiffusion"}},
                webhook_url="http://localhost:8000/webhooks/runpod",
            )
            job_id = await provider.submit_job(submission)

        assert job_id == "rp-abc123"
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "ep-rfdiffusion" in call_url
        assert call_url.endswith("/run")

    @pytest.mark.anyio
    async def test_cancel_job_sends_post(self):
        """Mock httpx.AsyncClient to intercept the POST to the RunPod cancel endpoint.
        Verify cancel_job() sends a POST to
        https://api.runpod.ai/v2/{endpoint_id}/cancel/{provider_job_id}.
        """
        mock_response = self._make_response({})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        from gpu.runpod import RunPodProvider

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        await provider.cancel_job("ep-bindcraft", "rp-job-456")

        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "ep-bindcraft" in call_url
        assert "rp-job-456" in call_url
        assert "cancel" in call_url

    @pytest.mark.anyio
    async def test_get_status_returns_gpujobstatus(self):
        """Mock httpx.AsyncClient GET to the RunPod status endpoint.
        Verify get_status() returns a GPUJobStatus instance with:
        - provider_job_id matching the requested job ID
        - status matching the 'status' field from the RunPod response
        - output matching the 'output' field (or None if absent)
        """
        mock_response = self._make_response({
            "id": "rp-job-789",
            "status": "COMPLETED",
            "output": {"candidate_count": 3, "candidates": []},
        })
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        from gpu.runpod import RunPodProvider
        from gpu.provider import GPUJobStatus

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.get_status("ep-boltzgen", "rp-job-789")

        assert isinstance(result, GPUJobStatus)
        assert result.provider_job_id == "rp-job-789"
        assert result.status == "COMPLETED"
        assert result.output is not None
        assert result.output["candidate_count"] == 3

    @pytest.mark.anyio
    async def test_get_results_returns_output_dict(self):
        """Mock httpx.AsyncClient GET to the RunPod status endpoint.
        Verify get_results() returns the dict from the 'output' field of the
        RunPod response. RunPod does not have a separate results endpoint —
        output is embedded in the status response.
        """
        output_data = {
            "candidate_count": 5,
            "next_steps": "Validate top candidates by SPR.",
            "candidates": [{"rank": 1, "pdb_key": "users/u/jobs/j/design_001.pdb", "scores": {}}],
        }
        mock_response = self._make_response({
            "id": "rp-job-results",
            "status": "COMPLETED",
            "output": output_data,
        })
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        from gpu.runpod import RunPodProvider

        provider = RunPodProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.get_results("ep-rfantibody", "rp-job-results")

        assert result == output_data
        assert result["candidate_count"] == 5
        assert "next_steps" in result
