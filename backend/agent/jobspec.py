"""JobSpec and ValidationResult Pydantic models.

These form the complete contract between the agent (Phase 2) and job dispatch (Phase 3).
A JobSpec is stored as JSONB in the jobs.job_spec column after the wizard completes.
"""

from typing import Literal

from pydantic import BaseModel


class ValidationResult(BaseModel):
    """Single pre-flight validation check result."""

    check_name: str                      # e.g. "hotspot_accessibility", "pdb_quality", "parameter_sanity"
    status: Literal["pass", "warn", "fail"]
    message: str                         # Human-readable explanation


class JobSpec(BaseModel):
    """Complete contract between agent (Phase 2) and job dispatch (Phase 3).

    Stored as JSONB in jobs.job_spec column. Created at wizard completion
    and validated before dispatch. Any 'fail' status in validation_results
    must block dispatch.
    """

    tool: Literal["rfdiffusion", "rfantibody", "bindcraft", "boltzgen", "pxdesign"]
    target_pdb_path: str                 # MinIO path: users/{uid}/jobs/{jid}/inputs/target.cif
    target_chain: str                    # e.g. "A"
    hotspot_residues: list[int]          # e.g. [45, 48, 52]; empty list if not applicable
    parameters: dict                     # Tool-specific; validated per tool in wizard.py
    validation_results: list[ValidationResult]
    estimated_cost_usd: float
    rationale: str                       # Plain-language explanation of tool choice
