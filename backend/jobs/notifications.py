"""Email notification functions for job completion and failure.

Uses the Resend API to send transactional emails. API key is set at module
import from settings; tests should patch resend.Emails.send after import.

Phase 5 of the Modal migration adds two milestone-style emails for
long-running full-design jobs:

- ``send_daily_progress_email`` — fires at ~24hr boundaries during a
  multi-day campaign so the scientist knows the job is alive without having
  to open the dashboard.
- ``send_first_filter_pass_email`` — fires the first time a candidate passes
  all default filters. Turns "still running…" into "here's something real to
  look at."

Neither fires on pilot jobs by default (pilots are short — the completion
email is enough). Both are opt-in configurable per user in the Phase 5
settings page (``frontend/src/pages/settings/notifications.tsx``).

WR-09: ``resend.Emails.send`` is a synchronous HTTP call. This module's
public senders are ``async`` functions called from the request path (FastAPI
BackgroundTasks) and the arq cron workers — running the sync call directly
would block the event loop for the duration of each Resend round-trip. All
sends are dispatched via ``asyncio.to_thread`` so the event loop stays free.
"""

import asyncio
import logging

import resend
from config import settings

logger = logging.getLogger(__name__)

resend.api_key = settings.resend_api_key


async def _send_email_safely(params: "resend.Emails.SendParams", purpose: str) -> None:
    """Send an email via Resend, swallowing any error with a warning log.

    The job flow (completion/failure webhook, daily progress cron) must never
    break just because email delivery failed — missing API key in local dev,
    Resend rate limits, recipient domain misconfig, etc. Log loudly and move on.

    WR-09: the underlying ``resend.Emails.send`` is sync HTTP. Wrap in
    ``asyncio.to_thread`` so the event loop is not blocked for the duration
    of the Resend call — serializes under cron load otherwise.

    Args:
        params: Resend ``SendParams`` dict.
        purpose: Short label for the log line ("completion", "failure", etc.).
    """
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


# Internal tool keys -> user-facing display names per project convention
# (see backend/agent/system_prompt.py "DESIGN TOOLS" section).
_TOOL_DISPLAY_NAMES = {
    "rfdiffusion": "RFdiffusion",
    "bindcraft": "BindCraft",
    "rfantibody": "RFantibody",
    "boltzgen": "BoltzGen",
    "pxdesign": "PXDesign",
}


def _format_runtime(runtime_seconds: int, runtime_min: int) -> str:
    """Render runtime in the shortest accurate human unit.

    < 60 s  -> "23 seconds"
    < 60 min -> "5 minutes" / "1 minute"
    >= 60 min -> "1 hour 23 minutes" / "2 hours"
    """
    if runtime_seconds < 60:
        return f"{runtime_seconds} second{'' if runtime_seconds == 1 else 's'}"
    if runtime_min < 60:
        return f"{runtime_min} minute{'' if runtime_min == 1 else 's'}"
    hours, mins = divmod(runtime_min, 60)
    hour_str = f"{hours} hour{'' if hours == 1 else 's'}"
    if mins == 0:
        return hour_str
    return f"{hour_str} {mins} minute{'' if mins == 1 else 's'}"


async def send_completion_email(
    to_email: str,
    job_id: str,
    tool: str,
    num_designs: int,
    runtime_min: int,
    runtime_seconds: int | None = None,
) -> None:
    """Send a job completion notification email via Resend.

    Args:
        to_email: Recipient email address.
        job_id: Job UUID string (used to build the results URL).
        tool: Tool name (e.g. "rfdiffusion") shown in subject and body.
        num_designs: Number of designs generated, shown in subject.
        runtime_min: Approximate runtime in minutes, shown in body.
        runtime_seconds: Optional sub-minute precision for short runs --
            when present, "<1 min" runs render as "N seconds" instead of
            "0 minutes" (added 2026-06-03 after the SC 6 close-out email
            showed "in 2 minutes" but failed to display tool name).
    """
    job_url = f"{settings.frontend_base_url}/jobs/{job_id}"
    tool_display = _TOOL_DISPLAY_NAMES.get(tool.lower(), tool)
    designs_word = "design" if num_designs == 1 else "designs"
    runtime_str = _format_runtime(
        runtime_seconds if runtime_seconds is not None else runtime_min * 60,
        runtime_min,
    )
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": (
            f"Your {tool_display} job is complete — "
            f"{num_designs} {designs_word} generated"
        ),
        "html": (
            f"<p>Your {tool_display} job completed in {runtime_str}. "
            f"{num_designs} {designs_word} {'is' if num_designs == 1 else 'are'} "
            f"ready for download.</p>"
            f'<p><a href="{job_url}">View results</a></p>'
        ),
    }
    await _send_email_safely(params, purpose="completion")


