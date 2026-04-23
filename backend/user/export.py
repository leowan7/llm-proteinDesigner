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
import zipfile
from typing import Any

from config import settings
from db.connection import get_db_pool
from jobs.notifications import send_export_ready_email
from storage.client import generate_presigned_get_url, get_s3_client

logger = logging.getLogger(__name__)

EXPORT_URL_TTL_SECONDS = 24 * 3600  # 24 hours


def _json_default(o: Any) -> Any:
    """JSON serializer for types asyncpg returns that ``json.dumps`` cannot handle.

    ``datetime``/``date`` -> ISO-8601 string. ``UUID`` and any object whose
    ``__str__`` is the canonical representation -> ``str(o)``. Falls through to
    ``str(o)`` for everything else so an unknown type never blocks the export.
    """
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    # UUID objects have a .hex attribute; use str() for the canonical form.
    if hasattr(o, "hex") and callable(getattr(o, "hex", None)):
        return str(o)
    return str(o)


async def build_and_deliver_export(user_id: str, user_email: str) -> None:
    """Build the export ZIP, upload to R2, presign, email, persist metadata.

    Steps:
      1. Pull user profile (all Phase-10 columns per W9), sessions, session_messages, jobs.
      2. Serialize to JSON files in an in-memory ZIP with a manifest.json.
      3. Upload to ``users/{user_id}/exports/export-{ts}.zip``.
      4. Presign GET URL (24 hours).
      5. ``UPDATE public.users`` with last_export_url + last_export_expires_at.
      6. Email the user the presigned URL.

    Args:
        user_id: Application user UUID (matches public.users.id).
        user_email: Destination email address — captured at request time so we
            still hold it even if the row is mid-modification.
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
        profile = await conn.fetchrow(
            """SELECT id, email, display_name, created_at,
                      tos_version, tos_accepted_at,
                      data_retention_days, deletion_requested_at,
                      last_export_requested_at,
                      notification_preferences, stripe_customer_id
               FROM public.users WHERE id = $1""",
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
    presigned_url = generate_presigned_get_url(key, expires_in=EXPORT_URL_TTL_SECONDS)

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE public.users
               SET last_export_requested_at = now(),
                   last_export_url = $2,
                   last_export_expires_at = $3,
                   updated_at = now()
               WHERE id = $1""",
            user_id,
            presigned_url,
            expires_at,
        )

    await send_export_ready_email(user_email, presigned_url, expires_at.isoformat())
    logger.info("Export delivered for user=%s key=%s", user_id, key)
