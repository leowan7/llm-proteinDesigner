"""arq worker tasks for GPU job execution.

This module is loaded by the arq worker process (worker/main.py).
The run_job task creates a RunPod GPU Pod and stores the pod ID.
Completion is handled by the webhook router (webhooks/router.py), which
also terminates the pod after receiving results.

Key design decisions:
- Idempotent: skips pod creation if runpod_job_id is already set in the DB.
- DB status is updated to 'running' before creating the pod.
- Redis pub/sub publishes status events consumed by the SSE endpoint.
"""

import json
import secrets

import redis.asyncio as aioredis

from config import settings
from db.connection import get_db_pool
from gpu.provider import GPUJobSubmission
from gpu.runpod import RunPodProvider
from jobs.models import TOOL_STAGE_MAP
from pipelines import PIPELINE_MAP
from storage.client import generate_presigned_get_url


# Map tool names to their Docker image names from settings.
TOOL_IMAGES: dict[str, str] = {
    "rfdiffusion": settings.runpod_image_rfdiffusion,
    "rfantibody": settings.runpod_image_rfantibody,
    "bindcraft": settings.runpod_image_bindcraft,
    "boltzgen": settings.runpod_image_boltzgen,
    "pxdesign": settings.runpod_image_pxdesign,
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
    """arq task: create a RunPod GPU Pod to execute a job.

    This task is idempotent — if runpod_job_id (pod ID) is already set in the
    DB (e.g. from a prior attempt), pod creation is skipped.

    Completion, billing, pod termination, and email notifications are handled
    by the webhook callback (webhooks/router.py), not by this task.

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
        return

    # Idempotency check: skip if already submitted (pod already created).
    if row["runpod_job_id"]:
        return

    spec_data = json.loads(row["job_spec"])
    tool = spec_data["tool"]
    user_id = str(row["user_id"])

    # Update status to running / initializing before creating the pod.
    await update_job_status(job_id, "running", stage="Initializing GPU")
    await publish_status(job_id, "running", "Initializing GPU")

    # Generate a job-specific token for container-to-backend authentication.
    # The container uses this token to request fresh presigned upload URLs on-demand.
    job_token = secrets.token_urlsafe(32)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.jobs SET job_token = $1 WHERE id = $2",
            job_token,
            job_id,
        )

    # Look up the tool pipeline for URL expiry settings.
    pipeline = PIPELINE_MAP[tool]
    url_expiry = pipeline.presigned_url_expiry_seconds

    # Generate presigned GET URL for the input PDB (container downloads target).
    input_pdb_url = generate_presigned_get_url(
        spec_data["target_pdb_path"], expires_in=url_expiry
    )

    # Resolve the Docker image for this tool.
    image_name = TOOL_IMAGES.get(tool, "")
    if not image_name:
        await update_job_status(
            job_id, "failed", stage="Failed",
            error_category=f"No Docker image configured for tool: {tool}",
        )
        await publish_status(job_id, "failed", "Failed")
        return

    # Build the pod submission. endpoint_id is repurposed as the Docker image name.
    # The policy dict carries pod-specific config (GPU type, volumes, etc.).
    submission = GPUJobSubmission(
        endpoint_id=image_name,
        input_payload={
            "job_spec": spec_data,
            "input_presigned_url": input_pdb_url,
            "job_token": job_token,
            "upload_urls_endpoint": f"{settings.app_base_url}/jobs/{job_id}/upload-urls",
        },
        webhook_url=f"{settings.app_base_url}/webhooks/runpod",
        policy={
            "job_id": job_id,
            "tool": tool,
            "gpu_type_ids": settings.runpod_gpu_type_ids,
            "container_disk_gb": settings.runpod_container_disk_gb,
            "network_volume_id": settings.runpod_network_volume_id,
            "container_registry_auth_id": settings.runpod_container_registry_auth_id,
        },
    )
    pod_id = await provider.submit_job(submission)

    # Persist the pod ID (stored in runpod_job_id column) for idempotency and termination.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.jobs SET runpod_job_id = $1 WHERE id = $2",
            pod_id,
            job_id,
        )

    # Update stage to the tool-specific running stage label.
    running_stage = TOOL_STAGE_MAP.get(tool, TOOL_STAGE_MAP["rfdiffusion"]).value
    await update_job_status(job_id, "running", stage=running_stage)
    await publish_status(job_id, "running", running_stage)
