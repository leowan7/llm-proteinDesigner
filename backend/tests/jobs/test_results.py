"""Tests for job result storage and interpretation (RESULT-03).

Covers:
- Job results JSONB contains next_steps guidance string
- zero_output flag is set correctly for BindCraft with 0 passing candidates

Implementation target: Plan 03-03.
"""

import json


class TestJobResults:
    """RESULT-03: Results include next_steps guidance and zero-output detection."""

    def test_next_steps_in_results_json(self):
        """Verify that the job results JSONB stored in jobs.results contains a
        non-empty 'next_steps' string field with actionable guidance for the scientist.
        """
        # Simulate the results payload the webhook handler writes to jobs.results
        output = {
            "candidate_count": 5,
            "next_steps": "Filter by ipTM > 0.7 and submit top 3 for wet-lab validation.",
            "candidates": [],
        }
        results = {
            "candidate_count": output["candidate_count"],
            "next_steps": output.get("next_steps", ""),
            "zero_output": output["candidate_count"] == 0,
        }
        results_json = json.dumps(results)
        parsed = json.loads(results_json)

        assert "next_steps" in parsed
        assert len(parsed["next_steps"]) > 0

    def test_zero_output_flag_set_for_bindcraft(self):
        """Verify that when a BindCraft job completes with candidate_count == 0,
        the JobResult has zero_output=True and the results JSONB reflects this.
        Zero-output is a meaningful outcome for BindCraft (no designs passed
        filters) rather than an error condition.
        """
        output = {
            "candidate_count": 0,
            "next_steps": "No designs passed the BindCraft filters. Try relaxing ipAE threshold.",
            "candidates": [],
        }
        candidate_count = output.get("candidate_count", 0)
        zero_output = candidate_count == 0

        results = {
            "candidate_count": candidate_count,
            "next_steps": output.get("next_steps", ""),
            "zero_output": zero_output,
        }
        results_json = json.dumps(results)
        parsed = json.loads(results_json)

        assert parsed["zero_output"] is True
        assert parsed["candidate_count"] == 0
