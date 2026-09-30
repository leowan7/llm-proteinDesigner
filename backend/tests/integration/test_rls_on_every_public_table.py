"""Fail if any ordinary table in schema public has row-level security off.

Reads pg_class on the database ``supabase start`` applied every migration to.
The URL defaults to that local stack, the same one .github/workflows/test.yml
uses. Override with SUPABASE_INTEGRATION_DB_URL.
"""

from __future__ import annotations

import os

import asyncpg

DB_URL = os.environ.get(
    "SUPABASE_INTEGRATION_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)


async def test_every_public_table_has_row_level_security_on():
    conn = await asyncpg.connect(DB_URL)
    try:
        tables = await conn.fetch(
            """SELECT c.relname, c.relrowsecurity
               FROM pg_class c
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind = 'r'
               ORDER BY c.relname"""
        )
    finally:
        await conn.close()

    assert tables, "no ordinary tables in schema public; is this the migrated database?"
    rls_off = [t["relname"] for t in tables if not t["relrowsecurity"]]
    assert not rls_off, f"row-level security is off on: {rls_off}"
