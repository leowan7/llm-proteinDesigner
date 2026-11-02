"""Spend and revenue reads count a failed or cancelled job once it is metered.

Every read here used to filter ``status = 'complete'``, so a failed run that
saved designs, or a cancelled run, was charged in Stripe and shown as $0.
gpu_cost_usd cannot replace the filter: the webhook and the reaper write it on
unbilled terminal jobs too. jobs.metered (migration 20261013000001) is what
separates them, set by billing.stripe_client.mark_metered.

The read tests seed the same six jobs for one fresh user and check
admin/router.py and user/router.py against the real schema, one test per read
path. Costs are powers of two, so a sum names exactly which rows it counted.
The last three tests check mark_metered and unmark_metered themselves.

Skipped, before any write, on a database without migration 20261013000001;
see test_flag_off_rolling_window.py for the database URL convention.
"""

from __future__ import annotations

import datetime
import os
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio
import stripe
from admin import router as admin_router
from billing.stripe_client import mark_metered, unmark_metered
from user import router as user_router

DB_URL = os.environ.get(
    "SUPABASE_INTEGRATION_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)

# (status, metered, gpu_cost_usd, counted)
JOBS = [
    ("complete", False, 1, True),     # complete counts with or without the flag
    ("failed", True, 2, True),
    ("failed", False, 4, False),      # cost written, never metered
    ("cancelled", True, 8, True),
    ("cancelled", False, 16, False),
    ("expired", True, 32, False),     # retention_cron's relabel; never counted
]
COUNTED_SPEND = sum(cost for _, _, cost, counted in JOBS if counted)
COUNTED_JOBS = sum(1 for *_, counted in JOBS if counted)


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    try:
        if not await p.fetchval(
            """SELECT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'jobs'
                  AND column_name = 'metered')"""
        ):
            pytest.skip("database lacks migration 20261013000001")
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture
async def account(pool: asyncpg.Pool):
    """A user, their personal org, and a tool name no other row uses."""
    user_id = uuid.uuid4()
    email = f"metered-{user_id.hex[:8]}@bindwave-test.local"
    try:
        await pool.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, email,
        )
        await pool.execute(
            "INSERT INTO public.users (id, email) VALUES ($1, $2)", user_id, email,
        )
        org_id = await pool.fetchval(
            "SELECT public.personal_org_for($1)", user_id,
        )
        yield {
            "user_id": user_id,
            "email": email,
            "org_id": org_id,
            "tool": f"metered-test-{user_id.hex[:8]}",
        }
    finally:
        # organizations.created_by is ON DELETE SET NULL
        # (20260605000001_organizations.sql:32), so the org goes first. Jobs
        # cascade from the org (same file, :213) and the user
        # (20260318000000_init.sql:4 and :12).
        await pool.execute(
            "DELETE FROM public.organizations WHERE created_by = $1", user_id,
        )
        await pool.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _seed(pool: asyncpg.Pool, account: dict) -> set[str]:
    """Insert JOBS for the account; return the ids that should be counted."""
    counted: set[str] = set()
    for status, metered, cost, should_count in JOBS:
        job_id = await pool.fetchval(
            """INSERT INTO public.jobs
                   (user_id, organization_id, created_by_user_id, tool, status,
                    gpu_seconds, gpu_cost_usd, metered, completed_at)
               VALUES ($1, $2, $1, $3, $4, 60, $5, $6, now())
               RETURNING id""",
            account["user_id"], account["org_id"], account["tool"],
            status, cost, metered,
        )
        if should_count:
            counted.add(str(job_id))
    return counted


def _patched(pool: asyncpg.Pool):
    return (
        patch("admin.router.get_db_pool", AsyncMock(return_value=pool)),
        patch("user.router.get_db_pool", AsyncMock(return_value=pool)),
        patch("admin.router.write_audit", AsyncMock()),
    )


# ---------------------------------------------------------------------------
# admin/router.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_cursor", [False, True])
async def test_list_users_total_spend(pool, account, with_cursor):
    """Both list_users queries (keyset cursor and none)."""
    await _seed(pool, account)
    before = (
        (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)).isoformat()
        if with_cursor else None
    )
    p1, p2, p3 = _patched(pool)
    with p1, p2, p3:
        result = await admin_router.list_users(
            email=account["email"], sort="created_at_desc", before=before,
            limit=50, admin_id="admin",
        )

    [user] = [u for u in result["users"] if u["id"] == str(account["user_id"])]
    assert user["total_spend"] == COUNTED_SPEND


