"""Typed job models (Phase 13, Plans 13-04 + 13-05).

The backend responses may carry extra fields the SDK doesn't model yet; each
model uses ``extra="ignore"`` so forward-compatible fields don't break parsing.

Plan 13-05 adds:
- a private ``_client`` back-reference on :class:`Job` (Pydantic ``PrivateAttr``,
  which is excluded from serialization by construction — the v2 idiom for a
  leading-underscore "Field(exclude=True)"), pinned by the resource methods so
  ``wait_until_complete`` / ``download_results`` can issue calls without taking a
  client kwarg;
- the ``wait_until_complete`` / ``await_until_complete`` polling helpers and the
  ``download_results`` / ``download_results_async`` result downloaders.

Serialization caveat (T-13-09, accept): ``Job.model_dump()`` / ``.model_dump_json()``
drop the ``_client`` back-reference. After deserializing a persisted Job, re-attach
a client via ``job._client = client`` before calling the convenience methods.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr


class JobStatus(str, Enum):
    """Lifecycle states of a job (matches the backend status vocabulary)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Terminal states — wait_until_complete stops polling when status is one of these.
TERMINAL_STATES = {JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED}


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
    as a full get response with inline candidates.

    ``_client`` is a private back-reference to the client that created this Job.
    It is a Pydantic ``PrivateAttr`` and therefore never serializes (the v2 idiom
    equivalent to ``Field(exclude=True)`` for a leading-underscore attribute).
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    id: str
    tool: str
    status: JobStatus
    name: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    organization_id: str | None = None
    gpu_cost_usd: float | None = None
    candidates: list[Candidate] = []

    # Private back-reference to the owning client (never serialized).
    _client: Any | None = PrivateAttr(default=None)

    def wait_until_complete(
        self, poll_every: int = 30, timeout: int | None = None
    ) -> "Job":
        """Poll ``client.jobs.get(self.id)`` until the job reaches a terminal
        status (``complete`` / ``failed`` / ``cancelled``), then return self
        (mutated in place with the fresh state, including inline candidates).

        Args:
            poll_every: seconds to sleep between polls (default 30). Pass 0 in
                tests to poll without waiting.
            timeout: raise ``TimeoutError`` once total elapsed >= this many
                seconds. ``None`` (default) means wait indefinitely.
        """
        import time

        start = time.monotonic()
        while self.status not in TERMINAL_STATES:
            if timeout is not None and time.monotonic() - start >= timeout:
                raise TimeoutError(
                    f"Job {self.id} did not complete within {timeout}s"
                )
            time.sleep(poll_every)
            fresh = self._client.jobs.get(self.id)
            self.__dict__.update(fresh.__dict__)
        return self

    async def await_until_complete(
        self, poll_every: int = 30, timeout: int | None = None
    ) -> "Job":
        """Async variant of :meth:`wait_until_complete` (uses ``asyncio.sleep``
        and awaits ``client.jobs.get``)."""
        import asyncio
        import time

        start = time.monotonic()
        while self.status not in TERMINAL_STATES:
            if timeout is not None and time.monotonic() - start >= timeout:
                raise TimeoutError(
                    f"Job {self.id} did not complete within {timeout}s"
                )
            await asyncio.sleep(poll_every)
            fresh = await self._client.jobs.get(self.id)
            self.__dict__.update(fresh.__dict__)
        return self

    def download_results(
        self, dest_dir: Path | str = Path("./")
    ) -> dict[int, Path]:
        """Download every candidate's PDB via its presigned ``download_url``.

        Each candidate is written to
        ``dest_dir/{self.id}-candidate-{rank}.pdb``; ``dest_dir`` is created if
        it does not exist. Returns ``{rank: Path}``.
        """
        import httpx

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        result: dict[int, Path] = {}
        for candidate in self.candidates:
            response = httpx.get(candidate.download_url)
            response.raise_for_status()
            path = dest / f"{self.id}-candidate-{candidate.rank}.pdb"
            path.write_bytes(response.content)
            result[candidate.rank] = path
        return result

    async def download_results_async(
        self, dest_dir: Path | str = Path("./")
    ) -> dict[int, Path]:
        """Async variant of :meth:`download_results` (uses ``httpx.AsyncClient``)."""
        import httpx

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        result: dict[int, Path] = {}
        async with httpx.AsyncClient() as http:
            for candidate in self.candidates:
                response = await http.get(candidate.download_url)
                response.raise_for_status()
                path = dest / f"{self.id}-candidate-{candidate.rank}.pdb"
                path.write_bytes(response.content)
                result[candidate.rank] = path
        return result


class JobListPage(BaseModel):
    """One page of a cursor-paginated job list. ``next_cursor`` is None on the
    final page."""

    model_config = ConfigDict(extra="ignore")

    data: list[Job]
    next_cursor: str | None = None
