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

import pytest
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
    # asyncpg's conn.transaction() is a sync call returning an async context
    # manager. Left as a plain AsyncMock attribute it would return a
    # coroutine, which `async with` rejects, so every connection handed to
    # the handler gets one: the claim and the writes it authorises run inside
    # it (webhooks/router.py:273). __aexit__ returns False so an exception
    # raised inside the block propagates, as a real transaction's does.
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
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

    This is the zero-saved-designs side of the billing rule: a crashed run is
    billed only when Storage holds at least one of its designs, so one that
    saved none stays free. Since 2026-10-13 that applies to every tool rather
    than the single one the deleted tuple named, and the one Storage read the
    handler makes decides both the charge and the mail -- which the assertion
    on the patched lookup pins.

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
        # One read, two consumers: the charge is gated on this count and the
        # failure mail reports it. Unpatched it reaches boto3 against an
        # unreachable endpoint and burns its retry budget inside the handler's
        # own except.
        patch("webhooks.router.list_job_design_keys", return_value=[]) as mock_list,
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

    # So no billing: a failure is billed only when it saved something
    mock_billing.assert_not_called()

    # Failure email was sent, and with nothing in Storage it carries a count of
    # zero -- the branch in jobs/notifications.py that keeps the plain
    # bare-failure wording
    mock_fail_email.assert_called_once()
    assert mock_fail_email.call_args[1]["to_email"] == "test@example.com"
    assert mock_fail_email.call_args[1]["saved_designs"] == 0

    # Storage was asked exactly once, with the launcher's user id and this job
    # id (the prefix itself is pinned in tests/storage/). Once, not twice,
    # because the charge and the mail read the same count.
    mock_list.assert_called_once_with("user-uuid", JOB_ID)