async def send_failure_email(
    to_email: str,
    job_id: str,
    error_category: str,
) -> None:
    """Send a job failure notification email via Resend.

    Args:
        to_email: Recipient email address.
        job_id: Job UUID string (used to build the details URL).
        error_category: Human-readable error category string from the provider.
    """
    job_url = f"{settings.frontend_base_url}/jobs/{job_id}"
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"Your job encountered an error — {error_category}",
        "html": (
            f"<p>Your job failed: {error_category}.</p>"
            f'<p><a href="{job_url}">View details</a></p>'
        ),
    }
    await _send_email_safely(params, purpose="failure")


async def send_daily_progress_email(
    to_email: str,
    job_id: str,
    tool: str,
    day_number: int,
    designs_completed: int,
    designs_total: int,
    top_ipsae: float | None = None,
) -> None:
    """Send a 24hr-boundary update on a multi-day full-design job.

    Fires at each 24hr boundary of a chunked full-design job (day 2, day 3,
    day 4). Suppressed on pilot jobs (too short) and on full-design jobs with
    ``total_budget_hours <= 23`` (single-session — completion email suffices).

    Args:
        to_email: Recipient email address.
        job_id: Job UUID string.
        tool: Tool name (shown in subject).
        day_number: 2 for the first 24hr checkpoint, 3 for 48hr, etc.
        designs_completed: Count of designs done so far.
        designs_total: Projected total designs at the current rate.
        top_ipsae: Best ipSAE/ipTM score observed so far (None if nothing has
            passed default filters yet).
    """
    job_url = f"{settings.frontend_base_url}/jobs/{job_id}/progress"
    top_line = (
        f"Best score so far: ipSAE {top_ipsae:.3f}."
        if top_ipsae is not None
        else "No candidates have passed default filters yet — normal at this stage."
    )
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"Day {day_number}: your {tool} job — {designs_completed}/{designs_total} designs done",
        "html": (
            f"<p>Your {tool} campaign has been running for about {day_number - 1} day"
            f"{'s' if day_number > 2 else ''}.</p>"
            f"<p>{designs_completed} of ~{designs_total} designs complete. {top_line}</p>"
            f'<p><a href="{job_url}">Open the live progress page</a></p>'
        ),
    }
    await _send_email_safely(params, purpose="daily_progress")


async def send_first_filter_pass_email(
    to_email: str,
    job_id: str,
    tool: str,
    candidate_rank: int,
    ipsae: float,
    plddt: float,
) -> None:
    """Send a "first quality candidate" email when a design first clears all filters.

    Only fires once per job, the first time ``designs_completed`` transitions
    from 0 to >=1 "accepted" candidate. Quiet afterwards regardless of how
    many more pass.

    Args:
        to_email: Recipient email address.
        job_id: Job UUID string.
        tool: Tool name.
        candidate_rank: Rank of the candidate (usually 1 since it's the first).
        ipsae: ipSAE / ipTM score.
        plddt: pLDDT score.
    """
    job_url = f"{settings.frontend_base_url}/jobs/{job_id}/progress"
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"Your first quality {tool} candidate is ready",
        "html": (
            f"<p>Good news — your {tool} job just produced its first candidate "
            f"passing all default filters.</p>"
            f"<p>Rank {candidate_rank}: ipSAE {ipsae:.3f}, pLDDT {plddt:.2f}.</p>"
            f"<p>The job is still running — more candidates will appear as it progresses.</p>"
            f'<p><a href="{job_url}">Preview the candidate</a></p>'
        ),
    }
    await _send_email_safely(params, purpose="first_filter_pass")


