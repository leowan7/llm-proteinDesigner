"""Tests for orphan pod cleanup and stale job detection.

Covers:
- cleanup_orphan_pods terminates pods running > MAX_POD_LIFETIME_SECONDS
- cleanup_orphan_pods does nothing when no orphaned pods exist
- detect_stale_jobs marks stale running jobs as failed and sends emails
- detect_stale_jobs does nothing when no stale jobs exist
- detect_stale_jobs meters GPU time for a stale run that banked designs, and
  only for one that did (Leo's 10-13 rule: this route delivers designs, so it
  charges for them), and never for a row another route already claimed

Patches db.connection.get_db_pool, gpu.runpod.RunPodProvider,
jobs.notifications.send_failure_email, and worker.tasks.publish_status_best_effort
(the reaper's terminal publish, which is deliberately swallowed -- see
worker/tasks.py:49).
"""
import os

os.environ.setdefault("TESTING", "true")

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import stripe
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
NOW_UTC = datetime.datetime.now(datetime.UTC)

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
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
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
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
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
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
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
    - send_failure_email called, carrying the count of designs the run banked
      before it went stale (this is the route a timed-out run takes, so it is
      the one that has to announce them)
    - the GPU time is metered, because the run banked designs and this route
      now delivers them, and metered at the capped figure rather than wall
      time to now
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

    # Conn 2: UPDATE job status to failed, returning the claimed id
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value="job-stale-uuid")

    # Conn 3: SELECT org_stripe_customer for the meter event
    conn3 = AsyncMock()
    conn3.fetchval = AsyncMock(return_value="cus_stale")

    # Conn 4: SELECT user email
    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value=user_row)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1),
        _make_ctx(conn2),
        _make_ctx(conn3),
        _make_ctx(conn4),
    ])

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.publish_status_best_effort", new_callable=AsyncMock) as mock_publish,
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.record_gpu_usage") as mock_billing,
        # Unpatched this reaches boto3 against an unreachable endpoint and
        # burns its retry budget per stale job.
        patch(
            "worker.cleanup.list_job_design_keys",
            return_value=[
                "users/user-stale/jobs/job-stale-uuid/outputs/design_1.pdb",
                "users/user-stale/jobs/job-stale-uuid/outputs/design_2.pdb",
            ],
        ) as mock_list,
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    assert count == 1

    # DB was updated to failed, and only from 'running' -- the guard that
    # keeps this route and the webhook route to one charge between them
    conn2.fetchval.assert_called_once()
    execute_sql = conn2.fetchval.call_args[0][0]
    assert "failed" in execute_sql
    assert "status = 'running'" in execute_sql
    assert "RETURNING id" in execute_sql

    # SSE failure event published
    mock_publish.assert_called_once()
    assert mock_publish.call_args[0][1] == "failed"

    # Failure email sent
    mock_email.assert_called_once()
    assert mock_email.call_args[1]["to_email"] == "stale@example.com"

    # ...and it names the designs the run banked, read under the launcher's
    # own storage prefix rather than the caller's.
    assert mock_email.call_args[1]["saved_designs"] == 2
    assert mock_list.call_args[0] == ("user-stale", "job-stale-uuid")

    # The customer comes from mark_metered, so the spend reads count the run.
    assert "SET metered = TRUE" in conn3.fetchval.call_args.args[0]

    # ...and the run is billed, because it banked designs this route now
    # hands over. Before 2026-10-13 this module metered nothing at all.
    mock_billing.assert_called_once()
    customer, billed_job, billed_seconds = mock_billing.call_args[0]
    assert customer == "cus_stale"
    assert billed_job == "job-stale-uuid"
    # The capped figure, not wall time to now: this job never sent a
    # heartbeat, so it is billed from started_at to started_at + threshold.
    assert billed_seconds == STALE_HEARTBEAT_SECONDS

    # ...and the charge is on the job row, not only in Stripe. This sweep
    # wrote gpu_seconds and no cost until it started metering, which was
    # harmless while it billed nothing. A real charge against a NULL cost
    # shows the customer's run as free (GET /jobs serves this column,
    # jobs/router.py:496) and check_daily_gpu_spend in this module
    # sums it as zero (worker/cleanup.py:415), so a burst of billed
    # timeouts could not trip the alert.
    assert "gpu_cost_usd = $3" in execute_sql
    # 1800 billable seconds at the rates this test sets: 0.0000917/s + 400%.
    assert conn2.fetchval.call_args[0][3] == 0.8253


