"""Tests for the shared cancel_job_by_id service (jobs/service.py).

Covers:
- Success path: running job is cancelled, GPU seconds and cost returned
- Not-found path: no running job raises HTTPException 404
- Billing: record_gpu_usage called with correct gpu_seconds when job has started_at

These tests call cancel_job_by_id directly (not via HTTP) so they do not
require FastAPI TestClient or dependency_overrides.
"""
import os

os.environ.setdefault("TESTING", "true")

import datetime
import decimal
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jobs.service import cancel_job_by_id


def _make_ctx(conn):
    """Wrap a mock connection in an async context manager.

    Args:
        conn: The mock asyncpg connection to wrap.

    Returns:
        AsyncMock configured for 'async with pool.acquire() as conn'.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_pool_for_cancel(job_row, stripe_customer_id=None):
    """Build the pool mock expected by cancel_job_by_id.

    cancel_job_by_id makes these pool.acquire() calls in order:
      1. fetchrow  — fetch job row (running check)
      2. execute   — UPDATE jobs SET gpu_cost_usd
      3. fetchval  — resolve stripe_customer_id (if gpu_seconds > 0)

    The third call is a scalar fetchval, not a fetchrow: the service resolves
    the customer through public.org_stripe_customer(), which returns one value.

    Args:
        job_row: Dict or None for the first fetchrow (job lookup).
        stripe_customer_id: Customer string or None for the third call.

    Returns:
        AsyncMock pool with side_effect covering all three acquire() calls.
    """
    # Connection 1: job lookup
    job_conn = AsyncMock()
    job_conn.fetchrow = AsyncMock(return_value=job_row)
    job_conn.execute = AsyncMock()

    # Connection 2: UPDATE gpu_cost_usd — asyncpg returns "UPDATE <n>" string
    update_conn = AsyncMock()
    update_conn.execute = AsyncMock(return_value="UPDATE 1")

    # Connection 3: stripe customer resolution (scalar)
    cust_conn = AsyncMock()
    cust_conn.fetchval = AsyncMock(return_value=stripe_customer_id)
    cust_conn.execute = AsyncMock()

    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[
        _make_ctx(job_conn),
        _make_ctx(update_conn),
        _make_ctx(cust_conn),
    ])
    return pool


async def test_cancel_job_by_id_success():
    """Running job is cancelled and returns status='cancelled' with gpu_seconds > 0."""
    started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
    job_row = {
        "runpod_job_id": "rp-job-001",
        "job_spec": json.dumps({"tool": "rfdiffusion"}),
        "started_at": started_at,
        "user_id": "uid-1",
    }
    mock_pool = _make_pool_for_cancel(job_row, "cus_test")

    mock_provider = AsyncMock()
    mock_provider.cancel_job = AsyncMock()

    with (
        patch("jobs.service.get_provider", return_value=mock_provider),
        patch("jobs.service.record_gpu_usage"),
        patch("worker.tasks.update_job_status", new_callable=AsyncMock),
        patch("worker.tasks.publish_status", new_callable=AsyncMock),
    ):
        result = await cancel_job_by_id("job-001", mock_pool)

    assert result["status"] == "cancelled"
    assert result["gpu_seconds"] > 0
    assert result["gpu_cost_usd"] > 0


async def test_cancel_job_not_found():
    """Job not found (or not running) raises HTTPException 404."""
    mock_pool = _make_pool_for_cancel(job_row=None)

    with pytest.raises(HTTPException) as exc_info:
        await cancel_job_by_id("nonexistent", mock_pool)

    assert exc_info.value.status_code == 404


async def test_a_cancel_that_loses_the_claim_reports_the_row_that_won():
    """A cancel landing on a row another route already took settles nothing.

    The reaper meters the runs it sweeps (worker/cleanup.py:376), so a cancel
    arriving just after a sweep claimed the same row would otherwise be the
    second charge for one job, on a different second count -- the reaper's
    capped figure against this route's wall-clock one. Stripe's idempotency
    key is a backstop with a 24-hour expiry
    (billing/stripe_client.py:219), not the guard; the guard is the status
    transition, which this route now has to win.

    Behind the claim: the cost write, the Stripe meter, and the SSE terminal
    event, which the winning route publishes itself (webhooks/router.py:312,
    worker/cleanup.py:326). The 404 lookup and the provider cancel are in
    front of it and still run. The response body reports the winning row --
    jobs/router.py:736 hands these three fields to the caller and
    admin/router.py:389 writes two of them into the audit log, so returning a
    cancellation that did not happen would be an audited charge nobody made.
    """
    started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
    job_row = {
        "runpod_job_id": "rp-job-003",
        "job_spec": json.dumps({"tool": "bindcraft"}),
        "started_at": started_at,
        "user_id": "uid-3",
    }

    # Connection 1: the job lookup. Connection 2: the read-back of the row the
    # winning route settled. The reaper's figures, so the seconds differ from
    # this route's 60 and the cost arrives as the Decimal asyncpg returns for
    # a numeric column.
    lookup_conn = AsyncMock()
    lookup_conn.fetchrow = AsyncMock(return_value=job_row)
    settled_conn = AsyncMock()
    settled_conn.fetchrow = AsyncMock(return_value={
        "status": "failed",
        "gpu_seconds": 1800,
        "gpu_cost_usd": decimal.Decimal("0.8253"),
    })
    settled_conn.execute = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(side_effect=[
        _make_ctx(lookup_conn),
        _make_ctx(settled_conn),
    ])

    mock_provider = AsyncMock()
    mock_provider.cancel_job = AsyncMock()
    mock_record = MagicMock()

    with (
        patch("jobs.service.get_provider", return_value=mock_provider),
        patch("jobs.service.record_gpu_usage", mock_record),
        patch(
            "worker.tasks.update_job_status",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_update,
        patch("worker.tasks.publish_status", new_callable=AsyncMock) as mock_publish,
    ):
        result = await cancel_job_by_id("job-003", mock_pool)

    # Claimed, and claimed as a claim.
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["require_non_terminal"] is True

    # In front of the claim: the provider cancel still ran.
    mock_provider.cancel_job.assert_called_once()

    # Behind it: no cost UPDATE, no customer resolution, no meter, no SSE
    # event. Two connections were taken -- the lookup and the read-back -- and
    # the second one issued no write.
    assert mock_pool.acquire.call_count == 2
    settled_conn.execute.assert_not_called()
    mock_record.assert_not_called()
    mock_publish.assert_not_called()

    # The body reports the winning row, not this call's computed figures.
    assert result["status"] == "failed"
    assert result["gpu_seconds"] == 1800
    assert result["gpu_cost_usd"] == 0.8253
    assert isinstance(result["gpu_cost_usd"], float)
    assert result["user_id"] == "uid-3"


async def test_cancel_records_billing():
    """cancel_job_by_id calls record_gpu_usage with the elapsed GPU seconds.

    Job started 60 seconds ago — billing must record a positive gpu_seconds value.
    """
    started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
    job_row = {
        "runpod_job_id": "rp-job-002",
        "job_spec": json.dumps({"tool": "bindcraft"}),
        "started_at": started_at,
        "user_id": "uid-2",
    }
    mock_pool = _make_pool_for_cancel(job_row, "cus_billing")

    mock_provider = AsyncMock()
    mock_provider.cancel_job = AsyncMock()
    mock_record = MagicMock()

    with (
        patch("jobs.service.get_provider", return_value=mock_provider),
        patch("jobs.service.record_gpu_usage", mock_record),
        patch("worker.tasks.update_job_status", new_callable=AsyncMock),
        patch("worker.tasks.publish_status", new_callable=AsyncMock),
    ):
        result = await cancel_job_by_id("job-002", mock_pool)

    gpu_seconds = result["gpu_seconds"]
    assert gpu_seconds > 0
    # record_gpu_usage called with (stripe_customer_id, job_id, gpu_seconds)
    mock_record.assert_called_once_with("cus_billing", "job-002", gpu_seconds)
