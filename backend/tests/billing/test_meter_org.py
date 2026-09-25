"""Phase 12 Plan 12-03 — meter events use the org's Stripe customer, not the user's.

Covers ORG-04:
- record_gpu_usage passes the org-resolved customer through to the Stripe
  Billing Meter API
- get_or_create_customer resolves the existing customer through
  public.org_stripe_customer() and UPDATEs public.organizations (not
  public.users) when a new Stripe customer is created
- Stripe metadata stamps organization_id + kendrew_org_name

The resolver's own fallback to the deprecated public.users.stripe_customer_id
is SQL, so it is proven at the DB layer in
tests/integration/test_flag_off_rolling_window.py, not here; these tests only
prove the call site asks the resolver rather than reading one column itself.
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

    captured = {"fetchval_queries": [], "execute_queries": []}

    async def _fetchval(query, *args):
        captured["fetchval_queries"].append(query)
        return None  # Neither the org nor the legacy user column has a customer

    async def _execute(query, *args):
        captured["execute_queries"].append((query, args))
        return "OK"

    pool = AsyncMock()
    pool.fetchval = _fetchval
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

    # The read must go through the resolver, which covers both the org column
    # and the deprecated users column during the rolling-deploy window. Reading
    # organizations.stripe_customer_id directly would mint a second Stripe
    # customer for a payer whose id an old replica wrote to the legacy column.
    assert any(
        "public.org_stripe_customer" in q for q in captured["fetchval_queries"]
    ), captured["fetchval_queries"]

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
    """When the resolver returns a customer, skip the Stripe API call and
    return that ID."""
    from billing.stripe_client import get_or_create_customer

    async def _fetchval(query, *args):
        # Pretend the resolver found a customer (on the org or the legacy column)
        return "cus_org_existing"

    async def _execute(query, *args):
        return "OK"

    pool = AsyncMock()
    pool.fetchval = _fetchval
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
