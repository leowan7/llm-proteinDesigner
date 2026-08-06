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
import logging
import secrets

import redis.asyncio as aioredis
import sentry_sdk
from config import settings
from db.connection import get_db_pool
from gpu import endpoint_for_tool, get_provider
from gpu.provider import GPUJobSubmission
from jobs.models import TOOL_STAGE_MAP
from pipelines import PIPELINE_MAP
from storage.client import ensure_pdb_in_s3, generate_presigned_get_url

logger = logging.getLogger(__name__)


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
    # Provider selection is driven by settings.gpu_provider. Default is Modal;
    # runpod_emergency is the break-glass rollback path. See gpu/__init__.py.
    provider = get_provider()

    # Fetch job spec and check idempotency guard. job_tier is new in Phase 2;
    # fall back to "pilot" when the column is absent on older rows.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT job_spec,
                   user_id,
                   runpod_job_id,
                   COALESCE(job_tier, 'pilot') AS job_tier,
                   COALESCE(total_budget_hours, 4) AS total_budget_hours
            FROM public.jobs
            WHERE id = $1
            """,
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

    # Inject tier + budget into the spec dict the container sees, so the
    # pipeline's generate_config can apply pilot presets and so the container's
    # run_pipeline.py can observe the tier for per-session behaviour.
    spec_data["job_tier"] = row["job_tier"]
    spec_data["total_budget_hours"] = int(row["total_budget_hours"])

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

    # Ensure the target PDB is in S3/MinIO. The agent's resolve_structure tool
    # writes the file to a local path inside the backend container; before we
    # hand Modal a presigned URL, upload it and replace the local path with the
    # S3 key. Persist back to the DB so resume sessions skip the upload.
    try:
        s3_key = ensure_pdb_in_s3(
            spec_data["target_pdb_path"], user_id=user_id, job_id=job_id,
        )
    except Exception as exc:
        logger.exception("run_job: ensure_pdb_in_s3 failed for job %s", job_id)
        sentry_sdk.capture_exception(exc)
        await update_job_status(
            job_id, "failed", stage="Failed",
            error_category=f"Target PDB upload failed: {type(exc).__name__}: {exc}",
        )
        await publish_status(job_id, "failed", "Failed")
        return

    if s3_key != spec_data["target_pdb_path"]:
        spec_data["target_pdb_path"] = s3_key
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.jobs SET job_spec = $1 WHERE id = $2",
                json.dumps(spec_data),
                job_id,
            )

    # Generate presigned GET URL for the input PDB (container downloads target).
    input_pdb_url = generate_presigned_get_url(s3_key, expires_in=url_expiry)

    # Resolve the provider-specific endpoint identifier for this tool.
    # Modal: "kendrew-<tool>-prod/run_tool". RunPod-emergency: Docker image name.
    try:
        endpoint_id = endpoint_for_tool(tool)
    except ValueError as exc:
        await update_job_status(
            job_id, "failed", stage="Failed",
            error_category=str(exc),
        )
        await publish_status(job_id, "failed", "Failed")
        return

    job_tier = row["job_tier"]
    total_budget_hours = int(row["total_budget_hours"])

    # Build the submission. ``endpoint_id`` semantics depend on the active
    # provider (see gpu/__init__.py:endpoint_for_tool). The policy dict now
    # also carries the pilot/full tier and total budget so the Modal function
    # body can enforce the right session timeout.
    submission = GPUJobSubmission(
        endpoint_id=endpoint_id,
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
            "job_tier": job_tier,
            "total_budget_hours": total_budget_hours,
            "session_index": 0,  # First session; orchestrator increments for resume.
            # Legacy RunPod-only fields (ignored by ModalProvider).
            "gpu_type_ids": settings.runpod_gpu_type_ids,
            "container_disk_gb": settings.runpod_container_disk_gb,
            "network_volume_id": settings.runpod_network_volume_id,
            "container_registry_auth_id": settings.runpod_container_registry_auth_id,
        },
    )
    # Submit to the GPU provider. Any failure here (auth, version mismatch,
    # provider outage, bad endpoint) must mark the job failed so the UI flips
    # out of "running" and the user isn't stuck staring at a dead job.
    try:
        pod_id = await provider.submit_job(submission)
    except Exception as exc:
        logger.exception("run_job: provider.submit_job failed for job %s", job_id)
        sentry_sdk.capture_exception(exc)
        await update_job_status(
            job_id,
            "failed",
            stage="Failed to dispatch",
            error_category=f"Dispatch failed: {type(exc).__name__}: {exc}",
        )
        await publish_status(job_id, "failed", "Failed to dispatch")
        return

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
