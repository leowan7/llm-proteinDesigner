"""Tests for job completion/failure email notifications (JOB-02).

Covers:
- Completion email sent via Resend with correct fields
- Failure email sent with error category

Implementation target: Plan 03-03.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestJobNotifications:
    """JOB-02: Email notifications are sent on job completion and failure."""

    @pytest.mark.anyio
    async def test_send_completion_email_calls_resend(self):
        """Mock resend.Emails.send and verify it is called with:
        - to: user email address
        - subject: contains tool name and design count
        - html: contains job URL with correct job_id

        The notifications module skips sending when RESEND_API_KEY is unset,
        so we patch settings to supply a dummy key for this test.
        """
        mock_send = MagicMock()

        with (
            patch("resend.Emails.send", mock_send),
            patch("jobs.notifications.settings") as mock_settings,
        ):
            mock_settings.resend_api_key = "re_test"
            mock_settings.resend_from_email = "Bindwave <jobs@bindwave.com>"
            mock_settings.app_base_url = "http://localhost:8000"
            from jobs.notifications import send_completion_email
            await send_completion_email(
                to_email="scientist@example.com",
                job_id="job-123",
                tool="rfdiffusion",
                num_designs=10,
                runtime_min=5,
            )

        mock_send.assert_called_once()
        call_params = mock_send.call_args[0][0]

        assert call_params["to"] == ["scientist@example.com"]
        # send_completion_email now title-cases known tool names per project
        # convention (rfdiffusion -> RFdiffusion); assert the display form.
        assert "RFdiffusion" in call_params["subject"]
        assert "10" in call_params["subject"]
        assert "job-123" in call_params["html"]

    @pytest.mark.anyio
    async def test_send_failure_email_calls_resend(self):
        """Mock resend.Emails.send and verify failure email is sent with:
        - to: user email address
        - subject: indicates failure
        - html: contains error_category to help the user understand what went wrong
        """
        mock_send = MagicMock()

        with (
            patch("resend.Emails.send", mock_send),
            patch("jobs.notifications.settings") as mock_settings,
        ):
            mock_settings.resend_api_key = "re_test"
            mock_settings.resend_from_email = "Bindwave <jobs@bindwave.com>"
            mock_settings.app_base_url = "http://localhost:8000"
            from jobs.notifications import send_failure_email
            await send_failure_email(
                to_email="scientist@example.com",
                job_id="job-456",
                error_category="OOM: GPU out of memory",
            )

        mock_send.assert_called_once()
        call_params = mock_send.call_args[0][0]

        assert call_params["to"] == ["scientist@example.com"]
        assert "OOM: GPU out of memory" in call_params["subject"]
        assert "OOM: GPU out of memory" in call_params["html"]

    @pytest.mark.anyio
    async def test_send_export_ready_email_points_at_a_bindwave_address(self):
        """The GDPR export email must not name a Ranomics address.

        Brand rule: Ranomics does not appear on Bindwave PRODUCT surfaces. The
        legal pages are the deliberate exception and still name Ranomics Inc.
        as the operating entity, because the data controller and contracting
        party must be identifiable there (frontend/src/pages/legal/Terms.tsx:9,
        :156, :204). An email body is a product surface, not a legal page.

        This email is sent from jobs@bindwave.com (backend/config.py:117), so a
        ranomics.com address in the body would also tell the user to reply to a
        different domain than the one that wrote to them.
        """
        mock_send = MagicMock()

        with (
            patch("resend.Emails.send", mock_send),
            patch("jobs.notifications.settings") as mock_settings,
        ):
            mock_settings.resend_api_key = "re_test"
            mock_settings.resend_from_email = "Bindwave <jobs@bindwave.com>"
            mock_settings.app_base_url = "http://localhost:8000"
            from jobs.notifications import send_export_ready_email
            await send_export_ready_email(
                to_email="scientist@example.com",
                presigned_url="https://r2.example.com/export.zip?sig=x",
                expires_at_iso="2026-09-26T00:00:00Z",
            )

        mock_send.assert_called_once()
        call_params = mock_send.call_args[0][0]

        assert "privacy@bindwave.com" in call_params["html"]
        assert "ranomics" not in call_params["html"].lower()
        assert "Bindwave" in call_params["subject"]
