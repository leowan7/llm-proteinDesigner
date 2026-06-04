"""Shared fixtures for Phase 12 organizations tests.

Provides three async factories (org_factory, member_factory, invitation_factory)
that insert rows into the public.organizations / organization_memberships /
organization_invitations tables and clean them up after each test.

Requires SUPABASE_INTEGRATION_DB_URL in the environment to point at a local
Supabase instance with migration 20260605000001 already applied.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable

import asyncpg
import pytest
import pytest_asyncio


SUPABASE_DB_URL = os.environ.get("SUPABASE_INTEGRATION_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not SUPABASE_DB_URL,
    reason="Requires SUPABASE_INTEGRATION_DB_URL pointing at a local Supabase",
)


@pytest_asyncio.fixture
async def db_pool() -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(SUPABASE_DB_URL, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def org_factory(db_pool: asyncpg.Pool) -> AsyncIterator[Callable]:
    created: list[uuid.UUID] = []

    async def _create(name: str | None = None, is_personal: bool = False) -> dict:
        org_id = uuid.uuid4()
        row = await db_pool.fetchrow(
            """INSERT INTO public.organizations (id, name, is_personal)
               VALUES ($1, $2, $3)
               RETURNING id, name, is_personal""",
            org_id,
            name or f"Test Org {org_id.hex[:8]}",
            is_personal,
        )
        created.append(org_id)
        return dict(row)

    yield _create

    # Cleanup
    if created:
        await db_pool.execute(
            "DELETE FROM public.organizations WHERE id = ANY($1::uuid[])",
            created,
        )


@pytest_asyncio.fixture
async def member_factory(db_pool: asyncpg.Pool) -> AsyncIterator[Callable]:
    inserted: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def _create(org_id, user_id, role: str = "scientist") -> dict:
        row = await db_pool.fetchrow(
            """INSERT INTO public.organization_memberships
                   (organization_id, user_id, role)
               VALUES ($1, $2, $3::public.org_role)
               RETURNING organization_id, user_id, role::text AS role""",
            org_id, user_id, role,
        )
        inserted.append((org_id, user_id))
        return dict(row)

    yield _create

    if inserted:
        await db_pool.executemany(
            "DELETE FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            inserted,
        )


@pytest_asyncio.fixture
async def invitation_factory(db_pool: asyncpg.Pool) -> AsyncIterator[Callable]:
    created: list[uuid.UUID] = []

    async def _create(org_id, email: str, role: str, invited_by) -> dict:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        row = await db_pool.fetchrow(
            """INSERT INTO public.organization_invitations
                   (organization_id, email, role, token, invited_by, expires_at)
               VALUES ($1, $2, $3::public.org_role, $4, $5, $6)
               RETURNING id, organization_id, email, role::text AS role, token,
                         invited_by, expires_at""",
            org_id, email, role, token, invited_by, expires_at,
        )
        created.append(row["id"])
        return dict(row)

    yield _create

    if created:
        await db_pool.execute(
            "DELETE FROM public.organization_invitations WHERE id = ANY($1::uuid[])",
            created,
        )
