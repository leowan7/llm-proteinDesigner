"""Daily cron scanning for users past the 30-day grace period and executing hard-delete.

Registered in ``worker/main.py`` at 03:15 UTC — runs once per day, scans
``public.users`` for rows whose ``deletion_requested_at`` is older than
``GRACE_PERIOD_DAYS`` (30), and invokes :func:`user.deletion.execute_hard_delete`
per row.

The per-row executor owns its own race guard (SELECT ... FOR UPDATE re-check
of deletion_requested_at) — the cron's job is batch enumeration + isolation:
one bad row must not block the rest of the queue, so every per-row exception
is logged and swallowed. Rows that fail stay pending; the next cron run
retries them.
"""
import logging

from db.connection import get_db_pool
from user.deletion import GRACE_PERIOD_DAYS, execute_hard_delete

logger = logging.getLogger(__name__)


async def process_pending_deletions(ctx: dict | None = None) -> int:
    """Find users with deletion_requested_at older than GRACE_PERIOD_DAYS and hard-delete them.

    Args:
        ctx: arq cron context (unused here — we acquire the pool via ``get_db_pool``
            for consistency with the rest of the worker module).

    Returns:
        Number of users successfully hard-deleted in this run. Rows that raised
        during execution are counted as failed (not included in the return value)
        and remain pending for the next cron cycle.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id, email, stripe_customer_id
                FROM public.users
                WHERE deletion_requested_at IS NOT NULL
                  AND deletion_requested_at < NOW() - INTERVAL '{GRACE_PERIOD_DAYS} days'"""
        )
    if not rows:
        return 0

    deleted = 0
    for row in rows:
        user_id = str(row["id"])
        # asyncpg Record: use dict(row).get() for NULL-safe access on the
        # nullable stripe_customer_id column.
        stripe_customer_id = dict(row).get("stripe_customer_id")
        try:
            await execute_hard_delete(user_id, row["email"], stripe_customer_id)
            deleted += 1
        except Exception as exc:
            logger.error(
                "process_pending_deletions: user=%s failed: %s",
                user_id, exc,
            )
            # Swallow — one bad row must not block the rest. Row stays
            # pending; the next cron run will retry.
    logger.info("process_pending_deletions: %d user(s) hard-deleted", deleted)
    return deleted
