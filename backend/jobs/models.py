"""Pydantic models and enums for job status, stages, and results.

These are the type contracts for Phase 3 job execution. They are imported by:
- backend/worker/ (job dispatch and status updates)
- backend/webhooks/ (RunPod webhook handler)
- frontend (via API response schemas)

JobStatus and JobStage are distinct:
- JobStatus is the coarse machine state (queued/running/complete/failed/cancelled).
- JobStage is the human-readable progress label shown in the UI.
"""

from enum import Enum

from pydantic import BaseModel

from agent.jobspec import JobSpec  # noqa: F401 — re-exported for consumer convenience


class JobStatus(str, Enum):
    """Coarse machine state for a job. Stored in jobs.status column."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    """Human-readable progress stage shown in the UI during execution."""

    QUEUED = "Queued"
    INITIALIZING = "Initializing GPU"
    RUNNING_DIFFUSION = "Running diffusion"
    RUNNING_BINDING = "Running binding optimization"
    RUNNING_GENERATION = "Running structure generation"
    SCORING = "Scoring designs"
    COMPLETE = "Complete"


# Maps tool name to the dominant execution stage label shown during GPU work.
# Used by the webhook handler to set stage when a job transitions to RUNNING.
TOOL_STAGE_MAP: dict[str, JobStage] = {
    "rfdiffusion": JobStage.RUNNING_DIFFUSION,
    "rfantibody": JobStage.RUNNING_DIFFUSION,
    "bindcraft": JobStage.RUNNING_BINDING,
    "boltzgen": JobStage.RUNNING_GENERATION,
}


class JobStatusEvent(BaseModel):
    """Payload published to Redis and streamed via SSE to the frontend.

    Published on channel: job:{job_id}:status
    """

    job_id: str
    status: JobStatus
    stage: str                          # Human-readable stage label (from JobStage)
    gpu_seconds: int | None = None      # Present on terminal events
    error_category: str | None = None   # Present on FAILED events


class CandidateResult(BaseModel):
    """A single ranked design candidate from a completed job."""

    rank: int       # 1-indexed rank by composite score
    pdb_key: str    # MinIO object key for the PDB file
    scores: dict    # Tool-specific scores (pLDDT, ipTM, binding energy, etc.)


class JobResult(BaseModel):
    """Complete result payload stored in jobs.results JSONB column.

    Written by the webhook handler on job completion. Consumed by the
    results API endpoint and the frontend results page.
    """

    candidate_count: int
    candidates: list[CandidateResult]
    next_steps: str             # Plain-language guidance for the scientist
    zero_output: bool = False   # True when BindCraft returned 0 passing candidates
    gpu_seconds: int
    gpu_cost_usd: float
    runtime_minutes: int
