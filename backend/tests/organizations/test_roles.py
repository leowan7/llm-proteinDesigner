"""Role ENUM round-trip + rejection tests.

Requires a real Supabase Postgres with migration 20260605000001 applied so
the public.org_role ENUM exists. Skipped automatically when
SUPABASE_INTEGRATION_DB_URL is not set (matches the conftest gate).
"""

from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

SUPABASE_DB_URL = os.environ.get("SUPABASE_INTEGRATION_DB_URL", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not SUPABASE_DB_URL,
        reason="Requires SUPABASE_INTEGRATION_DB_URL pointing at a local Supabase",
    ),
]


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(SUPABASE_DB_URL)
    try:
        yield c
    finally:
        await c.close()


async def test_role_enum_round_trip(conn, org_factory):
    """Cast each of owner / scientist / viewer to public.org_role and read it back.

    Not an INSERT: organization_memberships.user_id is an FK to a user row this
    test does not seed, so the ENUM itself is what is exercised.
    """
    await org_factory()

    for role in ("owner", "scientist", "viewer"):
        row = await conn.fetchrow(
            "SELECT $1::public.org_role AS role",
            role,
        )
        assert str(row["role"]) == role


async def test_invalid_role_rejected(conn):
    """Casting an unknown value to public.org_role raises InvalidTextRepresentationError."""
    with pytest.raises(asyncpg.exceptions.InvalidTextRepresentationError):
        await conn.fetchrow("SELECT $1::public.org_role AS role", "admin")
