"""ETA + progress-event helpers for long-running jobs.

Phase 5 of the Modal migration. Backs the progress page's cost/time estimates
and milestone emission. Consumed by:

- The heartbeat webhook handler (``backend/webhooks/router.py``) — stamps
  ``eta_seconds`` on each SSE event.
- The session orchestrator (Phase 6) — decides whether a session has enough
  budget remaining to continue vs. marking the job complete early.
- The submit form — previews cost before user commits.

ETA model is intentionally simple: per-tool ``avg_time_per_design_seconds``
populated from the last 20 completed jobs (live job counts replace the default
on first hit). Refined hourly by a cron worker (not in this module).

The live-stats cache is a module-global dict updated by ``refresh_live_stats``
and read by ``eta_seconds()``. Thread-safety is fine because the cron-only
writer + all readers run in the same async event loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from db.connection import get_db_pool

logger = logging.getLogger(__name__)


# ---- Baseline averages (fallback when no live data available) ----------------
#
# Conservative defaults tuned to the GPU SKUs each tool runs on. Actual
# observations replace these on first refresh.
_DEFAULT_AVG_SECONDS_PER_DESIGN: dict[str, float] = {
    "rfdiffusion": 90.0,    # backbone + MPNN + AF2 per design on A10G
    "rfantibody": 360.0,    # RF2_ab per design on A100-40 is ~5-6 min
    "bindcraft": 400.0,     # trajectory on A100-80 (spike: 977s for 1 design)
    "boltzgen": 180.0,      # design on A100-40 ~2-3 min
    "pxdesign": 300.0,      # design on A100-80 ~5 min including AF2 filter
}

# Live overrides populated by ``refresh_live_stats``.
_LIVE_AVG_SECONDS_PER_DESIGN: dict[str, float] = {}


@dataclass
class ProgressSnapshot:
    """A frozen view of a job's progress, suitable for SSE emission.

    Mirrors the extended heartbeat payload the container POSTs (Phase 5 spec).
    """

    job_id: str
    stage: str
    designs_completed: int
    designs_total: int
    current_session: int
    sessions_estimated: int
    eta_seconds: int
    top_candidates_so_far: list[dict]  # [{rank, pdb_name, ipsae, plddt}, ...]


def eta_seconds(
    tool: str,
    designs_completed: int,
    designs_total: int,
    current_rate_seconds_per_design: float | None = None,
) -> int:
    """Estimate remaining runtime in seconds for the current session.

    Args:
        tool: Tool slug (e.g. ``"bindcraft"``).
        designs_completed: Designs done so far in this session.
        designs_total: Expected total designs for this session (the pilot
            preset or the full-design budget slice).
        current_rate_seconds_per_design: Override used when the current
            session is producing designs noticeably faster/slower than the
            historical average. Passed by the heartbeat handler when
            ``session_designs_per_hour`` differs from baseline by >20%.

    Returns:
        Integer seconds remaining. Zero if ``designs_completed >= designs_total``.
        A reasonable upper bound is never clamped here — caller can cap if needed.
    """
    if designs_completed >= designs_total:
        return 0

    # Resolution order: live rate from caller > live historical > hard baseline.
    per_design = (
        current_rate_seconds_per_design
        or _LIVE_AVG_SECONDS_PER_DESIGN.get(tool)
        or _DEFAULT_AVG_SECONDS_PER_DESIGN.get(tool, 180.0)
    )
    remaining = designs_total - designs_completed
    return int(remaining * per_design)


def sessions_estimated(
    total_budget_hours: int,
    max_session_hours: int = 23,
) -> int:
    """Number of sessions a full-design job is expected to spawn.

    Args:
        total_budget_hours: Declared budget (``jobs.total_budget_hours``).
        max_session_hours: Modal's per-function-call timeout cap. Default 23
            (matches the migration plan — 1hr of headroom under Modal's 24hr cap).

    Returns:
        Integer count >= 1. A 4hr budget returns 1 (single session);
        a 48hr budget returns 3 (23 + 23 + 2).
    """
    if total_budget_hours <= max_session_hours:
        return 1
    full_sessions, remainder = divmod(total_budget_hours, max_session_hours)
    return int(full_sessions + (1 if remainder else 0))


async def refresh_live_stats(ctx: dict | None = None) -> None:
    """Recompute ``_LIVE_AVG_SECONDS_PER_DESIGN`` from the last 20 jobs per tool.

    Called by a cron worker nightly. Safe to call ad-hoc (idempotent, read-only
    against ``jobs``). Falls back silently to the baked-in defaults on any
    error — the ETA remains usable even if stats are stale.
    """
    try:
        pool = await get_db_pool()
    except Exception as exc:
        logger.warning("progress.refresh_live_stats: DB pool unavailable: %s", exc)
        return

    query = """
        SELECT tool,
               AVG(
                   EXTRACT(EPOCH FROM (completed_at - started_at))
                   / NULLIF((results->>'candidate_count')::int, 0)
               ) AS avg_seconds_per_design
        FROM (
            SELECT job_spec::jsonb ->> 'tool' AS tool,
                   started_at,
                   completed_at,
                   results,
                   ROW_NUMBER() OVER (
                       PARTITION BY job_spec::jsonb ->> 'tool'
                       ORDER BY completed_at DESC
                   ) AS rn
            FROM public.jobs
            WHERE status = 'complete'
              AND started_at IS NOT NULL
              AND completed_at IS NOT NULL
              AND (results ->> 'candidate_count')::int > 0
        ) recent
        WHERE rn <= 20
        GROUP BY tool
        HAVING AVG(
            EXTRACT(EPOCH FROM (completed_at - started_at))
            / NULLIF((results->>'candidate_count')::int, 0)
        ) IS NOT NULL;
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query)
    except Exception as exc:
        logger.warning("progress.refresh_live_stats: query failed: %s", exc)
        return

    for row in rows:
        tool = row["tool"]
        avg_s = float(row["avg_seconds_per_design"] or 0)
        if tool and avg_s > 0:
            _LIVE_AVG_SECONDS_PER_DESIGN[tool] = avg_s

    logger.info(
        "progress.refresh_live_stats: updated tools=%s live_stats=%s",
        sorted(_LIVE_AVG_SECONDS_PER_DESIGN),
        _LIVE_AVG_SECONDS_PER_DESIGN,
    )


def snapshot_from_heartbeat(
    tool: str,
    heartbeat: dict,
    total_budget_hours: int = 4,
) -> ProgressSnapshot:
    """Build a ProgressSnapshot from the container's heartbeat payload.

    Args:
        tool: Tool slug.
        heartbeat: Extended heartbeat dict (see plan Phase 5 spec).
        total_budget_hours: From ``jobs.total_budget_hours``.

    Returns:
        A ProgressSnapshot ready for SSE emission.
    """
    designs_completed = int(heartbeat.get("designs_completed", 0))
    designs_total = int(heartbeat.get("designs_total", max(1, designs_completed)))
    sessions_est = sessions_estimated(total_budget_hours)

    return ProgressSnapshot(
        job_id=str(heartbeat.get("job_id", "")),
        stage=str(heartbeat.get("stage", "")),
        designs_completed=designs_completed,
        designs_total=designs_total,
        current_session=int(heartbeat.get("current_session", 1)),
        sessions_estimated=int(heartbeat.get("sessions_estimated", sessions_est)),
        eta_seconds=eta_seconds(tool, designs_completed, designs_total),
        top_candidates_so_far=list(heartbeat.get("top_candidates_so_far", [])),
    )
