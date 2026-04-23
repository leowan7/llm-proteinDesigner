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
"""

import logging

import resend

from config import settings

logger = logging.getLogger(__name__)

resend.api_key = settings.resend_api_key


def _send_email_safely(params: "resend.Emails.SendParams", purpose: str) -> None:
    """Send an email via Resend, swallowing any error with a warning log.

    The job flow (completion/failure webhook, daily progress cron) must never
    break just because email delivery failed — missing API key in local dev,
    Resend rate limits, recipient domain misconfig, etc. Log loudly and move on.

    Args:
        params: Resend ``SendParams`` dict.
        purpose: Short label for the log line ("completion", "failure", etc.).
    """
    if not settings.resend_api_key:
        logger.info("Skipping %s email: RESEND_API_KEY not configured", purpose)
        return
    try:
        resend.Emails.send(params)
    except Exception as exc:
        logger.warning(
            "Failed to send %s email (to=%s): %s",
            purpose, params.get("to"), exc,
        )


async def send_completion_email(
    to_email: str,
    job_id: str,
    tool: str,
    num_designs: int,
    runtime_min: int,
) -> None:
    """Send a job completion notification email via Resend.

    Args:
        to_email: Recipient email address.
        job_id: Job UUID string (used to build the results URL).
        tool: Tool name (e.g. "rfdiffusion") shown in subject and body.
        num_designs: Number of designs generated, shown in subject.
        runtime_min: Approximate runtime in minutes, shown in body.
    """
    job_url = f"{settings.app_base_url}/jobs/{job_id}"
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"Your {tool} job is complete — {num_designs} designs generated",
        "html": (
            f"<p>Your {tool} job completed in {runtime_min} minutes. "
            f"{num_designs} designs are ready for download.</p>"
            f'<p><a href="{job_url}">View results</a></p>'
        ),
    }
    _send_email_safely(params, purpose="completion")


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
    job_url = f"{settings.app_base_url}/jobs/{job_id}"
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": f"Your job encountered an error — {error_category}",
        "html": (
            f"<p>Your job failed: {error_category}.</p>"
            f'<p><a href="{job_url}">View details</a></p>'
        ),
    }
    _send_email_safely(params, purpose="failure")


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
    job_url = f"{settings.app_base_url}/jobs/{job_id}/progress"
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
    _send_email_safely(params, purpose="daily_progress")


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
    job_url = f"{settings.app_base_url}/jobs/{job_id}/progress"
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
    _send_email_safely(params, purpose="first_filter_pass")


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
    _send_email_safely(params, purpose="data_export")


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
    _send_email_safely(params, purpose="deletion_scheduled")


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
    _send_email_safely(params, purpose="deletion_completed")
