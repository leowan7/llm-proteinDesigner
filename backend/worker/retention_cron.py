"""Retention cron — daily sweep at 04:45 UTC (Plan 10-05).

Registered in :mod:`worker.main` at ``hour=4, minute=45`` — offset from
``refresh_live_stats`` (04:30 UTC) and ``process_pending_deletions`` (03:15 UTC)
to avoid same-minute contention.

Two passes run sequentially:

1. **Warning pass** (:func:`send_retention_warnings`): emails owners 7 days
   before their per-user retention deadline. Stamps ``retention_warning_sent_at``
   only after the email call returns successfully — T-10.05-06 guarantees that
   a failed send does NOT mark the row as warned, so the next cron run retries.

2. **Deletion pass** (:func:`execute_retention_deletions`): hard-deletes the
   R2 object prefix ``users/{user_id}/jobs/{job_id}/`` and stamps
   ``retention_deleted_at``. Terminal statuses (``complete``/``failed``/
   ``cancelled``) flip to ``expired`` so users see the expiry reason in job
   history; non-terminal non-running statuses are preserved untouched.

Policy guards (all enforced in SQL, not in Python):

- **Pre-policy exemption (T-10.05-03):** every SELECT filters
  ``j.created_at > policy_effective_from``. The singleton row in
  ``public.retention_policy`` is seeded at migration time, so jobs created
  before Plan 10-05 landed are never candidates for automatic deletion.
- **Running-job safety (T-10.05-05):** the deletion SELECT filters
  ``j.status != 'running'`` — active GPU work is never interrupted.
- **Per-user retention (T-10.05-01):** the deadline computation joins each
  job row against its owner's ``users.data_retention_days`` (default 90,
  min 30, max 365) — global defaults are not inlined.

``WARNING_DAYS_BEFORE`` flows through a proper parameter binding using the
``($N || ' days')::interval`` pattern (WR-06 10-REVIEW). This is uniform with
the per-user retention window pattern elsewhere in the module and eliminates
the f-string SQL that previously interpolated the Python integer directly.

**UUID/str convention (W2):** row IDs are coerced to ``str`` at the top of each
per-row loop and the stringified form is used for all downstream operations
(SQL bindings, log lines, email payloads, storage client calls). This matches
``deletion_cron.py``. asyncpg accepts ``str`` for ``uuid`` columns so no
PostgreSQL-side type coercion is required.
"""
import datetime
import logging

import sentry_sdk
from db.connection import get_db_pool
from jobs.notifications import send_retention_warning_email
from storage.client import delete_job_objects

logger = logging.getLogger(__name__)

# 7-day pre-deadline warning window. Module-level int — safely interpolated
# into the INTERVAL literal in the warning SELECT (W8).
WARNING_DAYS_BEFORE = 7

# Status values considered "done" — a retention-expired job in one of these
# states is flipped to 'expired' so the UI can show why it was purged.
# Non-running, non-terminal statuses (queued/pending/draft) are preserved.
TERMINAL_STATUSES = ("complete", "failed", "cancelled")


async def _fetch_policy_effective_from(pool) -> datetime.datetime:
    """Return ``public.retention_policy.policy_effective_from`` for the singleton row.

    Raises RuntimeError if the row is missing (indicates migration
    ``20260424000003_retention_tracking.sql`` was not applied).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT policy_effective_from FROM public.retention_policy WHERE id = 1"
        )
    if row is None:
        raise RuntimeError(
            "retention_policy.id=1 missing — migration "
            "20260424000003_retention_tracking.sql not applied"
        )
    return row["policy_effective_from"]


async def send_retention_warnings(ctx: dict | None = None) -> int:
    """Pass 1: email owners 7 days before their retention deadline.

    Selection criteria (all server-side):

    - ``retention_warning_sent_at IS NULL`` — idempotency; one email per job.
    - ``retention_deleted_at IS NULL`` — already-deleted jobs are skipped.
    - ``j.created_at > policy_effective_from`` — pre-policy exemption (T-10.05-03).
    - deadline is within the next ``WARNING_DAYS_BEFORE`` days AND still in the
      future — so a job already past its deadline is handled by the deletion
      pass, not warned about again.

    Per-row ordering (T-10.05-06): call ``send_retention_warning_email`` FIRST,
    then stamp ``retention_warning_sent_at`` only on success. If the email call
    raises (e.g. Resend outage), the row remains unstamped and the next cron
    run retries it.

    Args:
        ctx: arq cron context (unused — we acquire the pool via
            :func:`get_db_pool` for consistency with the rest of the worker).

    Returns:
        Count of successfully sent warning emails.
    """
    pool = await get_db_pool()
    effective_from = await _fetch_policy_effective_from(pool)

    # WR-06 (10-REVIEW): use the parameterized interval pattern
    # ``($N || ' days')::interval`` to carry the warning window through a
    # proper SQL parameter binding rather than Python f-string interpolation.
    # Uniform with the ``(u.data_retention_days || ' days')::interval`` pattern
    # already used for the per-user retention column below. WARNING_DAYS_BEFORE
    # is still a module-level int today, but the pattern now holds even if a
    # future refactor routes a user-supplied value through the same path.
    query = """
        SELECT j.id, j.user_id, j.name, j.created_at,
               u.email, u.data_retention_days
        FROM public.jobs j
        JOIN public.users u ON u.id = j.user_id
        WHERE j.retention_warning_sent_at IS NULL
          AND j.retention_deleted_at IS NULL
          AND j.created_at > $1
          AND j.created_at + (u.data_retention_days || ' days')::interval
              < NOW() + ($2 || ' days')::interval
          AND j.created_at + (u.data_retention_days || ' days')::interval > NOW()
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, effective_from, str(WARNING_DAYS_BEFORE))

    sent = 0
    for row in rows:
        # W2 (10-REVIEW): coerce the UUID to str at the top of the loop and use
        # the stringified form for ALL downstream operations — SQL bindings, email
        # payloads, log lines. asyncpg accepts str for uuid columns. Convention:
        # IDs are handled as str throughout cron paths (matches deletion_cron.py).
        job_id = str(row["id"])
        deletion_date = row["created_at"] + datetime.timedelta(
            days=row["data_retention_days"]
        )
        try:
            # Step 1: send the email. If this raises, step 2 is skipped —
            # the row stays unstamped and the next cron retries it (T-10.05-06).
            await send_retention_warning_email(
                to_email=row["email"],
                job_id=job_id,
                job_name=row["name"],
                deletion_date_iso=deletion_date.date().isoformat(),
                retention_days=row["data_retention_days"],
            )
            # Step 2: stamp the row only after the email succeeded.
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE public.jobs "
                    "SET retention_warning_sent_at = now(), updated_at = now() "
                    "WHERE id = $1",
                    job_id,
                )
            sent += 1
        except Exception as exc:
            logger.exception(
                "send_retention_warnings: job=%s email failed",
                job_id,
            )
            sentry_sdk.capture_exception(exc)
            # Swallow — the next cron run retries this row.

    logger.info("send_retention_warnings: %d warning(s) sent", sent)
    return sent


