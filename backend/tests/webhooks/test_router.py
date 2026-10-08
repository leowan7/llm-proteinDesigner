"""Tests for RunPod webhook handler (/webhooks/runpod).

Covers:
- Completed job: DB status updated, billing recorded, pod terminated, email sent
- Failed job that saved nothing: DB status failed, no billing, pod terminated,
  failure email sent
- Failed job that already streamed designs to Storage: same failure handling,
  but the GPU time is metered
- Invalid payload (missing fields): acknowledged (webhook is permissive)
- Unknown job_id: acknowledged with received=True (webhook is permissive)
- Signature validation: 401 when HMAC signature is invalid

The webhook handler is designed to be permissive — it acknowledges unknown
jobs rather than raising 404, to avoid container retries. Tests verify the
business logic for valid jobs.

Uses patch() to replace get_db_pool and external service calls so no real
DB, Stripe, RunPod, or email service is required.
"""
import os

os.environ.setdefault("TESTING", "true")

import datetime
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter

_limiter.enabled = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JOB_ID = "job-1111-2222-3333"
POD_ID = "pod-abc123"
NOW_UTC = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
# started_at 5 minutes ago → 300 gpu_seconds
STARTED_AT = NOW_UTC - datetime.timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(conn):
    """Wrap a mock asyncpg connection in an async context manager.

    Args:
        conn: The mock asyncpg connection to wrap.

    Returns:
        AsyncMock configured for 'async with pool.acquire() as conn'.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_pool(*conn_sequence):
    """Build a pool mock that cycles through connections on acquire().

    Per Phase 3 lesson: router and worker DB pool mocks must be separate
    objects. The webhook handler calls pool.acquire() multiple times with
    different queries; each acquire() call gets its own conn mock.

    Args:
        *conn_sequence: AsyncMock connection objects in call order.

    Returns:
        AsyncMock pool with side_effect on acquire().
    """
    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[_make_ctx(c) for c in conn_sequence])
    return pool


def _completed_payload(timestamp: str | None = None) -> bytes:
    """Build a valid COMPLETED webhook payload as bytes.

    Args:
        timestamp: Optional ISO timestamp string for replay protection testing.

    Returns:
        JSON bytes.
    """
    payload = {
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "COMPLETED",
        "output": {
            "candidate_count": 5,
            "candidates": [],
            "next_steps": "Run AlphaFold validation",
            "job_spec": {"tool": "bindcraft"},
        },
    }
    if timestamp is not None:
        payload["timestamp"] = timestamp
    return json.dumps(payload).encode()


def _failed_payload() -> bytes:
    """Build a valid FAILED webhook payload as bytes."""
    return json.dumps({
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "FAILED",
        "error": {"category": "OOM error"},
    }).encode()


# ---------------------------------------------------------------------------
# Tests: completed job
# ---------------------------------------------------------------------------

async def test_webhook_completed_job():
    """POST /webhooks/runpod with COMPLETED status updates DB and sends email.

    Verifies:
    - Returns 200 with {"received": True}
    - update_job_status called with 'complete'
    - publish_status called
    - RunPodProvider.terminate_pod called for pod termination
    - send_completion_email called with correct job_id
    """
    # Conn 1: SELECT job by job_id
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    # Conn 2: SELECT status (double-processing guard) — not terminal
    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})

    # Conn 3: UPDATE results
    conn3 = AsyncMock()
    conn3.execute = AsyncMock()

    # Conn 4: SELECT stripe_customer_id
    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value={"stripe_customer_id": "cus_test"})

    # Conn 5: SELECT email from auth.users
    conn5 = AsyncMock()
    conn5.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4, conn5)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock) as mock_email,
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_completed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert response.json() == {"received": True}

    # DB status was updated to complete
    mock_update.assert_called_once()
    assert mock_update.call_args[0][1] == "complete"

    # SSE status was published
    mock_publish.assert_called()

    # Pod was terminated
    mock_provider.terminate_pod.assert_called_once_with(POD_ID)

    # Billing was recorded (completed job has gpu_seconds > 0)
    mock_billing.assert_called_once()

    # Completion email was sent
    mock_email.assert_called_once()
    call_kwargs = mock_email.call_args[1]
    assert call_kwargs["to_email"] == "test@example.com"
    assert call_kwargs["job_id"] == JOB_ID


# ---------------------------------------------------------------------------
# Tests: failed job
# ---------------------------------------------------------------------------

async def test_webhook_failed_job():
    """A failed run that saved no designs is not billed.

    This is the zero-saved-designs side of the 2026-10-07 billing rule: a
    crashed run of a tool that can save designs before failing is billed only
    when Storage holds at least one of them, so one that saved none stays
    free. The tool here is in ``_MAY_SAVE_DESIGNS_BEFORE_FAILING``, so the
    handler really does ask Storage -- which the assertion on the patched
    lookup pins.

    Verifies:
    - Returns 200 with {"received": True}
    - update_job_status called with 'failed'
    - Storage consulted for the failed job, and billing NOT recorded
    - send_failure_email called
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})

    conn3 = AsyncMock()
    conn3.execute = AsyncMock()

    # Conn 4: SELECT email from auth.users (failed job skips billing, goes to email)
    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_fail_email,
        patch("webhooks.router.job_has_saved_designs", return_value=False) as mock_saved,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert response.json() == {"received": True}

    # Status updated to failed with error_category
    mock_update.assert_called_once()
    assert mock_update.call_args[0][1] == "failed"

    # Storage was asked, with the launcher's user id and this job id, and said
    # no designs (the prefix itself is pinned in tests/storage/)
    mock_saved.assert_called_once_with("user-uuid", JOB_ID)

    # So no billing: a failure is billed only when it saved something
    mock_billing.assert_not_called()

    # Failure email was sent
    mock_fail_email.assert_called_once()
    assert mock_fail_email.call_args[1]["to_email"] == "test@example.com"


