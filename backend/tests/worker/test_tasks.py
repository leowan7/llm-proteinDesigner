"""Tests for arq worker tasks (run_job, publish_status).

Covers:
- run_job creates a RunPod pod and updates DB to 'running' with pod ID
- run_job is idempotent: skips pod creation if runpod_job_id already set
- run_job returns without error when job is not found in DB
- publish_status publishes correct JSON to Redis channel

Patches db.connection.get_db_pool, gpu.runpod.RunPodProvider,
storage.client.generate_presigned_get_url, and redis.asyncio at the
worker.tasks module import level.
"""
import os

os.environ.setdefault("TESTING", "true")

import json
from unittest.mock import AsyncMock, MagicMock, patch

# Import the functions under test AFTER setting TESTING env
from worker.tasks import publish_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(conn):
    """Wrap a mock asyncpg connection in an async context manager.

    Args:
        conn: The mock asyncpg connection to wrap.

    Returns:
        AsyncMock configured for 'async with pool.acquire() as conn'.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _queued_job_row(with_pod_id: str | None = None) -> dict:
    """Build a mock job row as returned by DB fetchrow.

    Args:
        with_pod_id: If provided, sets runpod_job_id (simulates already-submitted job).

    Returns:
        Dict matching the SELECT in run_job.
    """
    return {
        "job_spec": json.dumps({
            "tool": "rfdiffusion",
            "target_pdb_path": "s3://bucket/target.pdb",
            "num_designs": 10,
        }),
        "user_id": "test-user-uuid",
        "runpod_job_id": with_pod_id,
        "job_tier": "pilot",
        "total_budget_hours": 4,
    }


# ---------------------------------------------------------------------------
# Tests: run_job
# ---------------------------------------------------------------------------

async def test_run_job_creates_pod():
    """run_job creates a RunPod pod and stores pod ID in DB when job is queued."""
    from worker.tasks import run_job

    job_id = "job-test-create-pod"
    pod_id = "pod-xyz-789"

    # Conn 1: SELECT job spec + idempotency check
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value=_queued_job_row())

    # Conn 2: UPDATE job_token
    conn2 = AsyncMock()
    conn2.execute = AsyncMock()

    # Conn 3: UPDATE runpod_job_id
    conn3 = AsyncMock()
    conn3.execute = AsyncMock()

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1),
        _make_ctx(conn2),
        _make_ctx(conn3),
    ])

    mock_provider = AsyncMock()
    mock_provider.submit_job = AsyncMock(return_value=pod_id)

    mock_pipeline = MagicMock()
    mock_pipeline.presigned_url_expiry_seconds = 7200

    with (
        patch("worker.tasks.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.tasks.get_provider", return_value=mock_provider),
        patch("worker.tasks.endpoint_for_tool", return_value="ranomics-rfdiffusion-prod/run_tool"),
        patch("worker.tasks.generate_presigned_get_url", return_value="https://s3.example.com/target.pdb"),
        patch("worker.tasks.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("worker.tasks.update_job_status", new_callable=AsyncMock),
        patch("worker.tasks.PIPELINE_MAP", {"rfdiffusion": mock_pipeline}),
        patch("worker.tasks.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.runpod_gpu_type_ids = ["NVIDIA A100"]
        mock_settings.runpod_container_disk_gb = 50
        mock_settings.runpod_network_volume_id = None
        mock_settings.runpod_container_registry_auth_id = None
        mock_settings.app_base_url = "https://app.bindwave.com"

        await run_job({}, job_id)

    # provider.submit_job was called to create the pod
    mock_provider.submit_job.assert_called_once()

    # DB was updated with the pod ID
    conn3.execute.assert_called_once()
    update_call = conn3.execute.call_args
    assert pod_id in update_call[0]  # pod_id in positional args

    # Status was published at least once
    mock_publish.assert_called()


async def test_run_job_idempotent_skip():
    """run_job skips pod creation if runpod_job_id is already set (idempotency guard)."""
    from worker.tasks import run_job

    job_id = "job-test-idempotent"
    existing_pod_id = "pod-already-exists"

    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value=_queued_job_row(with_pod_id=existing_pod_id))

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn1))

    mock_provider = AsyncMock()
    mock_provider.submit_job = AsyncMock()

    with (
        patch("worker.tasks.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.tasks.get_provider", return_value=mock_provider),
        patch("worker.tasks.endpoint_for_tool", return_value="ranomics-rfdiffusion-prod/run_tool"),
        patch("worker.tasks.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        await run_job({}, job_id)

    # Pod creation was skipped due to idempotency guard
    mock_provider.submit_job.assert_not_called()


async def test_run_job_missing_job():
    """run_job returns without error when the job does not exist in DB."""
    from worker.tasks import run_job

    job_id = "job-does-not-exist"

    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value=None)  # No matching job

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn1))

    mock_provider = AsyncMock()

    with (
        patch("worker.tasks.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.tasks.get_provider", return_value=mock_provider),
        patch("worker.tasks.endpoint_for_tool", return_value="ranomics-rfdiffusion-prod/run_tool"),
        patch("worker.tasks.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        # Should return without raising any exception
        await run_job({}, job_id)

    mock_provider.submit_job.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: publish_status
# ---------------------------------------------------------------------------

async def test_publish_status():
    """publish_status publishes correct JSON payload to the Redis job channel."""
    job_id = "job-status-test"
    status = "running"
    stage = "Initializing GPU"

    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("worker.tasks.aioredis.from_url", return_value=mock_redis):
        await publish_status(job_id, status, stage)

    mock_redis.publish.assert_called_once()
    channel, payload_str = mock_redis.publish.call_args[0]

    assert channel == f"job:{job_id}:status"

    payload = json.loads(payload_str)
    assert payload["job_id"] == job_id
    assert payload["status"] == status
    assert payload["stage"] == stage

    # Redis connection was closed after publishing
    mock_redis.aclose.assert_called_once()
