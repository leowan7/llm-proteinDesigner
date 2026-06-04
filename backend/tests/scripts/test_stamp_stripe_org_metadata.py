"""Phase 12 Plan 12-04 -- unit tests for stamp_stripe_org_metadata.py.

Covers dry-run, idempotency, rate-limit retry-then-succeed, per-org failure
isolation, metadata payload shape, --limit cap, and overall exit code.

Mocks the stripe SDK entirely -- no network calls. Mocks asyncpg.create_pool
for the main_async tests so the CLI can be exercised without a real DB.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from contextlib import redirect_stdout
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from scripts import stamp_stripe_org_metadata as m


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ORG_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _row(
    org_id: str = _ORG_UUID,
    name: str = "acme",
    customer_id: str = "cus_xxx",
    is_personal: bool = True,
) -> Any:
    """Build an asyncpg.Record-shaped object via MagicMock + __getitem__.

    asyncpg Records are subscriptable by column name; tests use this rather
    than constructing a real Record (which is a CPython extension type).
    """
    r = MagicMock()
    data = {
        "id": org_id,
        "name": name,
        "stripe_customer_id": customer_id,
        "is_personal": is_personal,
    }
    r.__getitem__.side_effect = lambda key: data[key]
    return r


def _stripe_customer(metadata: dict[str, str] | None = None) -> MagicMock:
    """Mock a stripe.Customer object whose .metadata behaves like the SDK."""
    cust = MagicMock()
    cust.metadata = metadata or {}
    return cust


# ---------------------------------------------------------------------------
# 1. Dry-run does not call Stripe at all
# ---------------------------------------------------------------------------


async def test_dry_run_does_not_call_stripe_modify():
    with patch.object(m.stripe.Customer, "modify") as mod, \
         patch.object(m.stripe.Customer, "retrieve") as ret:
        outcome = await m._process_org(_row(), dry_run=True)
    assert mod.called is False, "dry-run must not call stripe.Customer.modify"
    assert ret.called is False, "dry-run must not call stripe.Customer.retrieve"
    assert outcome["outcome"] == "would-modify"
    assert outcome["target_metadata"]["organization_id"] == _ORG_UUID


# ---------------------------------------------------------------------------
# 2. Idempotency: skip already-tagged customers
# ---------------------------------------------------------------------------


async def test_idempotency_skips_already_tagged():
    existing = _stripe_customer({"organization_id": _ORG_UUID, "stale_key": "x"})
    with patch.object(m.stripe.Customer, "retrieve", return_value=existing) as ret, \
         patch.object(m.stripe.Customer, "modify") as mod:
        outcome = await m._process_org(_row(), dry_run=False)
    assert outcome["outcome"] == "skipped-already-tagged"
    assert ret.called is True, "idempotency check must call retrieve"
    assert mod.called is False, "must not call modify when already tagged"


async def test_modifies_when_organization_id_mismatched():
    # The customer is tagged but with a different org_id -- this is the
    # rebinding case (Stripe customer was once on another org). Script must
    # treat this as a write target, not a skip.
    existing = _stripe_customer({"organization_id": "different-uuid"})
    with patch.object(m.stripe.Customer, "retrieve", return_value=existing), \
         patch.object(m.stripe.Customer, "modify") as mod:
        outcome = await m._process_org(_row(), dry_run=False)
    assert outcome["outcome"] == "modified"
    assert mod.called is True
    # Confirm the new metadata flows through to the Stripe call.
    _, kwargs = mod.call_args
    assert kwargs["metadata"]["organization_id"] == _ORG_UUID


# ---------------------------------------------------------------------------
# 3. Metadata payload shape (4 keys; matches 12-03 get_or_create_customer +
#    audit columns)
# ---------------------------------------------------------------------------


async def test_metadata_includes_all_four_keys():
    meta = m._build_metadata("org-uuid", "Acme Bio", True)
    assert set(meta.keys()) == {
        "organization_id",
        "kendrew_org_name",
        "is_personal",
        "migrated_from_user_v1",
    }
    assert meta["organization_id"] == "org-uuid"
    assert meta["kendrew_org_name"] == "Acme Bio"
    assert meta["is_personal"] == "true"
    # migrated_from_user_v1 is today's ISO date -- be loose; just check shape.
    assert len(meta["migrated_from_user_v1"]) == 10


async def test_metadata_is_personal_false_renders_as_false_string():
    meta = m._build_metadata("org-uuid", "Acme Bio", False)
    assert meta["is_personal"] == "false"


# ---------------------------------------------------------------------------
# 4. Failure isolation: a Stripe error on one org is captured, not propagated
# ---------------------------------------------------------------------------


async def test_failed_outcome_returned_when_stripe_errors():
    err = stripe.error.InvalidRequestError("no such customer", "id")
    with patch.object(m.stripe.Customer, "retrieve", side_effect=err):
        outcome = await m._process_org(_row(), dry_run=False)
    assert outcome["outcome"] == "failed"
    assert "no such customer" in outcome["error"]


# ---------------------------------------------------------------------------
# 5. Rate-limit retry: first attempt 429, second attempt succeeds
# ---------------------------------------------------------------------------


async def test_rate_limit_retries_then_succeeds():
    existing = _stripe_customer({})  # not yet tagged -> will trigger modify
    rate_err = stripe.error.RateLimitError("slow down")
    # First retrieve raises RateLimitError; second succeeds.
    ret_mock = patch.object(
        m.stripe.Customer, "retrieve", side_effect=[rate_err, existing]
    )
    mod_mock = patch.object(
        m.stripe.Customer, "modify", return_value=MagicMock()
    )
    sleep_mock = patch.object(m.time, "sleep")
    with ret_mock as ret, mod_mock as mod, sleep_mock as slp:
        outcome = await m._process_org(_row(), dry_run=False)
    assert outcome["outcome"] == "modified"
    assert ret.call_count == 2
    assert mod.call_count == 1
    assert slp.called, "must back off (time.sleep) between rate-limit retries"


async def test_rate_limit_exhausted_returns_failed():
    rate_err = stripe.error.RateLimitError("slow down")
    ret_mock = patch.object(
        m.stripe.Customer, "retrieve", side_effect=[rate_err] * m.MAX_RETRIES
    )
    sleep_mock = patch.object(m.time, "sleep")
    with ret_mock, sleep_mock:
        outcome = await m._process_org(_row(), dry_run=False)
    assert outcome["outcome"] == "failed"
    assert "retries exhausted" in outcome["error"]


# ---------------------------------------------------------------------------
# 6. --limit caps the SQL fetch + exit code reflects failure presence
# ---------------------------------------------------------------------------


def _make_pool(rows):
    """Mock asyncpg.create_pool returning a pool whose .fetch(...) returns rows."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.close = AsyncMock()
    return pool