async def test_webhook_failed_job_with_saved_designs_is_billed():
    """A failed run that already streamed designs is settled on GPU time used.

    Leo's call 2026-10-07, widened to every tool on 2026-10-13: a run that
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
        patch(
            "webhooks.router.list_job_design_keys",
            return_value=[f"users/user-uuid/jobs/{JOB_ID}/outputs/design_1.pdb"],
        ) as mock_list,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200

    mock_list.assert_called_once_with("user-uuid", JOB_ID)

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
        patch(
            "webhooks.router.list_job_design_keys",
            return_value=[f"users/user-uuid/jobs/{JOB_ID}/outputs/design_1.pdb"],
        ),
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


async def test_webhook_failed_job_of_a_non_streaming_tool_is_billed_when_it_saved_designs():
    """Deliberately inverted on 2026-10-13: the tool no longer decides.

    This test asserted the opposite until this commit. It pinned
    ``_STREAMS_DESIGNS_MID_RUN``, a tuple in webhooks/router.py -- deleted by
    this commit, so there is no line left to cite -- which held exactly one
    tool at this branch's base, ``("bindcraft",)``
    (``git show a479ac38:backend/webhooks/router.py``, line 56). A failed run
    of every other tool was handed its saved designs for free, and this test
    checked that for rfdiffusion. Leo's call on 2026-10-13 is that delivery
    and billing are one predicate for every tool, so that tuple and the test
    that pinned it are both gone. The gate is now "the run failed and Storage
    holds at least one design", which this rfdiffusion run meets.

    rfdiffusion is the tool here because it was one of the excluded ones, not
    because it fails to stream: its container uploads each design as its
    scores parse (``docker/rfdiffusion/run_pipeline.py:2108``, inside the
    per-design loop, added by #33). The tuple was simply never widened past
    bindcraft as #33-#35 added that streaming to every container, so a crash
    mid-run delivered designs nothing charged for. That gap is what this
    closes.
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

    # Conn 4: SELECT org_stripe_customer -- reached now, where the tool gate
    # used to short-circuit before it
    conn4 = AsyncMock()
    conn4.fetchval = AsyncMock(return_value="cus_rfd")

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
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_fail_email,
        patch(
            "webhooks.router.list_job_design_keys",
            return_value=[
                f"users/user-uuid/jobs/{JOB_ID}/outputs/design_1.pdb",
                f"users/user-uuid/jobs/{JOB_ID}/outputs/design_2.pdb",
            ],
        ) as mock_list,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200

    # Billed, on the same count the mail reports
    mock_billing.assert_called_once()
    customer, billed_job, billed_seconds = mock_billing.call_args[0]
    assert customer == "cus_rfd"
    assert billed_job == JOB_ID
    assert billed_seconds == mock_update.call_args[1]["gpu_seconds"]

    # One read of the bucket behind both the charge and the mail, which names
    # both designs this rfdiffusion run banked
    mock_list.assert_called_once_with("user-uuid", JOB_ID)
    assert mock_fail_email.call_args[1]["saved_designs"] == 2


async def test_a_storage_fault_leaves_a_failed_run_unbilled():
    """An unreadable bucket must not become a charge.

    The count the charge rests on now comes from a network call, so the
    failure mode matters: a listing that raises leaves the count at zero,
    which leaves the run unbilled -- what every failure did before 2026-10-13
    -- and sends the plain failure notice rather than guessing at a number or
    losing the mail.
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
        patch(
            "webhooks.router.list_job_design_keys",
            side_effect=OSError("bucket unreachable"),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    mock_billing.assert_not_called()

    # The run is still failed and the customer still hears about it, with
    # nothing to collect
    assert mock_update.call_args[0][1] == "failed"
    mock_fail_email.assert_called_once()
    assert mock_fail_email.call_args[1]["saved_designs"] == 0


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
    """Webhook for a job already in terminal state returns 200 without re-processing.

    Since 2026-10-13 this guard is also what keeps a job to one charge: the
    reaper (worker/cleanup.py) now meters a stale-killed run that banked
    designs, so a late webhook for a row it already settled must not meter it
    again. Returning here, before the billing block, is the mechanism -- hence
    the assertion that nothing was metered.
    """
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
        patch("webhooks.router.record_gpu_usage") as mock_billing,
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
    # ...and nothing re-metered
    mock_billing.assert_not_called()


async def test_a_webhook_for_a_job_the_reaper_already_failed_is_not_billed_again():
    """The reaper-then-webhook half of one-charge-per-job.

    worker/cleanup.py sweeps a run killed from outside, claims the row, meters
    its capped GPU seconds and mails the customer. The provider's own FAILED
    post can still arrive afterwards -- and Storage holds designs, so the new
    gate would otherwise bill it a second time. The double-processing guard
    (webhooks/router.py:207) returns first, so the second charge never
    happens. The other order is pinned on the reaper side, by
    tests/worker/test_cleanup.py::
    test_a_stale_job_another_route_already_finished_is_not_billed_or_mailed.
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    # The reaper already took this row terminal.
    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "failed"})

    mock_pool = _make_pool(conn1, conn2)

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", new_callable=AsyncMock) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_fail_email,
        patch(
            "webhooks.router.list_job_design_keys",
            return_value=[f"users/user-uuid/jobs/{JOB_ID}/outputs/design_1.pdb"],
        ) as mock_list,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=_failed_payload(),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    mock_billing.assert_not_called()
    mock_update.assert_not_called()
    mock_fail_email.assert_not_called()
    # Not even read: the guard returns above the count.
    mock_list.assert_not_called()


