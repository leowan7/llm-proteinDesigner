"""Typed job models (Phase 13, Plan 13-04).

The backend responses may carry extra fields the SDK doesn't model yet; each
model uses ``extra="ignore"`` so forward-compatible fields don't break parsing.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class JobStatus(str, Enum):
    """Lifecycle states of a job (matches the backend status vocabulary)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Candidate(BaseModel):
    """A single designed candidate returned inline on GET /jobs/{id}."""

    model_config = ConfigDict(extra="ignore")

    rank: int
    pdb_key: str
    scores: dict = {}
    download_url: str


class Job(BaseModel):
    """A design job. Fields beyond ``id``/``tool``/``status`` are optional so a
    lean submit response (``{id, status, tool, created_at}``) parses as cleanly
    as a full get response with inline candidates."""

    model_config = ConfigDict(extra="ignore")

    id: str
    tool: str
    status: JobStatus
    name: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    organization_id: str | None = None
    gpu_cost_usd: float | None = None
    candidates: list[Candidate] = []


class JobListPage(BaseModel):
    """One page of a cursor-paginated job list. ``next_cursor`` is None on the
    final page."""

    model_config = ConfigDict(extra="ignore")

    data: list[Job]
    next_cursor: str | None = None
