"""Phase 12 Plan 12-01 — integration tests for the foundation migration.

Proves the personal-org backfill correctly created one personal org per
pre-existing user, moved their stripe_customer_id, and stamped
public.jobs.organization_id on every existing job row.

Run after `supabase db push` applies 20260605000001_organizations.sql.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

SUPABASE_DB_URL = os.environ.get("SUPABASE_INTEGRATION_DB_URL", "")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not SUPABASE_DB_URL,
        reason="Requires SUPABASE_INTEGRATION_DB_URL with migration applied",
    ),
]


async def _pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(SUPABASE_DB_URL, min_size=1, max_size=2)


async def test_every_user_has_exactly_one_personal_org_owner_membership():
    """Backfill must produce exactly one personal-org owner row per public.users row."""
    pool = await _pool()
    try:
        row = await pool.fetchrow(
            """SELECT
                 (SELECT count(*) FROM public.users) AS user_count,
                 (SELECT count(*) FROM public.organization_memberships m
                  JOIN public.organizations o ON o.id = m.organization_id
                  WHERE o.is_personal = TRUE AND m.role = 'owner') AS personal_owner_count
            """
        )
        assert row["user_count"] == row["personal_owner_count"], (
            f"Mismatch: {row['user_count']} users vs "
            f"{row['personal_owner_count']} personal-owner memberships"
        )
    finally:
        await pool.close()


async def test_legacy_jobs_visible_via_organization_id():
    """Every pre-existing job row has organization_id populated by the backfill."""
    pool = await _pool()
    try:
        unstamped = await pool.fetchval(
            "SELECT count(*) FROM public.jobs WHERE organization_id IS NULL"
        )
        assert unstamped == 0, f"{unstamped} jobs still have organization_id IS NULL"
    finally:
        await pool.close()


async def test_stripe_customer_id_moved_to_personal_org():
    """Users with a pre-existing stripe_customer_id see it on their personal org."""
    pool = await _pool()
    try:
        rows = await pool.fetch(
            """SELECT u.id AS user_id, u.stripe_customer_id AS user_cust,
                      o.stripe_customer_id AS org_cust
               FROM public.users u
               JOIN public.organization_memberships m
                 ON m.user_id = u.id AND m.role = 'owner'
               JOIN public.organizations o
                 ON o.id = m.organization_id AND o.is_personal = TRUE
               WHERE u.stripe_customer_id IS NOT NULL"""
        )
        for r in rows:
            assert r["user_cust"] == r["org_cust"], (
                f"user {r['user_id']} stripe_customer_id mismatch: "
                f"user.users.stripe_customer_id={r['user_cust']}, "
                f"personal_org.stripe_customer_id={r['org_cust']}"
            )
    finally:
        await pool.close()
