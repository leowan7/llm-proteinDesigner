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


def _make_pool_for_cancel(job_row, cust_row=None):
    """Build the pool mock expected by cancel_job_by_id.

    cancel_job_by_id makes these pool.acquire() calls in order:
      1. fetchrow  — fetch job row (running check)
      2. execute   — UPDATE jobs SET gpu_cost_usd
      3. fetchrow  — fetch stripe_customer_id (if gpu_seconds > 0)

    Args:
        job_row: Dict or None for the first fetchrow (job lookup).
        cust_row: Dict or None for the third fetchrow (customer lookup).

    Returns:
        AsyncMock pool with side_effect covering all three acquire() calls.
    """
    # Connection 1: job lookup
    job_conn = AsyncMock()
    job_conn.fetchrow = AsyncMock(return_value=job_row)
    job_conn.execute = AsyncMock()

    # Connection 2: UPDATE gpu_cost_usd
    update_conn = AsyncMock()
    update_conn.execute = AsyncMock()

    # Connection 3: stripe customer lookup
    cust_conn = AsyncMock()
    cust_conn.fetchrow = AsyncMock(return_value=cust_row)
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
    started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
    job_row = {
        "runpod_job_id": "rp-job-001",
        "job_spec": json.dumps({"tool": "rfdiffusion"}),
        "started_at": started_at,
        "user_id": "uid-1",
    }
    cust_row = {"stripe_customer_id": "cus_test"}
    mock_pool = _make_pool_for_cancel(job_row, cust_row)

    mock_provider = AsyncMock()
    mock_provider.cancel_job = AsyncMock()

    with (
        patch("jobs.service.RunPodProvider", return_value=mock_provider),
        patch("jobs.service.record_gpu_usage"),
        patch("worker.tasks.get_db_pool", return_value=AsyncMock(
            acquire=MagicMock(return_value=_make_ctx(AsyncMock(execute=AsyncMock())))
        )),
        patch("worker.tasks.aioredis.from_url", return_value=AsyncMock(
            publish=AsyncMock(), aclose=AsyncMock()
        )),
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


async def test_cancel_records_billing():
    """cancel_job_by_id calls record_gpu_usage with the elapsed GPU seconds.

    Job started 60 seconds ago — billing must record a positive gpu_seconds value.
    """
    started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
    job_row = {
        "runpod_job_id": "rp-job-002",
        "job_spec": json.dumps({"tool": "bindcraft"}),
        "started_at": started_at,
        "user_id": "uid-2",
    }
    cust_row = {"stripe_customer_id": "cus_billing"}
    mock_pool = _make_pool_for_cancel(job_row, cust_row)

    mock_provider = AsyncMock()
    mock_provider.cancel_job = AsyncMock()
    mock_record = MagicMock()

    with (
        patch("jobs.service.RunPodProvider", return_value=mock_provider),
        patch("jobs.service.record_gpu_usage", mock_record),
        patch("worker.tasks.get_db_pool", return_value=AsyncMock(
            acquire=MagicMock(return_value=_make_ctx(AsyncMock(execute=AsyncMock())))
        )),
        patch("worker.tasks.aioredis.from_url", return_value=AsyncMock(
            publish=AsyncMock(), aclose=AsyncMock()
        )),
    ):
        result = await cancel_job_by_id("job-002", mock_pool)

    gpu_seconds = result["gpu_seconds"]
    assert gpu_seconds > 0
    # record_gpu_usage called with (stripe_customer_id, job_id, gpu_seconds)
    mock_record.assert_called_once_with("cus_billing", "job-002", gpu_seconds)
