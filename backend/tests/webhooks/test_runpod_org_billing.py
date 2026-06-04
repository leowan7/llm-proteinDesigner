"""Phase 12 Plan 12-03 — webhook handler routes billing to the org's customer.

Covers ORG-04:
- The webhook completion path resolves the Stripe customer via JOIN through
  jobs.organization_id -> organizations.stripe_customer_id (NOT through
  users.stripe_customer_id which is the pre-cutover path)
- When the org has no stripe_customer_id (e.g. team org that hasn't set up
  Stripe yet), the meter event is skipped silently rather than throwing

These tests inspect the SQL that the webhook handler emits and assert on the
JOIN path, then assert record_gpu_usage is called with the resolved customer.
"""
from __future__ import annotations

import datetime
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


# Match the existing test_router.py timing: 5 min ago -> 300 gpu_seconds.
NOW_UTC = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
STARTED_AT = NOW_UTC - datetime.timedelta(minutes=5)


def _completed_payload() -> bytes:
    return json.dumps({
        "id": "job-1111-2222-3333",
        "pod_id": "pod-abc",
        "status": "COMPLETED",
        "output": {"candidate_count": 0, "candidates": [], "next_steps": ""},
    }).encode()


def _build_pool_capturing_billing(stripe_customer_id: str | None):
    """Build a pool whose fetchrow on the JOIN query returns the given customer.

    Captures all queries so the test can assert the JOIN SQL is the one used
    by the billing-resolution step.
    """
    captured = {"queries": []}

    async def _fetchrow(query, *args):
        captured["queries"].append(query)
        # First fetchrow: job row lookup (id, user_id, started_at, runpod_job_id, tool)
        if "SELECT id, user_id, started_at" in query:
            return {
                "id": "job-1111-2222-3333",
                "user_id": "user-launcher",
                "started_at": STARTED_AT,
                "runpod_job_id": "pod-abc",
                "tool": "bindcraft",
            }
        # Second fetchrow: terminal-state guard
        if "SELECT status FROM public.jobs WHERE id" in query:
            return {"status": "running"}
        # Third fetchrow: the billing-resolution JOIN
        if "JOIN public.organizations" in query:
            return {"stripe_customer_id": stripe_customer_id}
        # email lookup at the end
        if "FROM auth.users" in query:
            return {"email": "launcher@acme.bio"}
        return None

    async def _execute(query, *args):
        return "OK"

    conn = AsyncMock()
    conn.fetchrow = _fetchrow
    conn.execute = _execute

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, captured


async def test_webhook_completion_resolves_customer_via_org_join():
    """ORG-04: webhook handler reads organizations.stripe_customer_id by JOIN
    through jobs.organization_id, then meters against that customer."""
    from httpx import ASGITransport, AsyncClient

    pool, captured = _build_pool_capturing_billing(stripe_customer_id="cus_org_xxx")

    # Patch dependencies + the webhook signature secret to dev-skip mode.
    with patch("webhooks.router.get_db_pool", return_value=pool), \
         patch("webhooks.router.record_gpu_usage") as mock_meter, \
         patch("webhooks.router.update_job_status", new=AsyncMock()), \
         patch("webhooks.router.publish_status", new=AsyncMock()), \
         patch("webhooks.router.send_completion_email", new=AsyncMock()), \
         patch("webhooks.router.send_failure_email", new=AsyncMock()), \
         patch("webhooks.router.get_provider") as mock_provider, \
         patch("webhooks.router.settings") as mock_settings:
        mock_settings.webhook_hmac_secret = ""
        mock_settings.webhook_hmac_secret_prev = ""
        mock_settings.gpu_price_per_second = 0.001
        mock_settings.gpu_markup_percent = 0
        mock_provider.return_value = MagicMock(terminate_pod=AsyncMock())

        # Build a minimal app with just the webhook router mounted.
        from fastapi import FastAPI

        from webhooks.router import router as webhooks_router

        app = FastAPI()
        app.include_router(webhooks_router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/webhooks/runpod", content=_completed_payload())

    assert r.status_code == 200

    # The JOIN query must be the one used for billing resolution.
    join_queries = [q for q in captured["queries"] if "JOIN public.organizations" in q]
    assert join_queries, f"Expected JOIN public.organizations query; got: {captured['queries']}"
    # And it must scope through jobs.organization_id
    assert "j.organization_id" in join_queries[0]

    # record_gpu_usage called with the org-resolved customer
    assert mock_meter.called
    args = mock_meter.call_args.args
    assert args[0] == "cus_org_xxx"


async def test_webhook_completion_skips_billing_if_org_has_no_customer():
    """A team org that hasn't yet set up Stripe — JOIN returns NULL
    stripe_customer_id; no meter event."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _build_pool_capturing_billing(stripe_customer_id=None)

    with patch("webhooks.router.get_db_pool", return_value=pool), \
         patch("webhooks.router.record_gpu_usage") as mock_meter, \
         patch("webhooks.router.update_job_status", new=AsyncMock()), \
         patch("webhooks.router.publish_status", new=AsyncMock()), \
         patch("webhooks.router.send_completion_email", new=AsyncMock()), \
         patch("webhooks.router.send_failure_email", new=AsyncMock()), \
         patch("webhooks.router.get_provider") as mock_provider, \
         patch("webhooks.router.settings") as mock_settings:
        mock_settings.webhook_hmac_secret = ""
        mock_settings.webhook_hmac_secret_prev = ""
        mock_settings.gpu_price_per_second = 0.001
        mock_settings.gpu_markup_percent = 0
        mock_provider.return_value = MagicMock(terminate_pod=AsyncMock())

        from fastapi import FastAPI

        from webhooks.router import router as webhooks_router

        app = FastAPI()
        app.include_router(webhooks_router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/webhooks/runpod", content=_completed_payload())

    assert r.status_code == 200
    assert not mock_meter.called  # No customer => no meter event
