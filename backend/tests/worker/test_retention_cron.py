"""Tests for ``worker/retention_cron.py`` — daily warning + deletion sweep (Plan 10-05).

Eight behaviors covered:
    1. Warning pass sends email and stamps retention_warning_sent_at for a job
       created (retention_days - 7) days ago whose created_at is past policy_effective_from.
    2. Warning pass does NOT re-send when retention_warning_sent_at is already set
       (SQL-level guarded by WHERE retention_warning_sent_at IS NULL — behaviour
       is asserted by not receiving the candidate row at all).
    3. Warning pass respects policy_effective_from (pre-policy rows are excluded
       server-side; the test asserts the SELECT binds effective_from and the
       query text filters ``j.created_at > $1``).
    4. Deletion pass calls delete_job_objects and stamps retention_deleted_at for
       a job past its per-user retention window.
    5. Deletion pass NEVER touches jobs with status='running' (safety guard).
    6. Deletion pass marks status='expired' only when the previous status was a
       terminal status (complete/failed/cancelled); preserves non-terminal
       non-running statuses untouched.
    7. Deletion pass is a no-op on rows that already have retention_deleted_at
       set (WHERE clause excludes them).
    8. Deletion pass respects each user's own ``data_retention_days`` column.
"""
import os
os.environ.setdefault("TESTING", "true")

import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from worker.retention_cron import (
    TERMINAL_STATUSES,
    WARNING_DAYS_BEFORE,
    execute_retention_deletions,
    retention_cron,
    send_retention_warnings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.datetime.now(datetime.timezone.utc)
POLICY_OLD = NOW - datetime.timedelta(days=365)  # policy effective a year ago
POLICY_RECENT = NOW - datetime.timedelta(days=30)  # policy effective 30 days ago


def _ctx(conn):
    """Wrap a mock asyncpg connection in an async context manager for pool.acquire()."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _pool_for(conn):
    """Build a mock pool whose acquire() yields ``conn``."""
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_ctx(conn))
    return pool


# ---------------------------------------------------------------------------
# send_retention_warnings
# ---------------------------------------------------------------------------


async def test_warning_pass_sends_email_and_stamps_row():
    """Happy path: a job (retention_days - 7) days old gets a warning email."""
    job_id = uuid4()
    created_at = NOW - datetime.timedelta(days=84)  # 90 - 7 + slop
    row = {
        "id": job_id,
        "user_id": uuid4(),
        "name": "my-binder-pilot",
        "created_at": created_at,
        "email": "scientist@example.com",
        "data_retention_days": 90,
    }

    # Pool.acquire() is called multiple times: once for _fetch_policy_effective_from,
    # once for the SELECT, then once per row for the UPDATE. Use a single connection
    # mock whose fetchrow/fetch/execute are all programmable.
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=[row])
    conn.execute = AsyncMock()

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.send_retention_warning_email",
        new_callable=AsyncMock,
    ) as mock_email:
        sent = await send_retention_warnings()

    assert sent == 1
    mock_email.assert_awaited_once()
    kwargs = mock_email.await_args.kwargs
    assert kwargs["to_email"] == "scientist@example.com"
    assert kwargs["job_id"] == str(job_id)
    assert kwargs["job_name"] == "my-binder-pilot"
    assert kwargs["retention_days"] == 90
    expected_deletion = (created_at + datetime.timedelta(days=90)).date().isoformat()
    assert kwargs["deletion_date_iso"] == expected_deletion

    # UPDATE with retention_warning_sent_at = now()
    assert conn.execute.await_count == 1
    update_sql = conn.execute.await_args.args[0]
    assert "retention_warning_sent_at" in update_sql
    # W2: cron binds str(uuid) for consistency with deletion_cron convention.
    assert conn.execute.await_args.args[1] == str(job_id)


async def test_warning_pass_does_not_resend_when_already_stamped():
    """An empty SELECT result (because retention_warning_sent_at IS NOT NULL filters
    it out server-side) → zero emails sent, zero UPDATEs issued."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.send_retention_warning_email",
        new_callable=AsyncMock,
    ) as mock_email:
        sent = await send_retention_warnings()

    assert sent == 0
    mock_email.assert_not_called()
    conn.execute.assert_not_called()


async def test_warning_pass_filters_by_policy_effective_from():
    """The SELECT must bind policy_effective_from as $1 and the query text must
    filter ``j.created_at > $1`` so pre-policy jobs are never candidates."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_RECENT})
    conn.fetch = AsyncMock(return_value=[])

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.send_retention_warning_email",
        new_callable=AsyncMock,
    ):
        await send_retention_warnings()

    # The SELECT (conn.fetch) must have been called with the effective_from bound.
    assert conn.fetch.await_count == 1
    fetch_args = conn.fetch.await_args.args
    query = fetch_args[0]
    assert "j.created_at > $1" in query
    assert fetch_args[1] == POLICY_RECENT
    # W8: WARNING_DAYS_BEFORE is interpolated into the query text as an integer.
    assert f"INTERVAL '{WARNING_DAYS_BEFORE} days'" in query


# ---------------------------------------------------------------------------
# execute_retention_deletions
# ---------------------------------------------------------------------------


async def test_deletion_pass_deletes_objects_and_stamps_row():
    """Happy path: a terminal job past its retention window is hard-deleted."""
    job_id = uuid4()
    user_id = uuid4()
    rows = [
        {
            "id": job_id,
            "user_id": user_id,
            "status": "complete",
            "created_at": NOW - datetime.timedelta(days=91),
            "data_retention_days": 90,
        }
    ]

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.delete_job_objects",
        return_value=7,
    ) as mock_delete:
        deleted = await execute_retention_deletions()

    assert deleted == 1
    mock_delete.assert_called_once_with(str(user_id), str(job_id))
    # UPDATE stamps retention_deleted_at and flips status → 'expired' (was 'complete')
    assert conn.execute.await_count == 1
    update_sql, row_id, new_status = conn.execute.await_args.args
    assert "retention_deleted_at = now()" in update_sql
    # W2: cron binds str(uuid) for consistency with deletion_cron convention.
    assert row_id == str(job_id)
    assert new_status == "expired"


async def test_deletion_pass_skips_running_jobs_via_query_filter():
    """The SELECT must include ``status != 'running'`` so running rows never
    become candidates. We assert the query text carries the guard."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=[])

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.delete_job_objects",
    ) as mock_delete:
        await execute_retention_deletions()

    query = conn.fetch.await_args.args[0]
    assert "j.status != 'running'" in query
    mock_delete.assert_not_called()


