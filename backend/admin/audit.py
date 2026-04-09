"""Audit log write helper for admin actions.

Every admin endpoint writes an audit log entry synchronously in the request
path — not as a background task — to guarantee no audit gap on failure (per
RESEARCH.md anti-pattern guidance, Pitfall 4).

Schema: audit_log(id, admin_user_id, action, target_id, metadata, created_at)
"""

import json

from db.connection import get_db_pool


async def write_audit(
    admin_user_id: str,
    action: str,
    target_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write a record to the audit_log table.

    Called after every admin action. Writes synchronously in the request handler
    to guarantee the audit entry is recorded even if the caller is interrupted
    before returning.

    Args:
        admin_user_id: UUID string of the admin performing the action.
        action: Action label (e.g. "view_users", "cancel_job", "view_revenue").
        target_id: Optional ID of the entity acted upon (job_id, user_id, etc.).
        metadata: Optional dict of additional context (filters, result counts, etc.).

    Raises:
        Any asyncpg exception if the DB insert fails (propagates to caller).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.audit_log (admin_user_id, action, target_id, metadata)
               VALUES ($1, $2, $3, $4::jsonb)""",
            admin_user_id,
            action,
            target_id,
            json.dumps(metadata or {}),
        )