async def test_a_stale_job_whose_meter_call_raises_is_left_unmetered():
    """A Stripe refusal clears jobs.metered, and the customer is still mailed."""
    stale_job_row = {
        "id": "job-stale-uuid",
        "user_id": "user-stale",
        "started_at": STALE_STARTED_AT,
        "last_heartbeat_at": None,
        "runpod_job_id": "pod-stale-xyz",
    }
    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[stale_job_row])
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value="job-stale-uuid")
    conn3 = AsyncMock()
    conn3.fetchval = AsyncMock(return_value="cus_stale")
    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value={"email": "stale@example.com"})

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1), _make_ctx(conn2), _make_ctx(conn3), _make_ctx(conn4),
    ])

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=AsyncMock()),
        patch("worker.cleanup.publish_status_best_effort", new_callable=AsyncMock),
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch(
            "worker.cleanup.record_gpu_usage",
            side_effect=stripe.InvalidRequestError("No such customer", None),
        ) as mock_billing,
        patch(
            "worker.cleanup.list_job_design_keys",
            return_value=["users/user-stale/jobs/job-stale-uuid/outputs/design_1.pdb"],
        ),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    assert count == 1
    mock_billing.assert_called_once()
    assert "SET metered = FALSE" in conn3.execute.call_args.args[0]
    mock_email.assert_called_once()


async def test_a_stale_job_that_saved_nothing_is_not_billed():
    """The zero-designs side of the 10-13 rule on the reaper route.

    A run killed from outside that banked no design delivers nothing, so it
    costs nothing -- the behaviour every stale job had before this change.
    The same sweep still fails the row, cancels the pod and mails the
    customer, so the only thing the empty prefix changes is the charge.
    """
    stale_job_row = {
        "id": "job-empty-uuid",
        "user_id": "user-empty",
        "started_at": STALE_STARTED_AT,
        "last_heartbeat_at": None,
        "runpod_job_id": "pod-empty",
    }

    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[stale_job_row])
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value="job-empty-uuid")
    conn3 = AsyncMock()
    conn3.fetchrow = AsyncMock(return_value={"email": "empty@example.com"})

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1), _make_ctx(conn2), _make_ctx(conn3),
    ])

    mock_provider = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.publish_status_best_effort", new_callable=AsyncMock),
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.record_gpu_usage") as mock_billing,
        patch("worker.cleanup.list_job_design_keys", return_value=[]),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    assert count == 1
    mock_billing.assert_not_called()
    # The customer still hears about the failure, with nothing to collect.
    mock_email.assert_called_once()
    assert mock_email.call_args[1]["saved_designs"] == 0
    # Three: the stale SELECT, the claiming UPDATE, and the email SELECT
    # (worker/cleanup.py:387) that every swept job gets. The fourth the billed
    # path opens -- the Stripe customer lookup at worker/cleanup.py:371-372 -- is
    # skipped, because that block is gated on saved_designs > 0.
    assert mock_pool.acquire.call_count == 3


async def test_a_stale_job_another_route_already_finished_is_not_billed_or_mailed():
    """The webhook-then-reaper half of one-charge-per-job.

    The sweep's SELECT sees 'running' and a webhook for the same job lands
    before the sweep's UPDATE. The guarded UPDATE then claims nothing, which
    is the signal that the other route already billed and mailed this job.
    Nothing here may repeat either. The pod cancel still runs: an orphaned pod
    bills the company until something kills it, which is what this module is
    for.
    """
    stale_job_row = {
        "id": "job-raced-uuid",
        "user_id": "user-raced",
        "started_at": STALE_STARTED_AT,
        "last_heartbeat_at": None,
        "runpod_job_id": "pod-raced",
    }

    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[stale_job_row])
    # The UPDATE matched no row: the status was no longer 'running'.
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value=None)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[_make_ctx(conn1), _make_ctx(conn2)])

    mock_provider = AsyncMock()
    mock_provider.cancel_job = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        patch("worker.cleanup.publish_status_best_effort", new_callable=AsyncMock) as mock_publish,
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.record_gpu_usage") as mock_billing,
        patch("worker.cleanup.list_job_design_keys", return_value=["a.pdb"]) as mock_list,
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    # No second charge, no second mail, no second SSE event, and not counted
    # as killed -- even though Storage holds a design.
    mock_billing.assert_not_called()
    mock_email.assert_not_called()
    mock_publish.assert_not_called()
    mock_list.assert_not_called()
    assert count == 0
    # The pod is still cancelled, which is this module's stated purpose.
    mock_provider.cancel_job.assert_called_once()


