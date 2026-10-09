"""Tests for job completion/failure email notifications (JOB-02).

Covers:
- Completion email sent via Resend with correct fields
- Failure email sent with error category
- Failure email of a run that saved designs leads with them, and says the set
  is partial

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

    @pytest.mark.anyio
    async def test_a_failure_that_saved_designs_leads_with_them(self):
        """A partial run's mail points at the designs instead of only the error.

        The run still failed, so the mail says so, but burying that two designs
        survived under a bare failure notice is what the delivery work exists
        to stop. The wording also has to prevent the customer reading a partial
        set as a finished one: it names the count, calls the set partial, and
        says the final scoring pass is missing.
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
                error_category="Timed out",
                saved_designs=2,
            )

        call_params = mock_send.call_args[0][0]

        assert "2 designs saved" in call_params["subject"]
        assert "Timed out" in call_params["html"]
        assert "2 designs" in call_params["html"]
        assert "partial set" in call_params["html"]
        assert "without the final" in call_params["html"]
        assert "/jobs/job-456" in call_params["html"]

    @pytest.mark.anyio
    async def test_a_single_saved_design_is_not_pluralised(self):
        """One design is "1 design ... is saved", not "1 designs ... are".

        Worth a test because the failure mail is the first thing a customer
        sees after a run they paid for died, and one is the boundary of the
        branch that announces designs at all (``if saved_designs > 0``,
        ``jobs/notifications.py:162``), for every tool rather than a billed one.
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
                error_category="Timed out",
                saved_designs=1,
            )

        call_params = mock_send.call_args[0][0]

        assert "1 design saved" in call_params["subject"]
        assert "The 1 design it had already produced is saved" in call_params["html"]