async def test_webhook_failed_job_with_saved_designs_is_billed():
    """A failed run that already streamed designs is settled on GPU time used.

    Leo's call 2026-10-07 for the BindCraft save-as-you-go path: a run that
    saved at least one design and then crashed or timed out is billed the same
    as a finished run, not prorated. The "not prorated" half is pinned by
    comparing the metered figure against the gpu_seconds written to the job
    row -- the same number a completed run meters. That is an
    internal-consistency pin, not a claim the figure is right: started_at is
    COALESCE-preserved across sessions (worker/session_orchestrator.py:239), so
    for a campaign chunked across them it spans the gaps between sessions.

    Everything else about the failure is unchanged: the status is still
    'failed' and the failure email, not the completion email, goes out.
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})

    conn3 = AsyncMock()
    conn3.execute = AsyncMock()

    # Conn 4: SELECT org_stripe_customer -- only acquired when the run is billed
    conn4 = AsyncMock()
    conn4.fetchval = AsyncMock(return_value="cus_crashed")

    # Conn 5: SELECT email from auth.users
    conn5 = AsyncMock()
    conn5.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4, conn5)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock) as mock_ok_email,
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_fail_email,
        patch("webhooks.router.job_has_saved_designs", return_value=True) as mock_saved,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200

    mock_saved.assert_called_once_with("user-uuid", JOB_ID)

    # Billed, to the customer the org resolver returned
    mock_billing.assert_called_once()
    customer, billed_job, billed_seconds = mock_billing.call_args[0]
    assert customer == "cus_crashed"
    assert billed_job == JOB_ID

    # Not prorated: the metered seconds are the job row's own gpu_seconds
    assert billed_seconds > 0
    assert billed_seconds == mock_update.call_args[1]["gpu_seconds"]

    # Still a failure in every other respect
    assert mock_update.call_args[0][1] == "failed"
    mock_fail_email.assert_called_once()
    mock_ok_email.assert_not_called()


async def test_a_metering_failure_still_emails_the_customer():
    """A Stripe error must not swallow the failure notice.

    Before this rule a failed run could not reach record_gpu_usage at all, so
    the failure email always went out. Now that it can, an unguarded raise
    escapes the handler before the email block below it -- and the row is
    already 'failed', so the provider's retry returns at the
    double-processing guard and the customer never hears the run died. The
    charge is the droppable half, not the notification.
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})

    conn3 = AsyncMock()
    conn3.execute = AsyncMock()

    conn4 = AsyncMock()
    conn4.fetchval = AsyncMock(return_value="cus_crashed")

    conn5 = AsyncMock()
    conn5.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4, conn5)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock),
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch(
            "webhooks.router.record_gpu_usage",
            side_effect=RuntimeError("stripe is down"),
        ) as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_fail_email,
        patch("webhooks.router.job_has_saved_designs", return_value=True),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    # The metering was attempted, and raised
    mock_billing.assert_called_once()

    # The customer still got told, on a 200 the provider will not retry
    assert response.status_code == 200
    mock_fail_email.assert_called_once()