@pytest.mark.parametrize("prior_status", ["complete", "failed", "cancelled"])
async def test_deletion_pass_marks_terminal_jobs_as_expired(prior_status):
    """Terminal statuses flip to 'expired' after deletion."""
    rows = [
        {
            "id": uuid4(),
            "user_id": uuid4(),
            "status": prior_status,
            "created_at": NOW - datetime.timedelta(days=91),
            "data_retention_days": 90,
        }
    ]
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.delete_job_objects",
        return_value=0,
    ):
        await execute_retention_deletions()

    new_status = conn.execute.await_args.args[2]
    assert new_status == "expired"
    assert prior_status in TERMINAL_STATUSES


@pytest.mark.parametrize("prior_status", ["queued", "pending", "draft"])
async def test_deletion_pass_preserves_non_terminal_non_running_status(prior_status):
    """Non-terminal non-running statuses (queued/pending/draft) are preserved —
    only the object storage is purged and retention_deleted_at is stamped."""
    rows = [
        {
            "id": uuid4(),
            "user_id": uuid4(),
            "status": prior_status,
            "created_at": NOW - datetime.timedelta(days=91),
            "data_retention_days": 90,
        }
    ]
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.delete_job_objects",
        return_value=0,
    ):
        await execute_retention_deletions()

    new_status = conn.execute.await_args.args[2]
    assert new_status == prior_status


async def test_deletion_pass_noop_when_already_deleted_at_sql_level():
    """The SELECT filters ``retention_deleted_at IS NULL`` — an empty result is
    a no-op at the Python level. We assert the filter is in the query."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=[])

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.delete_job_objects",
    ) as mock_delete:
        count = await execute_retention_deletions()

    query = conn.fetch.await_args.args[0]
    assert "retention_deleted_at IS NULL" in query
    assert count == 0
    mock_delete.assert_not_called()


async def test_deletion_pass_respects_per_user_retention_days():
    """A user with a 30-day window has jobs purged at day 31, not day 90.

    The cron delegates this to SQL via ``(u.data_retention_days || ' days')::interval``
    — we assert the query text contains the per-user-days expression."""
    rows = [
        {
            "id": uuid4(),
            "user_id": uuid4(),
            "status": "complete",
            "created_at": NOW - datetime.timedelta(days=31),
            "data_retention_days": 30,
        }
    ]
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.delete_job_objects",
        return_value=1,
    ) as mock_delete:
        deleted = await execute_retention_deletions()

    query = conn.fetch.await_args.args[0]
    assert "(u.data_retention_days || ' days')::interval" in query
    assert deleted == 1
    mock_delete.assert_called_once()


# ---------------------------------------------------------------------------
# retention_cron entrypoint
# ---------------------------------------------------------------------------


async def test_retention_cron_runs_both_passes_and_returns_counts():
    """The entry point calls both passes sequentially and returns the counts."""
    with patch(
        "worker.retention_cron.send_retention_warnings",
        new_callable=AsyncMock,
        return_value=3,
    ) as mock_warn, patch(
        "worker.retention_cron.execute_retention_deletions",
        new_callable=AsyncMock,
        return_value=5,
    ) as mock_del:
        result = await retention_cron()

    assert result == {"warned": 3, "expired": 5}
    mock_warn.assert_awaited_once()
    mock_del.assert_awaited_once()


# ---------------------------------------------------------------------------
# T-10.05-06: email-first, stamp-second ordering guard
# ---------------------------------------------------------------------------


async def test_warning_pass_does_not_stamp_when_email_fails():
    """If send_retention_warning_email raises, retention_warning_sent_at must
    remain NULL so the next cron run retries — T-10.05-06 ordering guarantee."""
    job_id = uuid4()
    row = {
        "id": job_id,
        "user_id": uuid4(),
        "name": "x",
        "created_at": NOW - datetime.timedelta(days=84),
        "email": "a@example.com",
        "data_retention_days": 90,
    }
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"policy_effective_from": POLICY_OLD})
    conn.fetch = AsyncMock(return_value=[row])
    conn.execute = AsyncMock()

    async def raises(**_):
        raise RuntimeError("resend outage")

    with patch(
        "worker.retention_cron.get_db_pool",
        new_callable=AsyncMock,
        return_value=_pool_for(conn),
    ), patch(
        "worker.retention_cron.send_retention_warning_email",
        side_effect=raises,
    ):
        sent = await send_retention_warnings()

    assert sent == 0
    # No UPDATE was issued because the email raised before the stamp step.
    conn.execute.assert_not_called()