async def test_limit_arg_appears_in_sql():
    pool = _make_pool([])
    args = argparse.Namespace(dry_run=True, test_mode=True, limit=7)
    with patch.dict("os.environ", {"STRIPE_TEST_SECRET_KEY": "sk_test_dummy"}), \
         patch.object(m.asyncpg, "create_pool", AsyncMock(return_value=pool)):
        # Suppress stdout; we only care about the SQL emitted to pool.fetch.
        with redirect_stdout(io.StringIO()):
            rc = await m.main_async(args)
    assert rc == 0
    sql_arg = pool.fetch.call_args[0][0]
    assert "LIMIT 7" in sql_arg


async def test_exit_code_one_on_any_failure():
    # Build a pool with one row that will fail (Stripe raises on retrieve).
    pool = _make_pool([_row()])
    args = argparse.Namespace(dry_run=False, test_mode=True, limit=0)
    err = stripe.error.InvalidRequestError("nope", "id")
    with patch.dict("os.environ", {"STRIPE_TEST_SECRET_KEY": "sk_test_dummy"}), \
         patch.object(m.asyncpg, "create_pool", AsyncMock(return_value=pool)), \
         patch.object(m.stripe.Customer, "retrieve", side_effect=err):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await m.main_async(args)
    assert rc == 1, "exit code must be 1 when any row failed"
    # The last line of stdout is the summary; preceding line is the failed row.
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    summary = json.loads(lines[-1])
    assert summary["counts"]["failed"] == 1
    assert summary["total"] == 1


async def test_exit_code_zero_when_all_skipped():
    # Already-tagged customer -> skipped-already-tagged, exit 0.
    pool = _make_pool([_row()])
    args = argparse.Namespace(dry_run=False, test_mode=True, limit=0)
    existing = _stripe_customer({"organization_id": _ORG_UUID})
    with patch.dict("os.environ", {"STRIPE_TEST_SECRET_KEY": "sk_test_dummy"}), \
         patch.object(m.asyncpg, "create_pool", AsyncMock(return_value=pool)), \
         patch.object(m.stripe.Customer, "retrieve", return_value=existing), \
         patch.object(m.stripe.Customer, "modify") as mod:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await m.main_async(args)
    assert rc == 0
    assert mod.called is False, "idempotent re-run must not write to Stripe"