async def test_the_candidate_rows_of_a_complete_run_are_written_after_the_claim():
    """The INSERT runs, and runs below the claim.

    The sibling of
    ``test_a_lost_claim_on_a_complete_payload_writes_no_candidate_rows``: that
    one pins what a loser must not write, this one pins that a winner still
    writes it, and in the order that makes the loser's case reachable. The
    claim records how many connections had been taken when it fired; two is
    the pair of pre-claim reads, so a third would mean the INSERT had already
    run.
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
    conn3 = AsyncMock()   # claim txn: INSERT job_candidates, UPDATE results
    conn4 = AsyncMock()   # SELECT org_stripe_customer
    conn5 = AsyncMock()   # SELECT email
    conn5.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4, conn5)

    acquires_at_claim = []

    async def _claim(*args, **kwargs):
        acquires_at_claim.append(mock_pool.acquire.call_count)
        return True

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    payload = json.dumps({
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "COMPLETED",
        "output": {
            "candidate_count": 2,
            "candidates": [
                {"rank": 1, "pdb_key": "k1.pdb", "scores": {"ipae": 0.3}},
                {"rank": 2, "pdb_key": "k2.pdb", "scores": {}},
            ],
            "next_steps": "Run AlphaFold validation",
            "job_spec": {"tool": "bindcraft"},
        },
    }).encode()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("webhooks.router.update_job_status", side_effect=_claim) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage"),
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=payload,
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200

    # The claim fired on the third connection -- the one it shares with the
    # writes below it -- not on either of the two pre-claim reads.
    assert acquires_at_claim == [3]
    assert mock_update.call_args[1]["conn"] is conn3
    conn3.transaction.assert_called_once()

    # And all three writes the claim authorises landed on it: both candidate
    # rows and the results column.
    assert conn3.execute.await_count == 3
    first = conn3.execute.await_args_list[0][0]
    assert "INSERT INTO public.job_candidates" in first[0]
    assert first[1] == JOB_ID
    assert first[2] == 1
    assert first[3] == "k1.pdb"
    assert json.loads(first[4]) == {"ipae": 0.3}
    assert json.loads(conn3.execute.await_args_list[1][0][4]) == {}
    assert "SET results" in conn3.execute.await_args_list[2][0][0]


async def test_a_lost_claim_on_a_complete_payload_writes_no_candidate_rows():
    """A COMPLETED post that loses the claim leaves job_candidates untouched.

    The reaper can fail a row whose container is, at that moment, posting
    COMPLETED. The handler then returns at the lost-claim guard, and any
    job_candidates rows it had already written would sit on a row that is
    `failed`: get_job reads that table only when the status is 'complete'
    (jobs/router.py:837), so the customer of a run that actually finished
    would be served the partial branch's unscored Storage listing
    (jobs/router.py:852) while the scored rows sat unread.

    Pinned on the connection the claim shares with those writes: it is
    available and the handler does take it, because the claim now runs inside
    its transaction (webhooks/router.py:272-274), but nothing is executed on
    it once the claim comes back False.
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
    conn3 = AsyncMock()   # claim txn -- taken, and left unwritten

    mock_pool = _make_pool(conn1, conn2, conn3)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    payload = json.dumps({
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "COMPLETED",
        "output": {
            "candidate_count": 2,
            "candidates": [
                {"rank": 1, "pdb_key": "k1.pdb", "scores": {"ipae": 0.3}},
                {"rank": 2, "pdb_key": "k2.pdb", "scores": {"ipae": 0.4}},
            ],
            "next_steps": "Run AlphaFold validation",
            "job_spec": {"tool": "bindcraft"},
        },
    }).encode()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "webhooks.router.update_job_status",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock) as mock_email,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=payload,
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["require_non_terminal"] is True

    # The transaction connection was taken, and nothing ran on it: no INSERT,
    # no results write. The two reads above the claim are all that touched the
    # database.
    assert mock_pool.acquire.call_count == 3
    conn1.execute.assert_not_called()
    conn2.execute.assert_not_called()
    conn3.execute.assert_not_called()

    mock_billing.assert_not_called()
    mock_email.assert_not_called()
    mock_publish.assert_not_called()


