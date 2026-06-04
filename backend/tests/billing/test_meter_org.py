"""Phase 12 Plan 12-03 — meter events use the org's Stripe customer, not the user's.

Covers ORG-04:
- record_gpu_usage passes the org-resolved customer through to the Stripe
  Billing Meter API
- get_or_create_customer UPDATEs public.organizations (not public.users) when
  a new Stripe customer is created
- Stripe metadata stamps organization_id + kendrew_org_name
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


async def test_meter_event_uses_org_customer_id():
    """ORG-04: meter event payload uses the customer ID passed in.

    record_gpu_usage is a pure pass-through to Stripe; the caller (webhook
    handler / cancel service) resolves the customer via JOIN through
    jobs.organization_id and passes it here. This test asserts the meter
    payload picks up the org-resolved customer string verbatim.
    """
    from billing.stripe_client import record_gpu_usage

    with patch("billing.stripe_client.stripe.billing.MeterEvent.create") as mock_meter:
        record_gpu_usage("cus_org_xxx", "job-uuid", 120)
        assert mock_meter.called
        kwargs = mock_meter.call_args.kwargs
        assert kwargs["payload"]["stripe_customer_id"] == "cus_org_xxx"
        assert kwargs["payload"]["value"] == "120"  # Stripe requires string
        assert kwargs["idempotency_key"] == "gpu_usage_job-uuid"


async def test_get_or_create_customer_writes_org_table():
    """get_or_create_customer UPDATEs public.organizations (not public.users)
    and stamps Stripe metadata with organization_id + kendrew_org_name."""
    from billing.stripe_client import get_or_create_customer

    captured = {"fetchrow_queries": [], "execute_queries": []}

    async def _fetchrow(query, *args):
        captured["fetchrow_queries"].append(query)
        return None  # No existing customer

    async def _execute(query, *args):
        captured["execute_queries"].append((query, args))
        return "OK"

    pool = AsyncMock()
    pool.fetchrow = _fetchrow
    pool.execute = _execute

    with patch("billing.stripe_client.stripe.Customer.create") as mock_create:
        mock_create.return_value = MagicMock(id="cus_new_xxx")
        result = await get_or_create_customer(
            email="owner@acme.bio",
            org_id="org-uuid-123",
            org_name="Acme Bio",
            pool=pool,
        )

    assert result == "cus_new_xxx"

    # The SELECT must read public.organizations (not public.users)
    assert any("FROM public.organizations" in q for q in captured["fetchrow_queries"])
    assert not any("FROM public.users" in q for q in captured["fetchrow_queries"])

    # The UPDATE must write public.organizations
    update_queries = [q for q, _ in captured["execute_queries"] if "UPDATE" in q]
    assert len(update_queries) == 1
    assert "UPDATE public.organizations" in update_queries[0]
    assert "UPDATE public.users" not in update_queries[0]

    # Stripe metadata stamped with org context
    create_kwargs = mock_create.call_args.kwargs
    assert create_kwargs["metadata"]["organization_id"] == "org-uuid-123"
    assert create_kwargs["metadata"]["kendrew_org_name"] == "Acme Bio"


async def test_get_or_create_customer_returns_existing_id_without_stripe_call():
    """When public.organizations.stripe_customer_id is already populated,
    skip the Stripe API call and return the cached ID."""
    from billing.stripe_client import get_or_create_customer

    async def _fetchrow(query, *args):
        # Pretend the org already has a Stripe customer
        return {"stripe_customer_id": "cus_org_existing"}

    async def _execute(query, *args):
        return "OK"

    pool = AsyncMock()
    pool.fetchrow = _fetchrow
    pool.execute = _execute

    with patch("billing.stripe_client.stripe.Customer.create") as mock_create:
        result = await get_or_create_customer(
            email="owner@acme.bio",
            org_id="org-uuid-123",
            org_name="Acme Bio",
            pool=pool,
        )

    assert result == "cus_org_existing"
    assert not mock_create.called  # Skipped Stripe entirely
