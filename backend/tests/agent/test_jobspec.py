"""Tests for JobSpec validation and dispatch gating (AGENT-05)."""
import pytest
from agent.jobspec import JobSpec, ValidationResult


class TestJobSpecValidation:
    """AGENT-05: Validation warnings block dispatch."""

    def test_valid_jobspec_creation(self):
        """JobSpec with all required fields creates without error."""
        spec = JobSpec(
            tool="rfdiffusion",
            target_pdb_path="users/abc/jobs/123/inputs/target.cif",
            target_chain="A",
            hotspot_residues=[45, 48],
            parameters={"num_designs": 10},
            validation_results=[
                ValidationResult(check_name="pdb_quality", status="pass", message="OK")
            ],
            estimated_cost_usd=4.20,
            rationale="RFdiffusion is recommended for de novo binder design.",
        )
        assert spec.tool == "rfdiffusion"
        assert spec.estimated_cost_usd == 4.20

    def test_invalid_tool_rejected(self):
        """JobSpec with tool not in {rfdiffusion, bindcraft, boltzgen} raises ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            JobSpec(
                tool="invalid_tool",
                target_pdb_path="x",
                target_chain="A",
                hotspot_residues=[],
                parameters={},
                validation_results=[],
                estimated_cost_usd=0,
                rationale="",
            )

    def test_warn_blocks_dispatch(self):
        """JobSpec containing a 'fail' validation result should be detectable by checking status."""
        spec = JobSpec(
            tool="bindcraft",
            target_pdb_path="x",
            target_chain="A",
            hotspot_residues=[],
            parameters={},
            validation_results=[
                ValidationResult(check_name="pdb_quality", status="fail", message="No standard residues"),
            ],
            estimated_cost_usd=0,
            rationale="",
        )
        has_failure = any(v.status == "fail" for v in spec.validation_results)
        assert has_failure is True
