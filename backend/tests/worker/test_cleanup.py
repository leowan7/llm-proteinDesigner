"""Tests for orphan pod cleanup and stale job detection.

Covers:
- cleanup_orphan_pods terminates pods running > MAX_POD_LIFETIME_SECONDS
- cleanup_orphan_pods does nothing when no orphaned pods exist
- detect_stale_jobs marks stale running jobs as failed and sends emails
- detect_stale_jobs does nothing when no stale jobs exist

Patches db.connection.get_db_pool, gpu.runpod.RunPodProvider,
jobs.notifications.send_failure_email, and worker.tasks.publish_status.
"""
import os
os.environ.setdefault("TESTING", "true")

import datetime
from unittest.mock import AsyncMock, MagicMock, patch


from worker.cleanup import (
    MAX_POD_LIFETIME_SECONDS,
    STALE_HEARTBEAT_SECONDS,
    cleanup_orphan_pods,
    detect_stale_jobs,
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


# Use real system time so elapsed-time comparisons in cleanup functions are accurate.
# Tests that need "old" timestamps compute them relative to actual now().
NOW_UTC = datetime.datetime.now(datetime.timezone.utc)

# A job started beyond MAX_POD_LIFETIME_SECONDS ago — should be orphaned.
ORPHAN_STARTED_AT = NOW_UTC - datetime.timedelta(seconds=MAX_POD_LIFETIME_SECONDS + 600)

# A job started just 10 minutes ago — well within the orphan limit.
RECENT_STARTED_AT = NOW_UTC - datetime.timedelta(seconds=600)

# A job started beyond STALE_HEARTBEAT_SECONDS ago with no heartbeat.
STALE_STARTED_AT = NOW_UTC - datetime.timedelta(seconds=STALE_HEARTBEAT_SECONDS + 60)


# ---------------------------------------------------------------------------
# Tests: cleanup_orphan_pods
# ---------------------------------------------------------------------------

async def test_cleanup_orphan_pods_terminates():
    """cleanup_orphan_pods terminates pods that have been running too long.

    Verifies:
    - RunPodProvider.terminate_pod called for orphaned pod
    - Returns count of terminated pods (1)
    """
    orphan_pod = {
        "id": "pod-orphan-abc",
        "name": "kendrew-rfdiffusion-12345",
        "status": "RUNNING",
    }
    job_row = {
        "id": "job-orphan-uuid",
        "status": "running",
        "started_at": ORPHAN_STARTED_AT,
        "total_budget_hours": 0,
    }

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=job_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    mock_provider = AsyncMock()
    mock_provider.list_pods = AsyncMock(return_value=[orphan_pod])
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        count = await cleanup_orphan_pods()

    assert count == 1
    mock_provider.terminate_pod.assert_called_once_with("pod-orphan-abc")


async def test_cleanup_orphan_pods_no_orphans():
    """cleanup_orphan_pods does nothing when no pods are running beyond the limit."""
    fresh_pod = {
        "id": "pod-fresh-xyz",
        "name": "kendrew-rfdiffusion-99999",
        "status": "RUNNING",
    }
    # Use RECENT_STARTED_AT computed from real now — well within MAX_POD_LIFETIME_SECONDS.
    job_row = {
        "id": "job-fresh-uuid",
        "status": "running",
        "started_at": RECENT_STARTED_AT,
        "total_budget_hours": 0,
    }

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=job_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    mock_provider = AsyncMock()
    mock_provider.list_pods = AsyncMock(return_value=[fresh_pod])
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        count = await cleanup_orphan_pods()

    assert count == 0
    mock_provider.terminate_pod.assert_not_called()


async def test_cleanup_orphan_pods_no_provider():
    """cleanup_orphan_pods exits early when the GPU provider cannot be built.

    The modern flow routes through ``get_provider()`` which raises if no
    provider is configured (Modal/RunPod credentials both missing). The
    function swallows the error, logs it, and returns 0 without touching
    the provider.
    """
    with patch(
        "worker.cleanup.get_provider",
        side_effect=RuntimeError("No GPU provider configured"),
    ):
        count = await cleanup_orphan_pods()

    assert count == 0


async def test_cleanup_orphan_pods_non_kendrew_pods_skipped():
    """Pods not named with 'kendrew-' prefix are ignored by cleanup.

    The cleanup function still fetches the DB pool (called before the pod loop),
    but never calls pool.acquire() because non-kendrew pods are filtered by name.
    """
    external_pod = {
        "id": "pod-external-111",
        "name": "other-service-pod",  # Not a kendrew pod — skipped by name check
        "status": "RUNNING",
    }

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock()  # Should never be called

    mock_provider = AsyncMock()
    mock_provider.list_pods = AsyncMock(return_value=[external_pod])
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        count = await cleanup_orphan_pods()

    assert count == 0
    mock_provider.terminate_pod.assert_not_called()
    # Verify DB was never queried (non-kendrew pod filtered before DB lookup)
    mock_pool.acquire.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: detect_stale_jobs
# ---------------------------------------------------------------------------

async def test_cleanup_stale_jobs():
    """detect_stale_jobs marks stale running jobs as failed and sends failure emails.

    A stale job has status 'running' and no heartbeat for > STALE_HEARTBEAT_SECONDS.
    Verifies:
    - DB updated to 'failed'
    - publish_status called with 'failed'
    - send_failure_email called
    """
    stale_job_row = {
        "id": "job-stale-uuid",
        "user_id": "user-stale",
        "started_at": STALE_STARTED_AT,
        "last_heartbeat_at": None,  # Never sent a heartbeat
        "runpod_job_id": "pod-stale-xyz",
    }
    user_row = {"email": "stale@example.com"}

    # Conn 1: fetch stale jobs
    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[stale_job_row])

    # Conn 2: UPDATE job status to failed
    conn2 = AsyncMock()
    conn2.execute = AsyncMock()

    # Conn 3: SELECT user email
    conn3 = AsyncMock()
    conn3.fetchrow = AsyncMock(return_value=user_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1),
        _make_ctx(conn2),
        _make_ctx(conn3),
    ])

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        count = await detect_stale_jobs()

    assert count == 1

    # DB was updated to failed
    conn2.execute.assert_called_once()
    execute_sql = conn2.execute.call_args[0][0]
    assert "failed" in execute_sql

    # SSE failure event published
    mock_publish.assert_called_once()
    assert mock_publish.call_args[0][1] == "failed"

    # Failure email sent
    mock_email.assert_called_once()
    assert mock_email.call_args[1]["to_email"] == "stale@example.com"


async def test_cleanup_stale_jobs_none_stale():
    """detect_stale_jobs does nothing when no stale jobs are found."""
    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[])  # No stale jobs

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn1))

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        count = await detect_stale_jobs()

    assert count == 0
    mock_email.assert_not_called()
