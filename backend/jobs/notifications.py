"""Email notification functions for job completion and failure.

Uses the Resend API to send transactional emails. API key is set at module
import from settings; tests should patch resend.Emails.send after import.
"""

import resend

from config import settings

resend.api_key = settings.resend_api_key


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
    resend.Emails.send(params)


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
    resend.Emails.send(params)
