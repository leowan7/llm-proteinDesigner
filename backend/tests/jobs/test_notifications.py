"""Tests for job completion/failure email notifications (JOB-02).

Covers:
- Completion email sent via Resend with correct fields
- Failure email sent with error category

Implementation target: Plan 03-03.
"""

import pytest


class TestJobNotifications:
    """JOB-02: Email notifications are sent on job completion and failure."""

    def test_send_completion_email_calls_resend(self):
        """Mock resend.Emails.send and verify it is called with:
        - to: user email address
        - subject: contains tool name and design count
        - html: contains job URL with correct job_id

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_send_failure_email_calls_resend(self):
        """Mock resend.Emails.send and verify failure email is sent with:
        - to: user email address
        - subject: indicates failure
        - html: contains error_category to help the user understand what went wrong

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