async def test_webhook_failed_job_of_a_tool_outside_the_rule_never_asks_storage():
    """A tool outside _MAY_SAVE_DESIGNS_BEFORE_FAILING keeps the old all-or-nothing rule.

    Save-as-you-go lands one tool per PR, so the billing rule has to stay off
    for every tool that has not joined the tuple. Pinning that Storage is not
    even consulted keeps the gate from widening by accident: billing an
    rfdiffusion run that crashed is a call nobody has made.
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "rfdiffusion",
    })

    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})

    conn3 = AsyncMock()
    conn3.execute = AsyncMock()

    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock),
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock),
        patch("webhooks.router.job_has_saved_designs", return_value=True) as mock_saved,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    mock_saved.assert_not_called()
    mock_billing.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: unknown / missing job
# ---------------------------------------------------------------------------

async def test_webhook_job_not_found():
    """POST /webhooks/runpod for unknown job_id returns 200 acknowledged.

    The webhook handler logs a warning and returns {"received": True} rather
    than raising 404 — avoiding container retry loops.
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value=None)

    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value=None)

    mock_pool = _make_pool(conn1, conn2)

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=json.dumps({
                    "id": "unknown-job-id",
                    "pod_id": "unknown-pod",
                    "status": "COMPLETED",
                }).encode(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert response.json() == {"received": True}


# ---------------------------------------------------------------------------
# Tests: invalid payload
# ---------------------------------------------------------------------------

async def test_webhook_invalid_payload():
    """POST /webhooks/runpod with missing required fields returns 200 acknowledged.

    The webhook handler is designed to be permissive — it uses .get() on the
    payload dict, so missing fields result in empty strings. An unrecognised
    status value (empty string) causes the handler to return {"received": True}
    without performing any DB operations.
    """
    empty_payload = json.dumps({}).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/runpod",
            content=empty_payload,
            headers={"Content-Type": "application/json"},
        )

    # Empty JSON with no valid status → acknowledged and ignored
    assert response.status_code == 200
    assert response.json() == {"received": True}


# ---------------------------------------------------------------------------
# Tests: signature validation
# ---------------------------------------------------------------------------

async def test_webhook_signature_validation():
    """Invalid HMAC-SHA256 signature returns 401 when webhook secret is configured."""
    secret = "test-webhook-secret-32-bytes-long!"
    body = _completed_payload()

    # Compute wrong signature
    wrong_sig = "deadbeef" * 8

    with patch("webhooks.router.settings") as mock_settings:
        # Phase 11 D-10 rename: set both the new canonical field and the deprecated
        # alias so validate_webhook_signature reads the real secret (MagicMock
        # auto-attrs would otherwise be a truthy non-string).
        mock_settings.webhook_hmac_secret = secret
        mock_settings.webhook_hmac_secret_prev = ""
        mock_settings.runpod_webhook_secret = secret
        mock_settings.gpu_price_per_second = 0.0001
        mock_settings.gpu_markup_percent = 30.0

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-RunPod-Signature": wrong_sig,
                },
            )

    assert response.status_code == 401


async def test_webhook_valid_signature():
    """Correct HMAC-SHA256 signature passes validation and processes payload."""
    secret = "test-webhook-secret-32-bytes-long!"
    body = _completed_payload()

    # Compute correct HMAC signature
    correct_sig = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })
    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})
    conn3 = AsyncMock()
    conn3.execute = AsyncMock()
    conn4 = AsyncMock()
    conn4.fetchrow = AsyncMock(return_value={"stripe_customer_id": None})
    conn5 = AsyncMock()
    conn5.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4, conn5)

    with (
        patch("webhooks.router.settings") as mock_settings,
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock),
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider") as MockProvider,
        patch("webhooks.router.record_gpu_usage"),
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock),
    ):
        # Phase 11 D-10 rename: set new canonical field + deprecated alias.
        mock_settings.webhook_hmac_secret = secret
        mock_settings.webhook_hmac_secret_prev = ""
        mock_settings.runpod_webhook_secret = secret
        mock_settings.gpu_price_per_second = 0.0001
        mock_settings.gpu_markup_percent = 30.0
        mock_settings.runpod_api_key = "test-key"
        MockProvider.return_value.terminate_pod = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-RunPod-Signature": correct_sig,
                },
            )

    assert response.status_code == 200
    assert response.json() == {"received": True}


# ---------------------------------------------------------------------------
# Tests: double-processing guard
# ---------------------------------------------------------------------------

async def test_webhook_skips_terminal_job():
    """Webhook for a job already in terminal state returns 200 without re-processing."""
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    # Job is already complete
    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "complete"})

    mock_pool = _make_pool(conn1, conn2)

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_completed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    # update_job_status should NOT be called (already terminal)
    mock_update.assert_not_called()
