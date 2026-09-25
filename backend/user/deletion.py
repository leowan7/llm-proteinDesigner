"""Account deletion soft-delete handler + hard-delete executor.

Soft-delete: ``POST /user/delete-account`` sets ``deletion_requested_at = now()``.
Hard-delete: executed by ``backend/worker/deletion_cron.py`` once the 30-day
grace period has elapsed.

Hard-delete order is deliberately R2 -> Stripe -> Supabase auth:
  1. Clearing R2 objects first means the trailing Supabase cascade can only
     remove rows whose referenced objects are already gone. No hanging pointers.
  2. Stripe Customer delete runs second and is non-fatal — Stripe retains invoice
     records regardless, and an invoice-retention block on Customer.delete must
     NOT prevent the DB/auth row from being removed.
  3. ``auth.admin.delete_user`` is last so a failure there leaves a recoverable
     partial state (DB row present, R2 gone, Stripe detached) that the next cron
     run can finish. Organization hand-over runs in the guard transaction
     immediately before it, because the cascade destroys this user's membership
     rows and a shared org must not be left ownerless.
"""
import datetime  # noqa: F401  — exposed to callers that pass datetime-aware timestamps
import logging

import stripe  # stripe_client.py sets stripe.api_key at its module import
from auth.admin_client import delete_auth_user
from db.connection import get_db_pool
from jobs.notifications import send_deletion_completed_email
from storage.client import list_and_delete_user_objects

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 30


