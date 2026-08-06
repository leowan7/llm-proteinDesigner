"""Database connection pool using asyncpg.

Phase 11 D-06: in production this pool connects via the Supavisor transaction
pooler (port 6543), not direct Postgres (port 5432). Transaction mode
multiplexes connections across transactions, so asyncpg's default prepared-
statement cache breaks with DuplicatePreparedStatementError. See RESEARCH
Pitfall 2 and github.com/supabase/supabase/issues/39227.

Mitigation: statement_cache_size=0 + max_inactive_connection_lifetime=0.
"""

import asyncpg
from config import settings

_pool: asyncpg.Pool | None = None


async def get_db_pool() -> asyncpg.Pool:
    """Get or create the asyncpg connection pool.

    Pool configuration is safe for Supavisor transaction mode:
    - statement_cache_size=0 disables asyncpg's per-connection prepared-statement
      cache (server-side connection reuse makes cached statement IDs invalid).
    - max_inactive_connection_lifetime=0 recycles idle connections immediately
      so the Supavisor-side session state never drifts from our expectations.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
            # Supavisor transaction mode compatibility — see module docstring.
            statement_cache_size=0,
            max_inactive_connection_lifetime=0,
        )
    return _pool


async def close_db_pool() -> None:
    """Close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
