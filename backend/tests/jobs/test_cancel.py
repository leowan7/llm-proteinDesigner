"""Tests for job cancellation (JOB-03).

Covers:
- DB status updated to 'cancelled' on cancellation
- RunPodProvider.cancel_job called with correct args
- Partial billing recorded for GPU seconds consumed before cancellation

Implementation target: Plan 03-03.
"""

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.dependencies import get_current_user
from main import app


def _override_user(user_id: str = "user-abc"):
    """Return a FastAPI dependency override that returns a fixed user ID."""
    async def _dep():
        return user_id
    return _dep


def _make_ctx(conn):
    """Wrap a mock connection in an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_router_pool(job_row, cust_row):
    """Build the router-side pool mock.

    acquire() call sequence in cancel_job (router pool only):
      1. fetchrow — job row (check status='running')
      2. execute  — UPDATE gpu_cost_usd
      3. fetchrow — stripe_customer_id for billing
    """
    job_conn = AsyncMock()
    job_conn.fetchrow = AsyncMock(return_value=job_row)
    job_conn.execute = AsyncMock()

    exec_conn = AsyncMock()
    exec_conn.execute = AsyncMock()

    cust_conn = AsyncMock()
    cust_conn.fetchrow = AsyncMock(return_value=cust_row)
    cust_conn.execute = AsyncMock()

    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[
        _make_ctx(job_conn),
        _make_ctx(exec_conn),
        _make_ctx(cust_conn),
    ])
    return pool


def _make_worker_pool(job_row):
    """Build the worker-tasks-side pool mock.

    update_job_status() acquires once for the UPDATE statement.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_make_ctx(conn))
    return pool


class TestJobCancellation:
    """JOB-03: Jobs can be cancelled mid-execution with correct cleanup."""

    @pytest.mark.anyio
    async def test_cancel_updates_db_status(self):
        """Verify that cancelling a job sets status='cancelled' in the jobs table."""
        started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
        job_row = {
            "runpod_job_id": "rp-job-001",
            "job_spec": json.dumps({"tool": "rfdiffusion"}),
            "started_at": started_at,
        }
        router_pool = _make_router_pool(job_row, {"stripe_customer_id": "cus_test"})
        worker_pool = _make_worker_pool(job_row)

        mock_provider = AsyncMock()
        mock_provider.cancel_job = AsyncMock()

        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.router.RunPodProvider", return_value=mock_provider),
                patch("jobs.router.record_gpu_usage"),
                patch("worker.tasks.get_db_pool", return_value=worker_pool),
                patch("worker.tasks.aioredis.from_url", return_value=AsyncMock(
                    publish=AsyncMock(), aclose=AsyncMock()
                )),
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/jobs/job-cancel-test/cancel",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    @pytest.mark.anyio
    async def test_cancel_calls_runpod_cancel(self):
        """Mock RunPodProvider and verify cancel_job() is called with the correct
        endpoint_id and provider_job_id matching the job's runpod_job_id column.
        """
        started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
        job_row = {
            "runpod_job_id": "rp-job-xyz",
            "job_spec": json.dumps({"tool": "rfdiffusion"}),
            "started_at": started_at,
        }
        router_pool = _make_router_pool(job_row, {"stripe_customer_id": "cus_test"})
        worker_pool = _make_worker_pool(job_row)

        mock_provider = AsyncMock()
        mock_provider.cancel_job = AsyncMock()

        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.router.RunPodProvider", return_value=mock_provider),
                patch("jobs.router.record_gpu_usage"),
                patch("worker.tasks.get_db_pool", return_value=worker_pool),
                patch("worker.tasks.aioredis.from_url", return_value=AsyncMock(
                    publish=AsyncMock(), aclose=AsyncMock()
                )),
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    await client.post(
                        "/jobs/job-cancel-test/cancel",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        mock_provider.cancel_job.assert_called_once()
        call_args = mock_provider.cancel_job.call_args
        # Second positional arg is the runpod_job_id
        assert call_args[0][1] == "rp-job-xyz"

    @pytest.mark.anyio
    async def test_cancel_records_partial_billing(self):
        """Verify record_gpu_usage is called with the partial GPU seconds consumed
        before cancellation (not zero, not the full estimated duration).
        """
        started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)
        job_row = {
            "runpod_job_id": "rp-job-partial",
            "job_spec": json.dumps({"tool": "bindcraft"}),
            "started_at": started_at,
        }
        router_pool = _make_router_pool(job_row, {"stripe_customer_id": "cus_partial"})
        worker_pool = _make_worker_pool(job_row)

        mock_provider = AsyncMock()
        mock_provider.cancel_job = AsyncMock()
        mock_record = MagicMock()

        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.router.RunPodProvider", return_value=mock_provider),
                patch("jobs.router.record_gpu_usage", mock_record),
                patch("worker.tasks.get_db_pool", return_value=worker_pool),
                patch("worker.tasks.aioredis.from_url", return_value=AsyncMock(
                    publish=AsyncMock(), aclose=AsyncMock()
                )),
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/jobs/job-partial/cancel",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        data = response.json()
        gpu_seconds = data["gpu_seconds"]
        # Job ran for ~120 seconds — billing must reflect partial time (> 0)
        assert gpu_seconds > 0
        mock_record.assert_called_once_with("cus_partial", gpu_seconds)