async def test_a_storage_fault_leaves_a_stale_job_unbilled():
    """An unreadable bucket must not become a charge on this route either.

    The count is what the charge rests on, so a listing that raises has to
    leave it at zero rather than guess. The customer still gets the plain
    failure mail and the sweep still finishes.
    """
    stale_job_row = {
        "id": "job-fault-uuid",
        "user_id": "user-fault",
        "started_at": STALE_STARTED_AT,
        "last_heartbeat_at": None,
        "runpod_job_id": None,
    }

    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[stale_job_row])
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value="job-fault-uuid")
    conn3 = AsyncMock()
    conn3.fetchrow = AsyncMock(return_value={"email": "fault@example.com"})

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1), _make_ctx(conn2), _make_ctx(conn3),
    ])

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=AsyncMock()),
        patch("worker.cleanup.publish_status_best_effort", new_callable=AsyncMock),
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.record_gpu_usage") as mock_billing,
        patch(
            "worker.cleanup.list_job_design_keys",
            side_effect=OSError("bucket unreachable"),
        ),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    assert count == 1
    mock_billing.assert_not_called()
    mock_email.assert_called_once()
    assert mock_email.call_args[1]["saved_designs"] == 0


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
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    assert count == 0
    mock_email.assert_not_called()


async def test_a_redis_fault_on_the_reapers_publish_still_bills_and_mails():
    """The reaper settles the job even when the SSE event cannot be sent.

    This is test_cleanup_stale_jobs with one thing changed: the publish at
    worker/cleanup.py:328 raises. The claim UPDATE above it has already
    committed, and no later sweep selects the row again (``WHERE status =
    'running'``, worker/cleanup.py:203), so an exception out of the publish
    would strand a row stamped with a gpu_cost_usd nothing metered and leave
    the customer unmailed about a run that ended.
    """
    stale_job_row = {
        "id": "job-stale-uuid",
        "user_id": "user-stale",
        "started_at": STALE_STARTED_AT,
        "last_heartbeat_at": None,
        "runpod_job_id": "pod-stale-xyz",
    }

    conn1 = AsyncMock()
    conn1.fetch = AsyncMock(return_value=[stale_job_row])
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value="job-stale-uuid")
    conn3 = AsyncMock()
    conn3.fetchval = AsyncMock(return_value="cus_stale")
    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value={"email": "stale@example.com"})

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(conn1),
        _make_ctx(conn2),
        _make_ctx(conn3),
        _make_ctx(conn4),
    ])

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("worker.cleanup.get_provider", return_value=mock_provider),
        # The fault goes inside the real wrapper, not over it.
        patch(
            "worker.tasks.publish_status",
            new_callable=AsyncMock,
            side_effect=RuntimeError("redis unreachable"),
        ) as mock_publish,
        patch("worker.tasks.sentry_sdk.capture_exception") as mock_sentry,
        patch("worker.cleanup.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("worker.cleanup.record_gpu_usage") as mock_billing,
        patch(
            "worker.cleanup.list_job_design_keys",
            return_value=[
                "users/user-stale/jobs/job-stale-uuid/outputs/design_1.pdb",
            ],
        ),
        patch("worker.cleanup.settings") as mock_settings,
    ):
        mock_settings.runpod_api_key = "test-api-key"
        mock_settings.gpu_price_per_second = 0.0000917
        mock_settings.gpu_markup_percent = 400.0
        count = await detect_stale_jobs()

    # The sweep finished, and the fault was real and reported.
    assert count == 1
    mock_publish.assert_awaited_once()
    mock_sentry.assert_called_once()

    # Everything below the publish still ran.
    mock_billing.assert_called_once()
    assert mock_billing.call_args[0][0] == "cus_stale"
    mock_email.assert_called_once()
    assert mock_email.call_args[1]["to_email"] == "stale@example.com"
    assert mock_email.call_args[1]["saved_designs"] == 1
