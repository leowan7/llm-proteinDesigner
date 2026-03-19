"""Tests for RunPodProvider (GPUProvider implementation).

Covers:
- submit_job returns the provider job ID string
- cancel_job POSTs to the correct cancel URL
- get_status returns a GPUJobStatus with correct fields
- get_results returns the output dict from the RunPod status response

Implementation target: Plan 03-03.
"""

import pytest


class TestRunPodProvider:
    """Tests for the RunPod GPUProvider implementation."""

    def test_submit_job_returns_id(self):
        """Mock httpx.AsyncClient to intercept the POST to the RunPod run endpoint.
        Verify submit_job() returns the job ID string from the 'id' field of
        the RunPod response JSON.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_cancel_job_sends_post(self):
        """Mock httpx.AsyncClient to intercept the POST to the RunPod cancel endpoint.
        Verify cancel_job() sends a POST to
        https://api.runpod.ai/v2/{endpoint_id}/cancel/{provider_job_id}.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_get_status_returns_gpujobstatus(self):
        """Mock httpx.AsyncClient GET to the RunPod status endpoint.
        Verify get_status() returns a GPUJobStatus instance with:
        - provider_job_id matching the requested job ID
        - status matching the 'status' field from the RunPod response
        - output matching the 'output' field (or None if absent)

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_get_results_returns_output_dict(self):
        """Mock httpx.AsyncClient GET to the RunPod status endpoint.
        Verify get_results() returns the dict from the 'output' field of the
        RunPod response. RunPod does not have a separate results endpoint —
        output is embedded in the status response.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
