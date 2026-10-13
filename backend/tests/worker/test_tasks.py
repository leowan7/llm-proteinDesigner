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
from worker.tasks import (
    publish_status,
    publish_status_best_effort,
    update_job_status,
)

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


# ---------------------------------------------------------------------------
# Tests: update_job_status as a claim
# ---------------------------------------------------------------------------

async def test_update_job_status_claims_a_row_that_is_not_terminal():
    """``require_non_terminal`` makes the UPDATE an atomic claim.

    The webhook route charges a customer on the strength of winning this one
    write (webhooks/router.py:278), so the guard and the
    RETURNING have to reach the DB in the statement itself. A pre-read cannot
    stand in for them: the row can change between the read and the write,
    which is the window worker/cleanup.py's sweep opens
    (worker/cleanup.py:276 claims the same rows from the other
    side).
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value="job-uuid")
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("worker.tasks.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        claimed = await update_job_status(
            "job-uuid", "failed", require_non_terminal=True
        )

    assert claimed is True
    sql = conn.fetchval.call_args[0][0]
    assert "AND status NOT IN ('complete', 'failed', 'cancelled')" in sql
    assert "RETURNING id" in sql
    # The guarded path must not also fire the blind write.
    conn.execute.assert_not_called()


async def test_update_job_status_reports_a_row_another_route_already_took():
    """A claim that writes nothing comes back False.

    This is the value the webhook route tests for before it bills or mails
    (webhooks/router.py:337, the guard below the pod-termination block).
    asyncpg's ``fetchval``
    returns None when the UPDATE matched no row, which is what a terminal
    status does to the guard above.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("worker.tasks.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        claimed = await update_job_status(
            "job-uuid", "failed", require_non_terminal=True
        )

    assert claimed is False


async def test_update_job_status_is_an_unconditional_write_by_default():
    """Every other caller keeps the blind write it had.

    run_job makes all of its own status writes through the default: it
    drives a job through 'running' twice (worker/tasks.py:237 and
    worker/tasks.py:352) and writes 'failed' on three error paths that run
    before it has a pod id to store (worker/tasks.py:265,
    worker/tasks.py:289 and worker/tasks.py:333 -- the last of these is the
    submit_job call itself, so a pod may exist provider-side and be left to
    the reaper). None of them is claiming anything, so the default
    must stay the statement it was before the claim was added -- no status
    guard, no RETURNING, and the same ``execute`` call. The two callers that
    do claim pass the flag explicitly (webhooks/router.py:278,
    jobs/service.py:110).
    """
    conn = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("worker.tasks.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        claimed = await update_job_status("job-uuid", "running")

    assert claimed is True
    sql = conn.execute.call_args[0][0]
    assert "NOT IN" not in sql
    assert "RETURNING" not in sql
    conn.fetchval.assert_not_called()


async def test_update_job_status_claims_on_a_caller_s_own_connection():
    """A caller that passes a connection is written on that connection.

    The webhook handler opens a transaction and claims inside it
    (webhooks/router.py:276-282) so that a failure in the candidate writes
    rolls the claim back with them. That only holds if the claim actually
    runs on the connection it was handed: a claim that quietly took a second
    connection from the pool would commit on its own, outside the caller's
    transaction, and the rollback would leave a terminal row behind. So this
    pins both halves -- the statement lands on the caller's connection, and
    the pool is never touched.
    """
    caller_conn = AsyncMock()
    caller_conn.fetchval = AsyncMock(return_value="job-uuid")

    with patch("worker.tasks.get_db_pool", new_callable=AsyncMock) as mock_pool_getter:
        claimed = await update_job_status(
            "job-uuid", "complete", require_non_terminal=True, conn=caller_conn
        )

    assert claimed is True
    mock_pool_getter.assert_not_called()
    sql = caller_conn.fetchval.call_args[0][0]
    assert "AND status NOT IN ('complete', 'failed', 'cancelled')" in sql
    assert "RETURNING id" in sql


async def test_update_job_status_writes_unconditionally_on_a_caller_s_connection():
    """The default write honours a caller's connection too.

    Same contract as the claim above, for the branch that does not claim:
    whichever statement runs, it runs where the caller said.
    """
    caller_conn = AsyncMock()

    with patch("worker.tasks.get_db_pool", new_callable=AsyncMock) as mock_pool_getter:
        claimed = await update_job_status("job-uuid", "running", conn=caller_conn)

    assert claimed is True
    mock_pool_getter.assert_not_called()
    assert "NOT IN" not in caller_conn.execute.call_args[0][0]
    caller_conn.fetchval.assert_not_called()


async def test_publish_status_best_effort_swallows_a_redis_fault():
    """The wrapper all three settling routes publish through absorbs Redis.

    Each of them publishes between a claim UPDATE that has already committed
    and the charge that follows it (webhooks/router.py:349,
    worker/cleanup.py:328 and jobs/service.py:172), so an exception escaping
    the publish would skip a charge for GPU time already burned and, on the
    two failure routes, the mail that tells the customer the run ended.

    The fault is injected at ``worker.tasks.publish_status`` rather than at
    the wrapper, because that is the name the wrapper resolves from this
    module's namespace at call time. Patching the wrapper would replace the
    code under test.
    """
    boom = RuntimeError("redis unreachable")

    with (
        patch(
            "worker.tasks.publish_status",
            new_callable=AsyncMock,
            side_effect=boom,
        ) as mock_publish,
        patch("worker.tasks.sentry_sdk.capture_exception") as mock_sentry,
    ):
        await publish_status_best_effort("job-x", "failed", "Failed")

    # It delegated -- the wrapper is not a no-op -- and it reported.
    mock_publish.assert_awaited_once_with("job-x", "failed", "Failed")
    mock_sentry.assert_called_once_with(boom)
