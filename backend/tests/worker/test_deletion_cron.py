"""Tests for ``worker/deletion_cron.py`` — daily hard-delete sweep (Plan 10-04)."""
import os
os.environ.setdefault("TESTING", "true")

from unittest.mock import AsyncMock, MagicMock, patch


from worker.deletion_cron import process_pending_deletions


def _make_ctx(conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_process_pending_deletions_no_rows_returns_zero():
    """No users past grace period → returns 0; execute_hard_delete never called."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("worker.deletion_cron.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("worker.deletion_cron.execute_hard_delete", new_callable=AsyncMock) as mock_exec:
        count = await process_pending_deletions()

    assert count == 0
    mock_exec.assert_not_called()


async def test_process_pending_deletions_one_row_calls_executor_with_correct_args():
    """Single user past grace period → execute_hard_delete called with id/email/stripe; returns 1."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": "user-uuid-1", "email": "a@example.com", "stripe_customer_id": "cus_aaa"},
    ])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("worker.deletion_cron.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("worker.deletion_cron.execute_hard_delete", new_callable=AsyncMock) as mock_exec:
        count = await process_pending_deletions()

    assert count == 1
    mock_exec.assert_called_once_with("user-uuid-1", "a@example.com", "cus_aaa")


async def test_process_pending_deletions_swallows_executor_errors():
    """execute_hard_delete raising must NOT stop the function; return count excludes failed rows."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": "user-1", "email": "a@example.com", "stripe_customer_id": "cus_a"},
        {"id": "user-2", "email": "b@example.com", "stripe_customer_id": None},
    ])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    async def flaky_executor(user_id, email, stripe_id):
        if user_id == "user-1":
            raise RuntimeError("R2 permission denied")

    with patch("worker.deletion_cron.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("worker.deletion_cron.execute_hard_delete", side_effect=flaky_executor) as mock_exec:
        count = await process_pending_deletions()

    # user-2 succeeded, user-1 failed — count is 1, function did NOT re-raise.
    assert count == 1
    assert mock_exec.await_count == 2


async def test_process_pending_deletions_handles_missing_stripe_customer_id():
    """Users without Stripe customers (stripe_customer_id IS NULL) must still be processed."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": "user-nostripe", "email": "c@example.com", "stripe_customer_id": None},
    ])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("worker.deletion_cron.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("worker.deletion_cron.execute_hard_delete", new_callable=AsyncMock) as mock_exec:
        count = await process_pending_deletions()

    assert count == 1
    mock_exec.assert_called_once_with("user-nostripe", "c@example.com", None)
