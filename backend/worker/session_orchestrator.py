"""Session orchestration for chunked full-design jobs (Phase 6).

A full-design binder campaign can run 24–96 hours. Modal caps each
``@app.function`` call at 24 hr, so campaigns >23hr run as multiple sessions
with resume state in a per-job ``modal.Volume`` between them.

This module manages the session lifecycle:

1. The worker (``run_job`` in ``worker/tasks.py``) spawns session 0 directly
   for any job. For ``job_tier == "pilot"`` the first session IS the whole
   job and chunking never kicks in.
2. The container's ``run_pipeline.py`` observes ``SESSION_DEADLINE_UNIX``.
   If the deadline approaches mid-run, it finishes the in-flight unit
   (trajectory / design), writes ``/state/checkpoint.json``, exits 0 with
   ``chunk_status="paused_for_resume"`` in the webhook payload.
3. The webhook handler (``webhooks/router.py``) dispatches on ``chunk_status``
   and calls ``enqueue_resume_session(job_id)`` here when appropriate.
4. That enqueues a new arq task that spawns session N+1 with
   ``RESUME_STATE_PATH=/state`` pointing into the same Volume.

Pilot jobs never touch this path — they're single-shot. Budget-check + fail
still routes through here for uniformity of the progress UI (single "session 1
of 1" timeline segment on the page).

See .claude/plans/i-have-been-building-typed-whistle.md Phase 6.
"""

from __future__ import annotations

import json
import logging
import time

from arq import create_pool as arq_create_pool
from arq.connections import RedisSettings

from config import settings
from db.connection import get_db_pool
from gpu import endpoint_for_tool, get_provider
from gpu.provider import GPUJobSubmission
from jobs.models import TOOL_STAGE_MAP
from pipelines import PIPELINE_MAP
from storage.client import ensure_pdb_in_s3, generate_presigned_get_url

logger = logging.getLogger(__name__)


# Modal function-call timeout cap in seconds. 23 hr = 82800s. 1 hr of headroom
# below Modal's hard 24hr cap so our container has time to checkpoint and exit.
MODAL_MAX_SESSION_SECONDS = 23 * 3600

# Seconds of grace the container gets to finish the in-flight unit + checkpoint
# after SESSION_DEADLINE_UNIX passes. Container should exit before the Modal
# timeout fires.
CHECKPOINT_GRACE_SECONDS = 900  # 15 min


