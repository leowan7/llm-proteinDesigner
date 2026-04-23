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

    Cascades to ``public.users`` (and every FK child of it — sessions, jobs,
    session_messages, billing rows, audit_log entries whose admin_user_id
    matches) via the ``ON DELETE CASCADE`` defined on the public.users.id
    reference in migration ``20260318000000_init.sql``.

    Args:
        user_id: Supabase auth user UUID (same as public.users.id).
    """
    client = get_admin_supabase()
    client.auth.admin.delete_user(user_id)
