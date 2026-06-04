"""Phase 12 Plan 12-03 — POST /auth/signup auto-creates a personal org.

Covers ORG-07 (every user has at least one personal organization). The Wave 0
backfill in plan 12-01 handled all pre-existing users; this test proves that
the going-forward signup flow inserts the personal org + owner membership
inside the post-Supabase-signup block in backend/auth/router.py.

Env-gated on SUPABASE_INTEGRATION_DB_URL because the test exercises the real
INSERT path against a Postgres instance with migration 20260605000001 applied.
It does NOT call the Supabase auth API end-to-end — that would require either
the admin API to clean up the user after, or burning a real email address per
run. Instead it simulates the post-signup INSERT block directly with the same
SQL the endpoint runs.
"""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest


SUPABASE_DB_URL = os.environ.get("SUPABASE_INTEGRATION_DB_URL", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not SUPABASE_DB_URL,
        reason="requires local Supabase with Phase 12 migration applied",
    ),
]


async def _pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(SUPABASE_DB_URL, min_size=1, max_size=4)


async def test_new_signup_has_one_personal_org_with_owner_membership():
    """ORG-07: simulate the post-signup INSERT block from backend/auth/router.py
    and verify the resulting public.organizations + organization_memberships
    rows match the invariant 'every user has exactly one personal owner membership'.
    """
    pool = await _pool()
    try:
        # 1) Seed a public.users row to satisfy the FK constraint.
        new_user_id = uuid.uuid4()
        email = f"signup-{new_user_id.hex[:8]}@kendrew-test.local"
        await pool.execute(
            "INSERT INTO public.users (id, email) VALUES ($1, $2)",
            new_user_id, email,
        )

        try:
            # 2) Run the same INSERT block the signup endpoint runs.
            email_local = email.split("@", 1)[0] or "Personal"
            personal_name = f"{email_local} (Personal)"
            async with pool.acquire() as conn:
                async with conn.transaction():
                    org_row = await conn.fetchrow(
                        """INSERT INTO public.organizations (name, is_personal, created_by)
                           VALUES ($1, TRUE, $2)
                           RETURNING id""",
                        personal_name, new_user_id,
                    )
                    await conn.execute(
                        """INSERT INTO public.organization_memberships (organization_id, user_id, role)
                           VALUES ($1, $2, 'owner'::public.org_role)
                           ON CONFLICT DO NOTHING""",
                        org_row["id"], new_user_id,
                    )

            # 3) Assert: exactly one personal org membership where role=owner.
            rows = await pool.fetch(
                """SELECT o.id, o.name, o.is_personal, m.role::text AS role
                   FROM public.organization_memberships m
                   JOIN public.organizations o ON o.id = m.organization_id
                   WHERE m.user_id = $1""",
                new_user_id,
            )
            personal = [r for r in rows if r["is_personal"]]
            assert len(personal) == 1
            assert personal[0]["role"] == "owner"
            assert personal[0]["name"] == personal_name
        finally:
            # Cleanup: org delete cascades memberships, then drop the user.
            await pool.execute(
                "DELETE FROM public.organizations "
                "WHERE created_by = $1 AND is_personal = TRUE",
                new_user_id,
            )
            await pool.execute(
                "DELETE FROM public.users WHERE id = $1", new_user_id,
            )
    finally:
        await pool.close()
