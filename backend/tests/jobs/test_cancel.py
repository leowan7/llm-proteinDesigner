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
from auth.org_dependencies import get_active_org
from main import app


def _override_user(user_id: str = "user-abc"):
    """Return a FastAPI dependency override that returns a fixed user ID."""
    async def _dep():
        return user_id
    return _dep


def _override_active_org(role: str = "scientist", org_id: str = "org-personal"):
    """Phase 12: override get_active_org so cutover require_role gates resolve.

    Returns a (org_id, role) tuple as the real dep would. Tests in this file
    target the user-scoped cancel path which require_role('owner','scientist')
    gates — scientist is the default role for legacy single-tenant tests.
    """
    async def _dep():
        return (org_id, role)
    return _dep


def _make_ctx(conn):
    """Wrap a mock connection in an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_router_pool(job_row, cust_row, owner_row=None):
    """Build the router+service shared pool mock.

    acquire() call sequence (router then service):
      1. router ownership check — fetchrow SELECT id WHERE id=? AND user_id=?
      2. service job fetch — fetchrow full job row
      3. service UPDATE gpu_cost_usd — returns "UPDATE 1"
      4. service customer fetch — fetchrow stripe_customer_id
    """
    if owner_row is None:
        owner_row = {"id": "job-owned"}

    owner_conn = AsyncMock()
    owner_conn.fetchrow = AsyncMock(return_value=owner_row)

    job_conn = AsyncMock()
    job_conn.fetchrow = AsyncMock(return_value=job_row)

    exec_conn = AsyncMock()
    exec_conn.execute = AsyncMock(return_value="UPDATE 1")

    cust_conn = AsyncMock()
    cust_conn.fetchrow = AsyncMock(return_value=cust_row)

    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[
        _make_ctx(owner_conn),
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
            "job_spec": json.dumps({"tool": "bindcraft"}),
            "started_at": started_at,
            "user_id": "user-abc",
        }
        router_pool = _make_router_pool(job_row, {"stripe_customer_id": "cus_test"})

        mock_provider = AsyncMock()
        mock_provider.cancel_job = AsyncMock()

        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        app.dependency_overrides[get_active_org] = _override_active_org()
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.service.get_provider", return_value=mock_provider),
                patch("jobs.service.record_gpu_usage"),
                patch("worker.tasks.update_job_status", new_callable=AsyncMock),
                patch("worker.tasks.publish_status", new_callable=AsyncMock),
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
            app.dependency_overrides.pop(get_active_org, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    @pytest.mark.anyio
    async def test_cancel_calls_provider_cancel(self):
        """Verify provider.cancel_job is called with the job's runpod_job_id."""
        started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
        job_row = {
            "runpod_job_id": "rp-job-xyz",
            "job_spec": json.dumps({"tool": "bindcraft"}),
            "started_at": started_at,
            "user_id": "user-abc",
        }
        router_pool = _make_router_pool(job_row, {"stripe_customer_id": "cus_test"})

        mock_provider = AsyncMock()
        mock_provider.cancel_job = AsyncMock()

        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        app.dependency_overrides[get_active_org] = _override_active_org()
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.service.get_provider", return_value=mock_provider),
                patch("jobs.service.record_gpu_usage"),
                patch("worker.tasks.update_job_status", new_callable=AsyncMock),
                patch("worker.tasks.publish_status", new_callable=AsyncMock),
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
            app.dependency_overrides.pop(get_active_org, None)

        mock_provider.cancel_job.assert_called_once()
        call_args = mock_provider.cancel_job.call_args
        # Second positional arg is the runpod_job_id (aka provider_job_id).
        assert call_args[0][1] == "rp-job-xyz"

    @pytest.mark.anyio
    async def test_cancel_records_partial_billing(self):
        """Verify record_gpu_usage is called with (customer_id, job_id, gpu_seconds)
        for the partial GPU time consumed before cancellation.
        """
        started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)
        job_row = {
            "runpod_job_id": "rp-job-partial",
            "job_spec": json.dumps({"tool": "bindcraft"}),
            "started_at": started_at,
            "user_id": "user-abc",
        }
        router_pool = _make_router_pool(job_row, {"stripe_customer_id": "cus_partial"})

        mock_provider = AsyncMock()
        mock_provider.cancel_job = AsyncMock()
        mock_record = MagicMock()

        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        app.dependency_overrides[get_active_org] = _override_active_org()
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.service.get_provider", return_value=mock_provider),
                patch("jobs.service.record_gpu_usage", mock_record),
                patch("worker.tasks.update_job_status", new_callable=AsyncMock),
                patch("worker.tasks.publish_status", new_callable=AsyncMock),
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
            app.dependency_overrides.pop(get_active_org, None)

        data = response.json()
        gpu_seconds = data["gpu_seconds"]
        # Job ran for ~120 seconds — billing must reflect partial time (> 0).
        assert gpu_seconds > 0
        mock_record.assert_called_once_with("cus_partial", "job-partial", gpu_seconds)
