"""Tests for job cancellation (JOB-03).

Covers:
- DB status updated to 'cancelled' on cancellation
- RunPodProvider.cancel_job called with correct args
- Partial billing recorded for GPU seconds consumed before cancellation

Implementation target: Plan 03-03.
"""

import pytest


class TestJobCancellation:
    """JOB-03: Jobs can be cancelled mid-execution with correct cleanup."""

    def test_cancel_updates_db_status(self):
        """Verify that cancelling a job sets status='cancelled' in the jobs table.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_cancel_calls_runpod_cancel(self):
        """Mock RunPodProvider and verify cancel_job() is called with the correct
        endpoint_id and provider_job_id matching the job's runpod_job_id column.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_cancel_records_partial_billing(self):
        """Verify record_gpu_usage is called with the partial GPU seconds consumed
        before cancellation (not zero, not the full estimated duration).

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