async def spawn_session(
    ctx: dict,
    job_id: str,
    session_index: int = 0,
) -> None:
    """Spawn (or resume) one Modal session for a job.

    Called by ``worker.tasks.run_job`` for session 0 and by the webhook
    handler for session_index>=1 when the previous session paused for resume.

    Args:
        ctx: arq task context (unused; accepted for signature parity).
        job_id: UUID of the job to spawn a session for.
        session_index: 0 for the first session; N for subsequent resumes.
    """
    pool = await get_db_pool()
    provider = get_provider()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT job_spec, user_id, job_token,
                      COALESCE(job_tier, 'pilot') AS job_tier,
                      COALESCE(total_budget_hours, 4) AS total_budget_hours,
                      COALESCE(hours_consumed, 0) AS hours_consumed,
                      COALESCE(session_count, 0) AS session_count
               FROM public.jobs
               WHERE id = $1""",
            job_id,
        )

    if not row:
        logger.warning("session_orchestrator.spawn_session: job %s not found", job_id)
        return

    spec_data = json.loads(row["job_spec"])
    tool = spec_data["tool"]
    spec_data["job_tier"] = row["job_tier"]
    spec_data["total_budget_hours"] = int(row["total_budget_hours"])

    remaining_hours = float(row["total_budget_hours"]) - float(row["hours_consumed"])
    if remaining_hours <= 0:
        logger.info(
            "session_orchestrator.spawn_session: job %s budget exhausted "
            "(consumed=%.2fhr / budget=%dhr); no further sessions.",
            job_id, row["hours_consumed"], row["total_budget_hours"],
        )
        return

    # Compute session timeout: min(remaining, 23hr) — 15 min headroom.
    remaining_seconds = int(remaining_hours * 3600)
    session_timeout = min(remaining_seconds, MODAL_MAX_SESSION_SECONDS)
    session_deadline_unix = int(time.time()) + max(60, session_timeout - CHECKPOINT_GRACE_SECONDS)

    # Look up the pipeline for presigned URL expiry.
    pipeline = PIPELINE_MAP[tool]
    url_expiry = max(pipeline.presigned_url_expiry_seconds, session_timeout + 3600)

    # Ensure target PDB is in S3 (idempotent: returns key unchanged if already
    # uploaded). Session 0 normally handles this in worker.tasks.run_job, but
    # we call it here too so resume sessions are self-healing if the DB row
    # somehow still has a local path.
    try:
        s3_key = ensure_pdb_in_s3(
            spec_data["target_pdb_path"], user_id=str(row["user_id"]), job_id=job_id,
        )
    except Exception:
        logger.exception(
            "session_orchestrator.spawn_session: ensure_pdb_in_s3 failed for job %s", job_id,
        )
        return

    if s3_key != spec_data["target_pdb_path"]:
        spec_data["target_pdb_path"] = s3_key
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.jobs SET job_spec = $1 WHERE id = $2",
                json.dumps(spec_data),
                job_id,
            )

    # Presigned GET for the input target (may be reused across sessions —
    # generate a fresh URL each time to avoid expiry issues on day 3).
    input_pdb_url = generate_presigned_get_url(s3_key, expires_in=url_expiry)

    # State volume mount path is fixed at /state inside the container. Each
    # session reuses the same Modal Volume so checkpoints persist.
    resume_state_path = "/state" if session_index > 0 else ""

    try:
        endpoint_id = endpoint_for_tool(tool)
    except ValueError as exc:
        logger.error(
            "session_orchestrator.spawn_session: no endpoint for tool=%s: %s",
            tool, exc,
        )
        return

    submission = GPUJobSubmission(
        endpoint_id=endpoint_id,
        input_payload={
            "job_spec": spec_data,
            "input_presigned_url": input_pdb_url,
            "job_token": row["job_token"],
            "upload_urls_endpoint": f"{settings.app_base_url}/jobs/{job_id}/upload-urls",
        },
        webhook_url=f"{settings.app_base_url}/webhooks/runpod",
        policy={
            "job_id": job_id,
            "tool": tool,
            "job_tier": row["job_tier"],
            "total_budget_hours": int(row["total_budget_hours"]),
            "session_index": session_index,
            "session_deadline_unix": session_deadline_unix,
            "resume_state_path": resume_state_path,
        },
    )

    # Any dispatch failure here must fail the job in the DB + publish SSE so
    # the UI flips out of "running". Same contract as run_job in worker/tasks.
    try:
        provider_job_id = await provider.submit_job(submission)
    except Exception as exc:
        logger.exception(
            "session_orchestrator: provider.submit_job failed for job %s session %d",
            job_id, session_index,
        )
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE public.jobs
                   SET status = 'failed',
                       stage = 'Failed to dispatch',
                       error_category = $2,
                       completed_at = NOW(),
                       updated_at = NOW()
                   WHERE id = $1""",
                job_id,
                f"Dispatch failed: {type(exc).__name__}: {exc}",
            )
        # Publish a failure status event so the SSE stream flips the UI.
        import json as _json
        import redis.asyncio as _aioredis
        _r = _aioredis.from_url(settings.redis_url)
        try:
            await _r.publish(
                f"job:{job_id}:status",
                _json.dumps({"job_id": job_id, "status": "failed", "stage": "Failed to dispatch"}),
            )
        finally:
            await _r.aclose()
        return

    logger.info(
        "session_orchestrator: spawned session %d for job %s (provider_job_id=%s, "
        "budget_remaining=%.2fhr, timeout=%ds)",
        session_index, job_id, provider_job_id, remaining_hours, session_timeout,
    )

    # Persist session + update current provider_job_id on the job row.
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.job_sessions
                   (job_id, session_index, provider_job_id, chunk_status, started_at)
               VALUES ($1, $2, $3, 'running', NOW())
               ON CONFLICT (job_id, session_index) DO UPDATE
                   SET provider_job_id = EXCLUDED.provider_job_id,
                       chunk_status = 'running',
                       started_at = NOW(),
                       ended_at = NULL""",
            job_id,
            session_index,
            provider_job_id,
        )
        await conn.execute(
            """UPDATE public.jobs
               SET runpod_job_id = $1,
                   session_count = $2,
                   status = 'running',
                   stage = $3,
                   started_at = COALESCE(started_at, NOW()),
                   updated_at = NOW()
               WHERE id = $4""",
            provider_job_id,
            session_index + 1,
            TOOL_STAGE_MAP.get(tool, TOOL_STAGE_MAP.get("rfdiffusion")).value,
            job_id,
        )


async def handle_chunk_status(
    job_id: str,
    chunk_status: str,
    designs_completed: int,
    error: str | None = None,
) -> None:
    """Called from the webhook handler when a session reports its terminal
    ``chunk_status``. Closes the current session row and, for
    ``paused_for_resume``, enqueues the next session.

    Args:
        job_id: Job UUID.
        chunk_status: One of 'complete' | 'paused_for_resume' | 'failed'.
        designs_completed: Designs done in the session that just ended.
        error: Raw error string (only for chunk_status='failed').
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Close the most recent session row.
        await conn.execute(
            """UPDATE public.job_sessions
               SET ended_at = NOW(),
                   chunk_status = $2,
                   designs_completed = $3,
                   error = $4
               WHERE job_id = $1
                 AND session_index = (
                     SELECT MAX(session_index)
                     FROM public.job_sessions
                     WHERE job_id = $1
                 )""",
            job_id,
            chunk_status,
            designs_completed,
            error,
        )

        # Accumulate hours_consumed using the just-ended session's duration.
        await conn.execute(
            """UPDATE public.jobs j
               SET hours_consumed = hours_consumed + sess.session_hours
               FROM (
                   SELECT EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600.0
                          AS session_hours
                   FROM public.job_sessions
                   WHERE job_id = $1
                   ORDER BY session_index DESC
                   LIMIT 1
               ) sess
               WHERE j.id = $1""",
            job_id,
        )

    if chunk_status == "paused_for_resume":
        # Enqueue the next session. The new arq task calls spawn_session with
        # session_index + 1. Done asynchronously so the webhook returns fast.
        async with pool.acquire() as conn:
            cur = await conn.fetchval(
                "SELECT COALESCE(MAX(session_index), 0) FROM public.job_sessions WHERE job_id = $1",
                job_id,
            )
        next_index = int(cur) + 1
        arq_pool = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
        await arq_pool.enqueue_job("resume_session", job_id=job_id, session_index=next_index)
        await arq_pool.aclose()
        logger.info(
            "session_orchestrator: enqueued session %d resume for job %s",
            next_index, job_id,
        )


async def resume_session(ctx: dict, job_id: str, session_index: int) -> None:
    """arq task entry point for resuming a job at a new session index.

    Registered on the arq worker alongside ``run_job`` so the webhook
    handler's ``enqueue_job("resume_session", ...)`` call resolves correctly.
    Thin wrapper around ``spawn_session``.
    """
    await spawn_session(ctx, job_id, session_index=session_index)