async def execute_hard_delete(
    user_id: str,
    user_email: str,
    stripe_customer_id: str | None,
) -> None:
    """Execute the permanent deletion flow for one user.

    Race guard (T-10.04-04): the FIRST step re-acquires the user row under
    ``SELECT ... FOR UPDATE`` inside a transaction and aborts immediately if
    ``deletion_requested_at`` is NULL. This covers the window where a user
    cancels their deletion between the cron's batch SELECT and the per-user
    executor — without this guard a cancel that races in could still be
    bulldozed.

    Order after the guard passes:
      1. Delete R2 objects under ``users/{user_id}/`` — no downstream refs once gone.
      2. Delete or anonymize Stripe customer — invoices retained per tax law
         (7 yr), PII detached. Failure logs-and-continues.
      3. Delete Supabase ``auth.users`` row — cascades ``public.users`` + all
         FK children.
      4. Send final deletion-completed email (best effort; user no longer in DB).

    Args:
        user_id: public.users.id UUID string.
        user_email: Captured at cron time for the final farewell email.
        stripe_customer_id: May be None for users who never initiated billing.
    """
    # 0. Race guard (T-10.04-04) — abort if the user cancelled deletion in the meantime.
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            guard_row = await conn.fetchrow(
                "SELECT deletion_requested_at FROM public.users WHERE id = $1 FOR UPDATE",
                user_id,
            )
            if guard_row is None or guard_row["deletion_requested_at"] is None:
                logger.warning(
                    "execute_hard_delete: aborting user=%s — deletion_requested_at is NULL "
                    "(user cancelled or row missing).",
                    user_id,
                )
                return

    # 1. R2 — must complete before the auth delete cascade so orphaned references
    #    never exist. If this raises, we bail out and the row stays pending for
    #    the next cron run to retry.
    try:
        count = list_and_delete_user_objects(user_id)
        logger.info("Hard-delete: user=%s deleted %d R2 objects", user_id, count)
    except Exception as exc:
        logger.error("Hard-delete R2 failure user=%s: %s", user_id, exc)
        raise  # partial delete is worse than no delete — abort.

    # 2. Stripe — detach PII. We delete the Customer per decision D-04 in
    #    10-CONTEXT.md; Stripe retains invoice records regardless. For accounts
    #    with no charges, Customer.delete succeeds cleanly. Failure is logged
    #    but does NOT block the auth delete (invoice-retention on Stripe's side
    #    must not hold the DB row hostage).
    if stripe_customer_id:
        try:
            stripe.Customer.delete(stripe_customer_id)
            logger.info("Hard-delete: Stripe customer deleted %s", stripe_customer_id)
        except Exception as exc:
            logger.warning(
                "Hard-delete Stripe customer %s failed (continuing): %s",
                stripe_customer_id,
                exc,
            )

    # 3. Supabase auth + cascade — removes auth.users, which cascades public.users
    #    and every FK child (sessions, jobs, session_messages, billing rows).
    #
    # WR-07: re-check deletion_requested_at one last time under FOR UPDATE just
    # before the irreversible Supabase admin call. The step-0 guard released
    # its row lock after the leading transaction committed; in the window
    # between that and here a user could have hit /user/cancel-deletion. R2
    # and Stripe are already purged at this point, but skipping the auth
    # delete leaves the DB/auth row intact for manual review — preferable to
    # completing an irreversible wipe on a user who cancelled.
    async with pool.acquire() as conn:
        async with conn.transaction():
            late_guard = await conn.fetchrow(
                "SELECT deletion_requested_at FROM public.users WHERE id = $1 FOR UPDATE",
                user_id,
            )
            if late_guard is None or late_guard["deletion_requested_at"] is None:
                logger.error(
                    "Hard-delete: user=%s cancelled mid-execute after R2/Stripe ran. "
                    "R2 and Stripe were already purged; leaving DB row for manual review.",
                    user_id,
                )
                return

            # WR-08: hand over organizations BEFORE the cascade, in the same
            # transaction as the guard above so a cancelled deletion changes
            # nothing. public.users is deleted by cascade below, which takes this
            # user's organization_memberships rows with it. For a SHARED org that
            # this user solely owns, that would leave zero owners -- every
            # owner-only route and the memberships_write_owners RLS policy would
            # have no satisfiable caller for the rest of that org's life, and no
            # API exists to repair it. protect_last_owner() only tolerates the
            # user-gone cascade for personal orgs
            # (20260605000003_personal_org_tolerance.sql:264-270), so an org
            # missed here fails the delete loudly rather than being stranded.
            promoted = await conn.fetch(
                """WITH sole AS (
                       SELECT m.organization_id
                         FROM public.organization_memberships m
                         JOIN public.organizations o ON o.id = m.organization_id
                        WHERE m.user_id = $1::uuid
                          AND m.role = 'owner'
                          AND NOT o.is_personal
                          AND NOT EXISTS (
                              SELECT 1 FROM public.organization_memberships rival
                               WHERE rival.organization_id = m.organization_id
                                 AND rival.user_id <> $1::uuid
                                 AND rival.role = 'owner')
                   ), heir AS (
                       SELECT DISTINCT ON (m.organization_id)
                              m.organization_id, m.user_id
                         FROM public.organization_memberships m
                         JOIN sole s ON s.organization_id = m.organization_id
                        WHERE m.user_id <> $1::uuid
                        ORDER BY m.organization_id, m.created_at, m.user_id
                   )
                   UPDATE public.organization_memberships m
                      SET role = 'owner'
                     FROM heir h
                    WHERE m.organization_id = h.organization_id
                      AND m.user_id = h.user_id
                RETURNING m.organization_id, m.user_id""",
                user_id,
            )
            for row in promoted:
                logger.info(
                    "Hard-delete: org %s had no other owner, promoted member %s",
                    row["organization_id"],
                    row["user_id"],
                )

            # Any org whose ONLY member is this user goes with them. That is the
            # personal org -- whose name embeds the email local part, so leaving
            # it behind would survive the GDPR erasure as PII -- plus any team
            # org the user never shared. Nothing is promoted here: the CTE above
            # found no heir, because there is no other member.
            emptied = await conn.fetch(
                """DELETE FROM public.organizations o
                    WHERE EXISTS (
                              SELECT 1 FROM public.organization_memberships m
                               WHERE m.organization_id = o.id
                                 AND m.user_id = $1::uuid)
                      AND NOT EXISTS (
                              SELECT 1 FROM public.organization_memberships rival
                               WHERE rival.organization_id = o.id
                                 AND rival.user_id <> $1::uuid)
                RETURNING o.id, o.is_personal""",
                user_id,
            )
            for row in emptied:
                logger.info(
                    "Hard-delete: deleted sole-member org %s (personal=%s)",
                    row["id"],
                    row["is_personal"],
                )
    delete_auth_user(user_id)
    logger.info("Hard-delete: Supabase auth user deleted %s", user_id)

    # 4. Final email — best effort. Row is already gone but we still hold the
    #    email captured at cron-fetch time.
    try:
        await send_deletion_completed_email(user_email)
    except Exception as exc:
        logger.warning("Hard-delete final email failed for %s: %s", user_email, exc)
