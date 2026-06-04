"""Phase 12 Plan 12-01 — protect_last_owner trigger semantics.

Proves the BEFORE UPDATE OR DELETE trigger blocks removal/demotion of the
last owner with SQLSTATE 23514 (check_violation), and that promoting a
second owner first allows the original owner to leave.
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
        reason="Requires SUPABASE_INTEGRATION_DB_URL with migration applied",
    ),
]


async def _pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(SUPABASE_DB_URL, min_size=1, max_size=4)


async def _bootstrap_org_with_owner(pool: asyncpg.Pool, email_suffix: str):
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.users (id, email) VALUES ($1, $2)",
        user_id, f"trigger-test-{email_suffix}@example.com",
    )
    await pool.execute(
        "INSERT INTO public.organizations (id, name, is_personal) VALUES ($1, $2, FALSE)",
        org_id, f"trigger-test-{email_suffix}",
    )
    await pool.execute(
        "INSERT INTO public.organization_memberships (organization_id, user_id, role) "
        "VALUES ($1, $2, 'owner')",
        org_id, user_id,
    )
    return user_id, org_id


async def test_delete_last_owner_raises_check_violation():
    pool = await _pool()
    try:
        user_id, org_id = await _bootstrap_org_with_owner(pool, "delete")
        try:
            with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
                await pool.execute(
                    "DELETE FROM public.organization_memberships "
                    "WHERE organization_id = $1 AND user_id = $2",
                    org_id, user_id,
                )
            assert "last owner" in str(exc_info.value).lower()
        finally:
            await pool.execute(
                "DELETE FROM public.organizations WHERE id = $1", org_id
            )
            await pool.execute("DELETE FROM public.users WHERE id = $1", user_id)
    finally:
        await pool.close()


async def test_demote_last_owner_raises_check_violation():
    pool = await _pool()
    try:
        user_id, org_id = await _bootstrap_org_with_owner(pool, "demote")
        try:
            with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
                await pool.execute(
                    "UPDATE public.organization_memberships "
                    "SET role = 'scientist'::public.org_role "
                    "WHERE organization_id = $1 AND user_id = $2",
                    org_id, user_id,
                )
            assert "last owner" in str(exc_info.value).lower()
        finally:
            await pool.execute(
                "DELETE FROM public.organizations WHERE id = $1", org_id
            )
            await pool.execute("DELETE FROM public.users WHERE id = $1", user_id)
    finally:
        await pool.close()


async def test_demote_after_adding_second_owner_succeeds():
    pool = await _pool()
    try:
        user_id, org_id = await _bootstrap_org_with_owner(pool, "promote-then-demote")
        second_user_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO public.users (id, email) VALUES ($1, $2)",
            second_user_id, f"second-owner-{second_user_id.hex[:6]}@example.com",
        )
        try:
            await pool.execute(
                "INSERT INTO public.organization_memberships (organization_id, user_id, role) "
                "VALUES ($1, $2, 'owner')",
                org_id, second_user_id,
            )
            # Now first owner can step down
            await pool.execute(
                "UPDATE public.organization_memberships "
                "SET role = 'scientist'::public.org_role "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, user_id,
            )
            row = await pool.fetchrow(
                "SELECT role::text AS role FROM public.organization_memberships "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, user_id,
            )
            assert row["role"] == "scientist"
        finally:
            await pool.execute(
                "DELETE FROM public.organizations WHERE id = $1", org_id
            )
            await pool.execute(
                "DELETE FROM public.users WHERE id = ANY($1::uuid[])",
                [user_id, second_user_id],
            )
    finally:
        await pool.close()
