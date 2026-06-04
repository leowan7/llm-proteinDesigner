"""Phase 12 Plan 12-01 — jobs_org_members RLS isolation.

Drives the RLS predicate is_member_of(organization_id) by setting
request.jwt.claims on the connection. Proves a user cannot see another
org's jobs via direct SQL once RLS is enforced.

Requires SUPABASE_INTEGRATION_DB_URL pointing at a database where the
service_role can SET LOCAL ROLE authenticated; SET LOCAL request.jwt.claims.
"""

from __future__ import annotations

import json
import os
import uuid

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
    return await asyncpg.create_pool(SUPABASE_DB_URL, min_size=1, max_size=4)


async def _act_as(conn: asyncpg.Connection, user_id: uuid.UUID) -> None:
    """Configure this connection so RLS sees auth.uid() == user_id.

    Uses set_config (the asyncpg-safe form) so RLS evaluates the
    jobs_org_members policy with the correct sub claim.
    """
    claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
    # request.jwt.claims is the GUC Supabase's auth.uid() reads from.
    await conn.execute(f"SELECT set_config('request.jwt.claims', $1, true)", claims)
    await conn.execute("SET LOCAL ROLE authenticated")


async def test_user_in_org_a_cannot_see_org_b_jobs():
    pool = await _pool()
    try:
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()
        job_in_b = uuid.uuid4()

        await pool.execute(
            "INSERT INTO public.users (id, email) VALUES "
            "($1, 'rls-a@example.com'), ($2, 'rls-b@example.com')",
            user_a, user_b,
        )
        await pool.execute(
            "INSERT INTO public.organizations (id, name) VALUES "
            "($1, 'rls-a'), ($2, 'rls-b')",
            org_a, org_b,
        )
        await pool.execute(
            "INSERT INTO public.organization_memberships (organization_id, user_id, role) "
            "VALUES ($1, $2, 'owner'), ($3, $4, 'owner')",
            org_a, user_a, org_b, user_b,
        )
        await pool.execute(
            "INSERT INTO public.jobs (id, user_id, organization_id, status) "
            "VALUES ($1, $2, $3, 'queued')",
            job_in_b, user_b, org_b,
        )
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await _act_as(conn, user_a)
                    rows = await conn.fetch(
                        "SELECT id FROM public.jobs WHERE id = $1", job_in_b
                    )
                    assert rows == [], (
                        f"RLS leak: user_a saw user_b's job {job_in_b} "
                        f"(jobs_org_members policy not enforced)"
                    )
        finally:
            await pool.execute("DELETE FROM public.jobs WHERE id = $1", job_in_b)
            await pool.execute(
                "DELETE FROM public.organizations WHERE id = ANY($1::uuid[])",
                [org_a, org_b],
            )
            await pool.execute(
                "DELETE FROM public.users WHERE id = ANY($1::uuid[])",
                [user_a, user_b],
            )
    finally:
        await pool.close()


async def test_user_in_org_b_can_see_org_b_jobs():
    pool = await _pool()
    try:
        user_b = uuid.uuid4()
        org_b = uuid.uuid4()
        job_id = uuid.uuid4()

        await pool.execute(
            "INSERT INTO public.users (id, email) VALUES ($1, 'rls-bb@example.com')",
            user_b,
        )
        await pool.execute(
            "INSERT INTO public.organizations (id, name) VALUES ($1, 'rls-bb')",
            org_b,
        )
        await pool.execute(
            "INSERT INTO public.organization_memberships (organization_id, user_id, role) "
            "VALUES ($1, $2, 'owner')",
            org_b, user_b,
        )
        await pool.execute(
            "INSERT INTO public.jobs (id, user_id, organization_id, status) "
            "VALUES ($1, $2, $3, 'queued')",
            job_id, user_b, org_b,
        )
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await _act_as(conn, user_b)
                    rows = await conn.fetch(
                        "SELECT id FROM public.jobs WHERE id = $1", job_id
                    )
                    assert len(rows) == 1, (
                        f"Member of org_b should see job in org_b (got {len(rows)} rows)"
                    )
        finally:
            await pool.execute("DELETE FROM public.jobs WHERE id = $1", job_id)
            await pool.execute("DELETE FROM public.organizations WHERE id = $1", org_b)
            await pool.execute("DELETE FROM public.users WHERE id = $1", user_b)
    finally:
        await pool.close()
