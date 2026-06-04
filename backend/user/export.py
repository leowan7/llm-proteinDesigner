"""Data export builder — invoked as a FastAPI BackgroundTask from POST /user/data-export.

GDPR Article 20 (data portability). Produces a ZIP of every row the platform holds
for a user (profile, sessions, session messages, jobs) plus a manifest listing the
S3 keys of any referenced objects. Uploads the ZIP to R2 under the user's prefix,
presigns a 24-hour GET URL, persists that URL + expiry on the user row, and emails
the link to the account owner.

Never runs in the request path — always scheduled by ``request_data_export`` so the
endpoint responds 202 immediately.
"""
import datetime
import io
import json
import logging
import uuid
import zipfile
from typing import Any

from config import settings
from db.connection import get_db_pool
from jobs.notifications import send_export_ready_email
from storage.client import generate_presigned_get_url, get_s3_client

logger = logging.getLogger(__name__)

EXPORT_URL_TTL_SECONDS = 3600  # 1 hour per CR-02


def _json_default(o: Any) -> Any:
    """JSON serializer for types asyncpg returns that ``json.dumps`` cannot handle.

    ``datetime``/``date`` -> ISO-8601 string. ``UUID`` and any object whose
    ``__str__`` is the canonical representation -> ``str(o)``. Falls through to
    ``str(o)`` for everything else so an unknown type never blocks the export.
    """
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    return str(o)


async def build_and_deliver_export(user_id: str, user_email: str) -> None:
    """Build the export ZIP, upload to R2, presign, email, persist metadata.

    Steps:
      1. Pull user profile (all Phase-10 columns per W9), sessions, session_messages, jobs.
      2. Serialize to JSON files in an in-memory ZIP with a manifest.json.
      3. Upload to ``users/{user_id}/exports/export-{ts}.zip``.
      4. Presign GET URL (1 hour) for the notification email only.
      5. ``UPDATE public.users`` with last_export_key + last_export_expires_at (CR-02: key only, not URL).
      6. Email the user the presigned URL.

    WR-08: the whole flow is wrapped in try/except. On any failure (DB,
    R2, email) we stamp ``last_export_expires_at = now() - 1s`` as a sentinel
    so ``GET /user/data-export`` can surface a ``failed`` status instead of
    leaving the UI on ``pending`` forever. The exception is re-raised so the
    FastAPI BackgroundTask framework logs the traceback.

    Args:
        user_id: Application user UUID (matches public.users.id).
        user_email: Destination email address — captured at request time so we
            still hold it even if the row is mid-modification.
    """
    try:
        await _build_and_deliver_export_inner(user_id, user_email)
    except Exception as exc:
        logger.error("Export failed for user=%s: %s", user_id, exc)
        # Sentinel stamp: last_export_expires_at in the past marks the request
        # as failed without clobbering last_export_requested_at (which remains
        # NULL or the original request timestamp, letting GET return "failed").
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE public.users "
                    "SET last_export_expires_at = now() - interval '1 second', "
                    "    updated_at = now() "
                    "WHERE id = $1",
                    user_id,
                )
        except Exception as stamp_exc:  # pragma: no cover
            logger.error(
                "Export failure sentinel stamp also failed for user=%s: %s",
                user_id, stamp_exc,
            )
        raise


async def _build_and_deliver_export_inner(user_id: str, user_email: str) -> None:
    """Core export flow — extracted from ``build_and_deliver_export`` so the
    outer wrapper owns the WR-08 failure-sentinel try/except block.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"users/{user_id}/exports/export-{ts}.zip"
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=EXPORT_URL_TTL_SECONDS,
    )

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Export profile covers every Phase-10-added user column so the user can
        # verify ToS acceptance, retention, deletion state, export history, and
        # (for their own records) their Stripe customer id. No billing PII beyond that.
        #
        # Phase 12 cutover: stripe_customer_id moved from public.users to
        # public.organizations. Resolve via JOIN through the caller's personal
        # org so the export profile still surfaces the same field with the
        # same key — preserving GDPR export shape across the schema change.
        profile = await conn.fetchrow(
            """SELECT u.id, u.email, u.display_name, u.created_at,
                      u.tos_version, u.tos_accepted_at,
                      u.data_retention_days, u.deletion_requested_at,
                      u.last_export_requested_at,
                      u.notification_preferences, o.stripe_customer_id
               FROM public.users u
               LEFT JOIN public.organization_memberships om ON om.user_id = u.id
               LEFT JOIN public.organizations o
                 ON o.id = om.organization_id AND o.is_personal = true
               WHERE u.id = $1""",
            user_id,
        )
        sessions = await conn.fetch(
            "SELECT * FROM public.sessions WHERE user_id = $1",
            user_id,
        )
        jobs = await conn.fetch(
            "SELECT * FROM public.jobs WHERE user_id = $1",
            user_id,
        )
        session_ids = [s["id"] for s in sessions]
        messages: list[Any] = []
        if session_ids:
            messages = await conn.fetch(
                "SELECT * FROM public.session_messages WHERE session_id = ANY($1::uuid[])",
                session_ids,
            )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "profile.json",
            json.dumps(dict(profile) if profile else {}, default=_json_default, indent=2),
        )
        zf.writestr(
            "sessions.json",
            json.dumps([dict(r) for r in sessions], default=_json_default, indent=2),
        )
        zf.writestr(
            "session_messages.json",
            json.dumps([dict(r) for r in messages], default=_json_default, indent=2),
        )
        zf.writestr(
            "jobs.json",
            json.dumps([dict(r) for r in jobs], default=_json_default, indent=2),
        )
        referenced_keys = [j["pdb_path"] for j in jobs if dict(j).get("pdb_path")]
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "generated_at": ts,
                    "user_id": user_id,
                    "referenced_object_keys": referenced_keys,
                    "notes": (
                        "PDB input files and job outputs remain in object storage; "
                        "their S3 keys are listed here. If you need the files themselves, "
                        "contact privacy@ranomics.com."
                    ),
                },
                indent=2,
            ),
        )
    buf.seek(0)

    s3 = get_s3_client()
    s3.put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=buf.getvalue(),
        ContentType="application/zip",
    )
    # CR-02: persist only the object key, not the presigned URL (bearer credential).
    # The router re-presigns on each authenticated GET. We still presign here once
    # so the email link works immediately — email URLs are unavoidable per the review.
    email_url = generate_presigned_get_url(key, expires_in=EXPORT_URL_TTL_SECONDS)

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE public.users
               SET last_export_requested_at = now(),
                   last_export_key = $2,
                   last_export_expires_at = $3,
                   updated_at = now()
               WHERE id = $1""",
            user_id,
            key,
            expires_at,
        )

    await send_export_ready_email(user_email, email_url, expires_at.isoformat())
    logger.info("Export delivered for user=%s key=%s", user_id, key)