async def test_a_webhook_that_loses_the_status_claim_is_not_billed_or_mailed():
    """The reaper taking the row mid-handler stops the charge and the mail.

    The other order -- reaper first, webhook after -- is
    ``test_a_webhook_for_a_job_the_reaper_already_failed_is_not_billed_again``,
    which returns at the pre-read guard (webhooks/router.py:207).
    This is the window that guard cannot cover: the row still reads 'running'
    when the handler looks, and the sweep claims it before the handler's own
    UPDATE lands. The claim comes back False and everything that must happen
    once per job -- the cost write, the SSE event, the Storage read, the
    charge, the failure mail -- belongs to the route that won it.

    Pinned on the connection the claim runs on: it is taken, and after the
    claim comes back False nothing is executed on it.
    """
    conn1 = AsyncMock()
    conn1.fetchrow = AsyncMock(return_value={
        "id": JOB_ID,
        "user_id": "user-uuid",
        "started_at": STARTED_AT,
        "runpod_job_id": POD_ID,
        "tool": "bindcraft",
    })

    # The pre-read still sees a running job -- this is the race, not a late
    # duplicate webhook.
    conn2 = AsyncMock()
    conn2.fetchrow = AsyncMock(return_value={"status": "running"})
    conn3 = AsyncMock()   # claim txn -- taken, and left unwritten

    mock_pool = _make_pool(conn1, conn2, conn3)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "webhooks.router.update_job_status",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_fail_email,
        # Storage holds a design, so the charge would fire if the handler got
        # that far: what stops it is losing the claim, not an empty bucket.
        patch(
            "webhooks.router.list_job_design_keys",
            return_value=["users/user-uuid/jobs/" + JOB_ID + "/outputs/design_1.pdb"],
        ) as mock_list,
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

    # The claim was attempted, and attempted as a claim.
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["require_non_terminal"] is True

    # Nothing after it ran.
    mock_billing.assert_not_called()
    mock_fail_email.assert_not_called()
    mock_publish.assert_not_called()
    mock_list.assert_not_called()
    assert mock_pool.acquire.call_count == 3
    conn3.execute.assert_not_called()


