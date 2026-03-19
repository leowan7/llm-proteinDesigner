"""Tests for job dispatch ordering and idempotency (BILL-04).

Covers:
- DB is updated to 'queued' before RunPod submit_job is called
- Job dispatch is idempotent: re-submitting a job with an existing runpod_job_id
  does not call submit_job again

Implementation target: Plan 03-03.
"""

import pytest


class TestJobDispatch:
    """BILL-04: Dispatch is DB-first and idempotent."""

    def test_db_before_runpod(self):
        """Mock both DB and RunPodProvider. Use unittest.mock call_args_list ordering
        to verify the DB update setting status='queued' happens BEFORE submit_job()
        is called. This ensures the job is tracked in the DB even if the process
        crashes between DB write and RunPod submission.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_idempotent_resubmit(self):
        """Set runpod_job_id in the DB before calling the worker task.
        Verify submit_job() is NOT called again — dispatch should detect the
        existing runpod_job_id and skip re-submission to prevent duplicate jobs.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
