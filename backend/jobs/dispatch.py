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
    user_id: str,
    pool: asyncpg.Pool,
    job_tier: str = "pilot",
    total_budget_hours: int = 4,
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
    """
    # 1. DB write first — BILL-04 compliance. Persist tier + budget columns
    #    added in migration 20260420000001_job_tier_and_budget.sql so the worker
    #    and Modal session orchestrator can read them.
    async with pool.acquire() as conn:
        await conn.execute(
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

    # 2. Enqueue arq task after DB write succeeds
    arq_pool = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
    await arq_pool.enqueue_job("run_job", job_id=job_id)
    await arq_pool.aclose()