async def test_a_failed_candidate_insert_rolls_the_claim_back():
    """A candidate INSERT that raises takes the claim down with it.

    The claim is what makes the row terminal, and the candidate rows are what
    record the run. Written in that order on separate connections, a failure
    part-way through the loop would leave a `complete` row holding some of its
    candidates, a NULL `results`, no mail and no charge -- and the redelivered
    webhook would be dropped by the pre-read guard (webhooks/router.py:207),
    so nothing would ever repair it. Before the claim existed the same failure
    ran above an unconditional status write
    (``git show a479ac38:backend/webhooks/router.py``, the INSERT loop at
    :242-255 above the write at :262) and left the row 'running', where a
    redelivery or the reaper could still settle it.

    What this pins is the structure that restores that property: the claim and
    the INSERT run on one connection, inside one transaction, and the
    exception leaves the transaction block rather than being swallowed. The
    rollback itself is asyncpg's contract for an exception escaping
    ``conn.transaction()``, not something a mock can demonstrate; this test
    does not exercise a database.
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

    # The claim txn connection: the first candidate INSERT fails on it.
    conn3 = AsyncMock()
    conn3.execute = AsyncMock(side_effect=RuntimeError("connection reset"))

    mock_pool = _make_pool(conn1, conn2, conn3)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    payload = json.dumps({
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "COMPLETED",
        "output": {
            "candidate_count": 2,
            "candidates": [
                {"rank": 1, "pdb_key": "k1.pdb", "scores": {"ipae": 0.3}},
                {"rank": 2, "pdb_key": "k2.pdb", "scores": {"ipae": 0.4}},
            ],
            "next_steps": "Run AlphaFold validation",
            "job_spec": {"tool": "bindcraft"},
        },
    }).encode()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "webhooks.router.update_job_status",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock) as mock_email,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(RuntimeError, match="connection reset"):
                await client.post(
                    "/webhooks/runpod",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

    # The claim ran on the same connection as the INSERT that failed, inside
    # the transaction that connection opened.
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["conn"] is conn3
    conn3.transaction.assert_called_once()
    conn3.execute.assert_awaited_once()
    assert "INSERT INTO public.job_candidates" in conn3.execute.await_args[0][0]

    # The exception was handed to the transaction's __aexit__ rather than
    # swallowed inside the block -- that is what asyncpg rolls back on.
    aexit_args = conn3.transaction.return_value.__aexit__.await_args[0]
    assert aexit_args[0] is RuntimeError

    # And nothing downstream of the writes ran: no terminal SSE event, no
    # charge, no mail, and the pod was left for the reaper to cancel.
    mock_publish.assert_not_called()
    mock_billing.assert_not_called()
    mock_email.assert_not_called()
    mock_provider.terminate_pod.assert_not_called()


async def test_a_null_candidates_payload_is_not_iterated():
    """An explicit JSON null must not stop a finished run going terminal.

    The candidate loop runs inside the claim's transaction
    (webhooks/router.py:272-292), so a TypeError there rolls the claim back
    and returns 500 -- and the redelivery that follows raises the same way,
    so the row would stay `running` until the reaper swept it and the
    customer of a run that actually finished would be mailed a failure.

    Pinned on a null rather than a missing key, because that is the only
    input the two spellings disagree on: `output.get("candidates", [])`
    returns None for `{"candidates": null}` and the default never fires.
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
    conn4 = AsyncMock()   # SELECT org_stripe_customer
    conn4.fetchval = AsyncMock(return_value="cus_test")
    conn5 = AsyncMock()   # SELECT email
    conn5.fetchrow = AsyncMock(return_value={"email": "test@example.com"})

    mock_pool = _make_pool(conn1, conn2, conn3, conn4, conn5)

    payload = json.dumps({
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "COMPLETED",
        "output": {
            "candidate_count": 0,
            "candidates": None,
            "next_steps": "",
            "job_spec": {"tool": "bindcraft"},
        },
    }).encode()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "webhooks.router.update_job_status",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("webhooks.router.publish_status", new_callable=AsyncMock),
        patch("webhooks.router.get_provider", return_value=AsyncMock()),
        patch("webhooks.router.record_gpu_usage"),
        patch("webhooks.router.send_completion_email", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=payload,
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200

    # The claim's transaction ran its one write -- the results/cost column --
    # and nothing was inserted for a payload that carried no candidates.
    queries = [c[0][0] for c in conn3.execute.await_args_list]
    assert len(queries) == 1
    assert "SET results" in queries[0]
    assert not any("job_candidates" in q for q in queries)


async def test_a_lost_claim_still_terminates_the_pod():
    """Losing the claim must not leave the pod burning money.

    The claim guard is new on this branch; before it, every webhook that got
    past the pre-read guard terminated the pod, because there was no claim to
    lose. Returning above the termination block would leave a live pod until
    the reaper swept it, and the reaper only sweeps after
    STALE_HEARTBEAT_SECONDS (worker/cleanup.py:52) -- 30 minutes of GPU on a
    run that is already over. The route that did win the transition is not a
    reliable substitute: a cancel skips the provider call outright when the
    tool resolves to no endpoint (jobs/service.py:84).

    The reaper's own sweep does cancel every stale pod it finds, claimed or
    not (worker/cleanup.py:309); this is about the half hour before that.
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

    mock_pool = _make_pool(conn1, conn2, conn3)

    mock_provider = AsyncMock()
    mock_provider.terminate_pod = AsyncMock()

    payload = json.dumps({
        "id": JOB_ID,
        "pod_id": POD_ID,
        "status": "FAILED",
        "error": {"category": "Pipeline error", "message": "boom"},
    }).encode()

    with (
        patch("webhooks.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "webhooks.router.update_job_status",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_update,
        patch("webhooks.router.publish_status", new_callable=AsyncMock) as mock_publish,
        patch("webhooks.router.get_provider", return_value=mock_provider),
        patch("webhooks.router.record_gpu_usage") as mock_billing,
        patch("webhooks.router.send_failure_email", new_callable=AsyncMock) as mock_email,
        patch("webhooks.router.list_job_design_keys") as mock_list,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/runpod",
                content=payload,
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert mock_update.call_args[1]["require_non_terminal"] is True

    # The point of the test: the claim was lost and the pod was still stopped.
    mock_provider.terminate_pod.assert_awaited_once_with(POD_ID)

    # Everything the winner owns stayed with the winner, and the Storage read
    # that decides the charge was never even made.
    mock_publish.assert_not_called()
    mock_billing.assert_not_called()
    mock_email.assert_not_called()
    mock_list.assert_not_called()
