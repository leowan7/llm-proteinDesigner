"""Threshold-gated daily GPU spend alert tests (SC 8 precursor).

Target: ``worker.cleanup.check_daily_gpu_spend`` — sums ``gpu_cost_usd`` across
jobs completed in the last 24h and emails via Resend if the total exceeds
``settings.gpu_daily_spend_alert_usd`` (default $50).

Two cases:
  1. <$50 total -> no email sent.
  2. >$50 total -> exactly one email sent with subject/body mentioning the
     amount.

Uses patch() to replace ``get_db_pool`` (returns a pool whose fetchrow yields
a preset total_spend) and to replace the ``resend`` module import so no real
email goes out.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "true")


def _make_pool_with_total(total_spend: float) -> AsyncMock:
    """Build a mock asyncpg pool whose acquire() ctx yields a conn that
    returns ``{"total_spend": total_spend}`` on fetchrow()."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"total_spend": total_spend})

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


@pytest.mark.asyncio
async def test_below_threshold_sends_no_email():
    """Total spend of $10 stays under the $50 default threshold -> no Resend call."""
    from worker import cleanup

    pool = _make_pool_with_total(10.0)

    # A fake 'resend' module so `import resend` inside cleanup.py resolves to us.
    mock_resend = MagicMock()
    mock_resend.Emails = MagicMock()
    mock_resend.Emails.send = MagicMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=pool),
        patch.dict("sys.modules", {"resend": mock_resend}),
        patch.object(cleanup.settings, "gpu_daily_spend_alert_usd", 50.0),
        patch.object(cleanup.settings, "resend_api_key", "fake-key"),
        patch.object(cleanup.settings, "resend_from_email", "jobs@kendrew.ai"),
    ):
        await cleanup.check_daily_gpu_spend()

    assert mock_resend.Emails.send.called is False, (
        "Expected no email when total spend ($10) is below threshold ($50)"
    )


@pytest.mark.asyncio
async def test_above_threshold_sends_one_email():
    """Total spend of $60 exceeds $50 threshold -> exactly one Resend.send call."""
    from worker import cleanup

    pool = _make_pool_with_total(60.0)

    mock_resend = MagicMock()
    mock_resend.Emails = MagicMock()
    mock_resend.Emails.send = MagicMock()

    with (
        patch("worker.cleanup.get_db_pool", new_callable=AsyncMock, return_value=pool),
        patch.dict("sys.modules", {"resend": mock_resend}),
        patch.object(cleanup.settings, "gpu_daily_spend_alert_usd", 50.0),
        patch.object(cleanup.settings, "resend_api_key", "fake-key"),
        patch.object(cleanup.settings, "resend_from_email", "jobs@kendrew.ai"),
    ):
        await cleanup.check_daily_gpu_spend()

    assert mock_resend.Emails.send.call_count == 1, (
        f"Expected exactly 1 email, got {mock_resend.Emails.send.call_count}"
    )

    # The payload is passed as the first positional arg (a dict).
    sent_payload = mock_resend.Emails.send.call_args[0][0]
    assert "GPU spend alert" in sent_payload["subject"], (
        f"Expected 'GPU spend alert' in subject, got: {sent_payload['subject']!r}"
    )
    assert "$60.00" in sent_payload["text"], (
        f"Expected '$60.00' in email body, got: {sent_payload['text']!r}"
    )