# ---------------------------------------------------------------------------
# Phase 10 Plan 04 — GDPR export + deletion notifications
# ---------------------------------------------------------------------------


async def send_export_ready_email(
    to_email: str,
    presigned_url: str,
    expires_at_iso: str,
) -> None:
    """Notify a user that their GDPR Article 20 data export ZIP is ready.

    Args:
        to_email: Recipient email address.
        presigned_url: Time-limited R2 URL to the export ZIP.
        expires_at_iso: ISO-8601 timestamp when the URL stops working.
    """
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": "Your Kendrew data export is ready",
        "html": (
            "<p>Your data export has been generated. Download the ZIP before the link expires:</p>"
            f'<p><a href="{presigned_url}">Download my data</a></p>'
            f"<p>Link expires: {expires_at_iso}.</p>"
            "<p>If you did not request this export, contact privacy@ranomics.com immediately.</p>"
        ),
    }
    await _send_email_safely(params, purpose="data_export")


async def send_deletion_scheduled_email(
    to_email: str,
    scheduled_for_iso: str,
    cancel_url: str,
) -> None:
    """Confirm that an account deletion request has been scheduled.

    Args:
        to_email: Recipient email address.
        scheduled_for_iso: ISO-8601 timestamp of the hard-delete execution (deletion_requested_at + 30 days).
        cancel_url: Absolute URL to the Privacy tab where the user can cancel.
    """
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": "Your Kendrew account deletion is scheduled",
        "html": (
            "<p>We have received your account deletion request.</p>"
            f"<p>Your account and all associated data will be permanently deleted on <strong>{scheduled_for_iso}</strong>.</p>"
            f'<p>Change your mind? <a href="{cancel_url}">Cancel deletion</a>.</p>'
            "<p>If you did not request deletion, sign in now and cancel immediately, then change your password.</p>"
        ),
    }
    await _send_email_safely(params, purpose="deletion_scheduled")


async def send_retention_warning_email(
    to_email: str,
    job_id: str,
    job_name: str | None,
    deletion_date_iso: str,
    retention_days: int,
) -> None:
    """7-day warning before automatic retention deletion (Plan 10-05).

    Args:
        to_email: Recipient email.
        job_id: Kendrew job UUID string.
        job_name: User-chosen job name (if any) for subject clarity.
        deletion_date_iso: When the hard delete will occur (ISO date).
        retention_days: The user's current retention window (default 90).
    """
    label = job_name or f"Job {job_id[:8]}"
    settings_url = f"{settings.frontend_base_url}/settings?tab=privacy"
    job_url = f"{settings.frontend_base_url}/jobs/{job_id}"
    # Uniform f-string block — mixed quote styles previously caused a SyntaxError
    # on import (W11); every anchor uses double-quoted HTML attributes, and the
    # f-string itself is a single concatenated expression.
    html = (
        f"<p>Your Kendrew retention policy ({retention_days} days) will permanently delete "
        f"<strong>{label}</strong> on <strong>{deletion_date_iso}</strong>.</p>"
        f'<p>To keep this run, download the outputs now: <a href="{job_url}">View job</a>.</p>'
        f'<p>To change your retention window (30-365 days), visit <a href="{settings_url}">Settings &rarr; Privacy</a>.</p>'
    )
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"'{label}' will be deleted on {deletion_date_iso} (retention policy)",
        "html": html,
    }
    await _send_email_safely(params, purpose="retention_warning")


async def send_deletion_completed_email(to_email: str) -> None:
    """Final notification that the hard-delete has executed.

    Sent from the cron executor (user row is already gone when this fires, but
    we retained the email address on the stack for this one last send).

    Args:
        to_email: Recipient email address.
    """
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": "Your Kendrew account has been deleted",
        "html": (
            "<p>Your account and all associated data have been permanently deleted per your request.</p>"
            "<p>This is our final communication. Thank you for using Kendrew.</p>"
        ),
    }
    await _send_email_safely(params, purpose="deletion_completed")
