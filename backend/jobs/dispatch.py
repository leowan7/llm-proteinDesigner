"""Job dispatch: write DB state then enqueue arq worker task.

BILL-04 compliance: the DB write (status=queued) ALWAYS precedes any GPU
provider API call. This ensures jobs are tracked even if the process crashes
between the DB write and RunPod submission.
"""

import asyncpg
from arq import create_pool as arq_create_pool
from arq.connections import RedisSettings

from agent.jobspec import JobSpec
from config import settings


async def launch_job(
    job_id: str,
    job_spec: JobSpec,
    user_id: str | None,
    pool: asyncpg.Pool,
    job_tier: str = "pilot",
    total_budget_hours: int = 4,
    organization_id: str | None = None,
    created_by_user_id: str | None = None,
    conn: asyncpg.Connection | None = None,
) -> None:
    """Write job state to DB (status=queued) then enqueue the arq worker task.

    The DB update is performed before enqueueing so the job is always recorded
    even if the process crashes between the DB write and the arq enqueue call.

    Args:
        job_id: UUID string of the pre-created job row.
        job_spec: Validated JobSpec from the agent wizard.
        user_id: UUID string of the authenticated user owning the job.
        pool: asyncpg connection pool for the DB update.
        job_tier: "pilot" (default, GSD Phase 4 validation) or "full_design"
            (real campaign). Written to the ``jobs.job_tier`` column.
        total_budget_hours: GPU hours cap for the job (1-96). Only used for
            full_design; pilot runs ignore this and use a tool-specific preset.
        organization_id: Phase 12 — the org context for this job. When provided,
            also stamped on the row alongside created_by_user_id so org-scoped
            queries see this job.
        created_by_user_id: Phase 12 — the user who launched (defaults to
            ``user_id`` when omitted; explicit param exists so future API paths
            where launcher != owner can pass a distinct value).
        conn: Phase 13 — an existing asyncpg connection to run the DB update on.
            When supplied, ``launch_job`` uses it directly instead of acquiring a
            new connection from ``pool``, so the /api/v1 handler can co-write the
            idempotency row + jobs UPDATE in one transaction (RESEARCH §5.6). The
            arq enqueue path is unchanged. When None (all legacy callers), the
            behavior is byte-identical to the pre-Phase-13 path.
    """
    # 1. DB write first — BILL-04 compliance. Persist tier + budget columns
    #    added in migration 20260420000001_job_tier_and_budget.sql so the worker
    #    and Modal session orchestrator can read them.
    #
    # Phase 12: also stamp organization_id + created_by_user_id when supplied.
    # These columns are NOT NULL once migration 20260605000002 + signup
    # bootstrap roll out, but during the flag-off cutover window the launch
    # path still has them — the cutover preserves the org context.
    effective_created_by = created_by_user_id or user_id

    async def _do_update(c: asyncpg.Connection) -> None:
        # WHERE matches the row when EITHER the launcher owns it (web flow, where
        # user_id is supplied) OR the org owns it (v1 flow, where user_id is None
        # and organization_id is supplied). For legacy callers organization_id is
        # None so ``organization_id = $6`` is never true and the match falls back
        # to user_id — byte-identical to the pre-Phase-13 behavior.
        if organization_id is not None and effective_created_by is not None:
            await c.execute(
                """
                UPDATE public.jobs
                SET status = 'queued',
                    job_spec = $1::jsonb,
                    job_tier = $4,
                    total_budget_hours = $5,
                    organization_id = $6::uuid,
                    created_by_user_id = $7::uuid,
                    updated_at = NOW()
                WHERE id = $2 AND (user_id = $3 OR organization_id = $6::uuid)
                """,
                job_spec.model_dump_json(),
                job_id,
                user_id,
                job_tier,
                total_budget_hours,
                organization_id,
                effective_created_by,
            )
        elif organization_id is not None:
            # v1 path: org supplied but no launcher user (created_by unknown).
            # Do not overwrite created_by_user_id; only stamp/confirm the org.
            await c.execute(
                """
                UPDATE public.jobs
                SET status = 'queued',
                    job_spec = $1::jsonb,
                    job_tier = $4,
                    total_budget_hours = $5,
                    organization_id = $6::uuid,
                    updated_at = NOW()
                WHERE id = $2 AND (user_id = $3 OR organization_id = $6::uuid)
                """,
                job_spec.model_dump_json(),
                job_id,
                user_id,
                job_tier,
                total_budget_hours,
                organization_id,
            )
        else:
            await c.execute(
                """
                UPDATE public.jobs
                SET status = 'queued',
                    job_spec = $1::jsonb,
                    job_tier = $4,
                    total_budget_hours = $5,
                    updated_at = NOW()
                WHERE id = $2 AND user_id = $3
                """,
                job_spec.model_dump_json(),
                job_id,
                user_id,
                job_tier,
                total_budget_hours,
            )

    if conn is not None:
        await _do_update(conn)
    else:
        async with pool.acquire() as c:
            await _do_update(c)

    # 2. Enqueue arq task after DB write succeeds
    arq_pool = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
    await arq_pool.enqueue_job("run_job", job_id=job_id)
    await arq_pool.aclose()