async def execute_retention_deletions(ctx: dict | None = None) -> int:
    """Pass 2: hard-delete S3 objects and stamp ``retention_deleted_at``.

    Selection criteria (all server-side):

    - ``retention_deleted_at IS NULL`` — idempotency; never re-process a row.
    - ``j.status != 'running'`` — safety guard (T-10.05-05); active GPU work
      is never deleted.
    - ``j.created_at > policy_effective_from`` — pre-policy exemption
      (T-10.05-03).
    - ``j.created_at + (data_retention_days days)::interval <= NOW()`` —
      the job is past its per-user deadline (T-10.05-01).

    Per-row behaviour:

    - :func:`delete_job_objects` purges the ``users/{user_id}/jobs/{job_id}/``
      R2 prefix (idempotent; empty prefix returns 0).
    - If the prior status was terminal (complete/failed/cancelled) the row
      flips to ``expired``. Otherwise the status is preserved (queued / pending
      / draft stay as-is — the row is still in a non-running limbo and the
      UI already treats it as "never ran").
    - ``retention_deleted_at = now()`` is stamped regardless of status so the
      row is not re-processed next cycle.

    One failing row does not block the rest of the batch — we log and swallow
    per-row exceptions.

    Args:
        ctx: arq cron context (unused).

    Returns:
        Count of rows successfully purged.
    """
    pool = await get_db_pool()
    effective_from = await _fetch_policy_effective_from(pool)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT j.id, j.user_id, j.status, j.created_at,
                      u.data_retention_days
               FROM public.jobs j
               JOIN public.users u ON u.id = j.user_id
               WHERE j.retention_deleted_at IS NULL
                 AND j.status != 'running'
                 AND j.created_at > $1
                 AND j.created_at + (u.data_retention_days || ' days')::interval
                     <= NOW()""",
            effective_from,
        )

    deleted = 0
    for row in rows:
        job_id = str(row["id"])
        user_id = str(row["user_id"])
        try:
            # Purge R2 prefix (idempotent — empty prefix returns 0).
            count = delete_job_objects(user_id, job_id)
            new_status = (
                "expired" if row["status"] in TERMINAL_STATUSES else row["status"]
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE public.jobs
                       SET retention_deleted_at = now(),
                           status = $2,
                           updated_at = now()
                       WHERE id = $1""",
                    job_id,  # W2: str form, consistent with send_retention_warnings
                    new_status,
                )
            logger.info(
                "retention delete: job=%s objects=%d status=%s",
                job_id, count, new_status,
            )
            deleted += 1
        except Exception as exc:
            logger.exception(
                "execute_retention_deletions: job=%s failed",
                job_id,
            )
            sentry_sdk.capture_exception(exc)
            # Swallow — row stays unstamped; next cron retries.

    logger.info("execute_retention_deletions: %d job(s) expired", deleted)
    return deleted


async def retention_cron(ctx: dict | None = None) -> dict:
    """Entry point registered in :class:`worker.main.WorkerSettings.cron_jobs`.

    Runs both passes sequentially. The warning pass runs first so that jobs
    crossing the 7-day-pre-deadline boundary this cycle get a warning before
    they are eligible for deletion next cycle.

    Returns:
        Dict with ``warned`` and ``expired`` counts — visible in the arq log.
    """
    warned = await send_retention_warnings(ctx)
    expired = await execute_retention_deletions(ctx)
    return {"warned": warned, "expired": expired}
