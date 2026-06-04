"""Resend-backed invitation email helper for Phase 12.

Mirrors ``backend/jobs/notifications.py``:

- Sync ``resend.Emails.send`` wrapped in ``asyncio.to_thread`` so the FastAPI
  event loop is never blocked on a Resend round-trip.
- No-op + INFO log when ``settings.resend_api_key`` is empty (local dev,
  CI without secrets) — invite rows still get inserted, the email is just
  not sent.
- Errors are logged at WARNING and swallowed; an invitation flow must never
  break because the email provider hiccuped (the URL is also surfaced in the
  router response so the inviter can fall back to copy-paste).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import resend

from config import settings


logger = logging.getLogger(__name__)

resend.api_key = settings.resend_api_key


async def _send_email_safely(params: "resend.Emails.SendParams", purpose: str) -> None:
    """Send an email via Resend, swallowing any error with a warning log."""
    if not settings.resend_api_key:
        logger.info("Skipping %s email: RESEND_API_KEY not configured", purpose)
        return
    try:
        await asyncio.to_thread(resend.Emails.send, params)
    except Exception as exc:
        logger.warning(
            "Failed to send %s email (to=%s): %s",
            purpose, params.get("to"), exc,
        )


async def send_invitation_email(
    to_email: str,
    inviter_email: str,
    organization_name: str,
    role: str,
    accept_url: str,
    expires_at: datetime,
) -> None:
    """Send an organization invitation email.

    Args:
        to_email: Recipient email address (the invitee).
        inviter_email: Email of the user who created the invitation.
        organization_name: Org name shown in subject and body.
        role: Role being offered (``owner`` / ``scientist`` / ``viewer``).
        accept_url: Frontend URL the user clicks to accept (carries the token).
        expires_at: Invitation expiry timestamp; rendered as a date in the body.
    """
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"You've been invited to join {organization_name} on Bindwave",
        "html": (
            f"<p>{inviter_email} has invited you to join "
            f"<strong>{organization_name}</strong> as <strong>{role}</strong>.</p>"
            f'<p><a href="{accept_url}">Accept invitation</a></p>'
            f"<p>This link expires on {expires_at.strftime('%Y-%m-%d')}.</p>"
        ),
    }
    await _send_email_safely(params, purpose="invitation")