async def test_revenue_total(pool, account):
    """get_revenue's summary query sums the whole table, so compare a delta."""
    p1, p2, p3 = _patched(pool)
    with p1, p2, p3:
        before = await admin_router.get_revenue(period="all_time", admin_id="admin")
        await _seed(pool, account)
        after = await admin_router.get_revenue(period="all_time", admin_id="admin")

    assert after["total_revenue"] - before["total_revenue"] == pytest.approx(COUNTED_SPEND)
    # completed_jobs still means completed.
    assert after["completed_jobs"] - before["completed_jobs"] == 1


async def test_revenue_by_tool(pool, account):
    await _seed(pool, account)
    p1, p2, p3 = _patched(pool)
    with p1, p2, p3:
        result = await admin_router.get_revenue(period="all_time", admin_id="admin")

    [row] = [r for r in result["by_tool"] if r["tool"] == account["tool"]]
    assert row["revenue"] == COUNTED_SPEND
    assert row["job_count"] == COUNTED_JOBS


# ---------------------------------------------------------------------------
# user/router.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "scientist"])
async def test_usage_summary(pool, account, role):
    await _seed(pool, account)
    p1, p2, p3 = _patched(pool)
    with p1, p2, p3:
        result = await user_router.get_usage(
            user_id=str(account["user_id"]), active=(str(account["org_id"]), role),
        )

    assert result["total_spend_usd"] == COUNTED_SPEND
    assert result["job_count"] == COUNTED_JOBS


@pytest.mark.parametrize("role", ["owner", "scientist"])
async def test_usage_recent_charges(pool, account, role):
    counted = await _seed(pool, account)
    p1, p2, p3 = _patched(pool)
    with p1, p2, p3:
        result = await user_router.get_usage(
            user_id=str(account["user_id"]), active=(str(account["org_id"]), role),
        )

    assert {c["id"] for c in result["recent_charges"]} == counted


# ---------------------------------------------------------------------------
# billing/stripe_client.py::mark_metered and unmark_metered, the writes the
# reads depend on
# ---------------------------------------------------------------------------


async def _failed_job(pool: asyncpg.Pool, account: dict) -> uuid.UUID:
    return await pool.fetchval(
        """INSERT INTO public.jobs
               (user_id, organization_id, created_by_user_id, tool, status)
           VALUES ($1, $2, $1, $3, 'failed') RETURNING id""",
        account["user_id"], account["org_id"], account["tool"],
    )


async def test_mark_metered_flags_the_job_and_returns_its_customer(pool, account):
    await pool.execute(
        "UPDATE public.organizations SET stripe_customer_id = 'cus_metered' "
        "WHERE id = $1", account["org_id"],
    )
    job_id = await _failed_job(pool, account)

    async with pool.acquire() as conn:
        customer = await mark_metered(conn, str(job_id))

    assert customer == "cus_metered"
    assert await pool.fetchval(
        "SELECT metered FROM public.jobs WHERE id = $1", job_id,
    ) is True


async def test_mark_metered_leaves_a_job_with_no_customer_unflagged(pool, account):
    job_id = await _failed_job(pool, account)

    async with pool.acquire() as conn:
        customer = await mark_metered(conn, str(job_id))

    assert customer is None
    assert await pool.fetchval(
        "SELECT metered FROM public.jobs WHERE id = $1", job_id,
    ) is False


async def test_unmark_metered_clears_the_flag(pool, account):
    await pool.execute(
        "UPDATE public.organizations SET stripe_customer_id = 'cus_metered' "
        "WHERE id = $1", account["org_id"],
    )
    job_id = await _failed_job(pool, account)

    async with pool.acquire() as conn:
        assert await mark_metered(conn, str(job_id)) == "cus_metered"
        await unmark_metered(
            conn, str(job_id), stripe.InvalidRequestError("No such customer", None),
        )

    assert await pool.fetchval(
        "SELECT metered FROM public.jobs WHERE id = $1", job_id,
    ) is False
