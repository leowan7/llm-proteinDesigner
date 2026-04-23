"""Supabase admin client wrapper using the service role key.

Separated from ``backend/auth/router.py``'s ``_get_supabase()`` because that
helper uses the anon key (appropriate for end-user sign-ups and logins);
administrative user lifecycle operations — ``delete_user``, ``ban_user``,
metadata updates — require the service-role key.

Only called by the GDPR hard-delete executor in ``backend/user/deletion.py``
from within the daily ``worker/deletion_cron.py`` cron. No request path
should ever reach this module directly.
"""

from supabase import create_client, Client

from config import settings


def get_admin_supabase() -> Client:
    """Return a Supabase client authenticated with the service-role key.

    Keep short-lived: create per-call so credentials are not retained in a
    module-level singleton that survives process-wide exception handling.
    """
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def delete_auth_user(user_id: str) -> None:
    """Permanently delete the Supabase ``auth.users`` row for ``user_id``.

    Cascades to ``public.users`` (and its FK children — sessions, jobs,
    session_messages, billing rows) via the ``ON DELETE CASCADE`` on the
    public.users.id reference in migration ``20260318000000_init.sql``.

    **audit_log behaviour (CR-01, migration 20260424000004_audit_log_fk.sql):**
    ``audit_log`` rows are NOT removed by this cascade. The FK
    ``audit_log.admin_user_id → public.users(id)`` is declared
    ``ON DELETE SET NULL``, so the deleting user's audit rows survive as
    orphans with ``admin_user_id = NULL``. This is deliberate — the audit
    trail is non-repudiation data (who requested what, when) and must outlive
    the user's right-to-erasure. The row's original ``metadata`` JSONB still
    carries the IP and user-agent captured at request time.

    Before migration 20260424000004 was applied, ``admin_user_id`` was
    ``NOT NULL`` with no cascade policy, causing this cascade to abort with a
    foreign-key violation. The post-migration behaviour documented above is
    the current production state.

    Args:
        user_id: Supabase auth user UUID (same as public.users.id).
    """
    client = get_admin_supabase()
    client.auth.admin.delete_user(user_id)
