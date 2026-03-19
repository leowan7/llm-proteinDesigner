"""arq worker tasks for GPU job execution.

This module is loaded by the arq worker process (worker/main.py).
The run_job task submits a job to RunPod and stores the provider job ID.
Completion is handled by the webhook router (webhooks/router.py), not polling.

Key design decisions:
- Idempotent: skips submit if runpod_job_id is already set in the DB.
- DB status is updated to 'running' before calling the GPU provider.
- Redis pub/sub publishes status events consumed by the SSE endpoint.
"""

import json

import redis.asyncio as aioredis

from config import settings
from db.connection import get_db_pool
from gpu.provider import GPUJobSubmission
from gpu.runpod import RunPodProvider
from jobs.models import TOOL_STAGE_MAP
from storage.client import generate_presigned_put_url


# Map tool names to their RunPod endpoint IDs from settings.
ENDPOINT_IDS: dict[str, str] = {
    "rfdiffusion": settings.runpod_endpoint_rfdiffusion,
    "rfantibody": settings.runpod_endpoint_rfantibody,
    "bindcraft": settings.runpod_endpoint_bindcraft,
    "boltzgen": settings.runpod_endpoint_boltzgen,
}


async def publish_status(job_id: str, status: str, stage: str) -> None:
    """Publish a job status event to the Redis pub/sub channel for SSE fan-out.

    Channel name: job:{job_id}:status
    Payload: JSON with job_id, status, and stage fields.

    Args:
        job_id: Job UUID string.
        status: Coarse machine state (e.g. "running", "complete").
        stage: Human-readable stage label (e.g. "Initializing GPU").
    """
    r = aioredis.from_url(settings.redis_url)
    payload = json.dumps({"job_id": job_id, "status": status, "stage": stage})
    await r.publish(f"job:{job_id}:status", payload)
    await r.aclose()


async def update_job_status(
    job_id: str,
    status: str,
    stage: str | None = None,
    gpu_seconds: int | None = None,
    error_category: str | None = None,
) -> None:
    """Update the job record in the DB with new status and optional fields.

    Handles started_at and completed_at timestamps automatically based on status
    transitions. Only updates columns that are explicitly provided.

    Args:
        job_id: Job UUID string.
        status: New coarse status value.
        stage: Optional human-readable stage label to update.
        gpu_seconds: Optional GPU time consumed so far.
        error_category: Optional error category string for failed jobs.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Build SET clause dynamically to avoid overwriting unrelated columns.
        # $1 = status, $2 = job_id — reserved. Additional params start at $3.
        sets = ["status = $1", "updated_at = NOW()"]
        params: list = [status, job_id]
        idx = 3

        if stage is not None:
            sets.append(f"stage = ${idx}")
            params.append(stage)
            idx += 1

        if gpu_seconds is not None:
            sets.append(f"gpu_seconds = ${idx}")
            params.append(gpu_seconds)
            idx += 1

        if error_category is not None:
            sets.append(f"error_category = ${idx}")
            params.append(error_category)
            idx += 1

        # Set terminal timestamp when job reaches a terminal state.
        if status in ("complete", "failed", "cancelled"):
            sets.append("completed_at = NOW()")

        # Set started_at on first transition to running (COALESCE preserves existing value).
        if status == "running":
            sets.append("started_at = COALESCE(started_at, NOW())")

        await conn.execute(
            f"UPDATE public.jobs SET {', '.join(sets)} WHERE id = $2",
            *params,
        )


async def run_job(ctx: dict, job_id: str) -> None:
    """arq task: submit a job to RunPod and record the provider job ID.

    This task is idempotent — if runpod_job_id is already set in the DB
    (e.g. from a prior attempt), the submit is skipped to prevent duplicate jobs.

    Completion, billing, and email notifications are handled by the RunPod
    webhook callback (webhooks/router.py), not by this task.

    Args:
        ctx: arq task context (not used directly).
        job_id: Job UUID string to execute.
    """
    pool = await get_db_pool()
    provider = RunPodProvider(api_key=settings.runpod_api_key)

    # Fetch job spec and check idempotency guard.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT job_spec, user_id, runpod_job_id FROM public.jobs WHERE id = $1",
            job_id,
        )

    if not row:
        # Job not found — nothing to do.
        return

    # Idempotency check: skip if already submitted to RunPod.
    if row["runpod_job_id"]:
        return

    spec_data = json.loads(row["job_spec"])
    tool = spec_data["tool"]
    user_id = str(row["user_id"])

    # Update status to running / initializing before touching the GPU provider.
    await update_job_status(job_id, "running", stage="Initializing GPU")
    await publish_status(job_id, "running", "Initializing GPU")

    # Generate presigned PUT URLs so the RunPod container can upload outputs directly.
    num_designs = spec_data.get("parameters", {}).get("num_designs", 10)
    output_prefix = f"users/{user_id}/jobs/{job_id}/outputs/"
    presigned_urls = [
        generate_presigned_put_url(f"{output_prefix}design_{i + 1:03d}.pdb")
        for i in range(num_designs)
    ]
    report_url = generate_presigned_put_url(f"{output_prefix}report.txt")

    # Submit to RunPod.
    endpoint_id = ENDPOINT_IDS.get(tool, "")
    submission = GPUJobSubmission(
        endpoint_id=endpoint_id,
        input_payload={
            "job_spec": spec_data,
            "output_presigned_urls": presigned_urls,
            "report_presigned_url": report_url,
        },
        webhook_url=f"{settings.app_base_url}/webhooks/runpod",
    )
    runpod_job_id = await provider.submit_job(submission)

    # Persist the RunPod job ID for idempotency and cancellation.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.jobs SET runpod_job_id = $1 WHERE id = $2",
            runpod_job_id,
            job_id,
        )

    # Update stage to the tool-specific running stage label.
    running_stage = TOOL_STAGE_MAP.get(tool, TOOL_STAGE_MAP["rfdiffusion"]).value
    await update_job_status(job_id, "running", stage=running_stage)
    await publish_status(job_id, "running", running_stage)
