"""Tests for job result storage and interpretation (RESULT-03).

Covers:
- Job results JSONB contains next_steps guidance string
- zero_output flag is set correctly for BindCraft with 0 passing candidates

Implementation target: Plan 03-03.
"""

import pytest


class TestJobResults:
    """RESULT-03: Results include next_steps guidance and zero-output detection."""

    def test_next_steps_in_results_json(self):
        """Verify that the job results JSONB stored in jobs.results contains a
        non-empty 'next_steps' string field with actionable guidance for the scientist.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_zero_output_flag_set_for_bindcraft(self):
        """Verify that when a BindCraft job completes with candidate_count == 0,
        the JobResult has zero_output=True and the results JSONB reflects this.
        Zero-output is a meaningful outcome for BindCraft (no designs passed
        filters) rather than an error condition.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
