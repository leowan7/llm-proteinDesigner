"""Tests for GDPR Article 17 deletion endpoints + race-guarded hard-delete executor.

Covers:
- POST /user/delete-account — wrong phrase → 400
- POST /user/delete-account — correct phrase → 200, deletion_scheduled_for ISO 30 days out
- POST /user/delete-account — writes audit_log row (T-10.04-07)
- POST /user/delete-account — second call when already pending → 409
- POST /user/delete-account — unauthenticated → 401
- POST /user/cancel-deletion — set → 200, UPDATE issued
- POST /user/cancel-deletion — not set → 404
- POST /user/cancel-deletion — user missing → 404
- execute_hard_delete — race guard aborts when deletion_requested_at is NULL (T-10.04-04)
"""
import os
os.environ.setdefault("TESTING", "true")

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from auth.dependencies import get_current_user
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False


USER_ID = "test-user-uuid"
NOW = datetime.datetime(2026, 4, 23, 12, 0, 0, tzinfo=datetime.timezone.utc)


async def _mock_user():
    return USER_ID


def _make_ctx(conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = _mock_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# POST /user/delete-account
# ---------------------------------------------------------------------------

async def test_delete_account_wrong_phrase_returns_400_and_writes_audit_log():
    """POST /user/delete-account with wrong phrase returns 400. Audit log still written."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock()  # should not be reached

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/user/delete-account",
                json={"confirmation_phrase": "delete my account"},  # lowercase, wrong
            )

    assert response.status_code == 400
    assert "Confirmation phrase does not match" in response.text
    # Audit log INSERT ran BEFORE the phrase check — this is T-10.04-07 behavior.
    assert conn.execute.await_count >= 1
    audit_sql = conn.execute.await_args_list[0].args[0]
    assert "audit_log" in audit_sql
    assert "user_deletion_requested" in audit_sql


async def test_delete_account_correct_phrase_returns_scheduled_timestamp():
    """POST /user/delete-account with exact phrase returns deletion_scheduled_for 30 days out."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "email": "user@example.com",
        "deletion_requested_at": NOW,
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.router.send_deletion_scheduled_email") as _mock_email:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/user/delete-account",
                json={"confirmation_phrase": "DELETE MY ACCOUNT"},
            )

    assert response.status_code == 200, response.text
    data = response.json()
    assert "deletion_scheduled_for" in data
    # 30 days out from NOW = 2026-05-23.
    expected = (NOW + datetime.timedelta(days=30)).isoformat()
    assert data["deletion_scheduled_for"] == expected


async def test_delete_account_audit_log_written_before_phrase_check():
    """T-10.04-07 — audit_log INSERT runs before phrase validation (happy path too)."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "email": "user@example.com",
        "deletion_requested_at": NOW,
    })

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.router.send_deletion_scheduled_email"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/user/delete-account",
                json={"confirmation_phrase": "DELETE MY ACCOUNT"},
            )

    assert response.status_code == 200
    # First execute is always the audit_log INSERT.
    first_sql = conn.execute.await_args_list[0].args[0]
    assert "audit_log" in first_sql
    assert "user_deletion_requested" in first_sql


async def test_delete_account_second_call_returns_409():
    """POST /user/delete-account when deletion_requested_at already set returns 409."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    # fetchrow is called twice: once by the conditional UPDATE (returns None —
    # 0 rows updated because deletion_requested_at is NOT NULL), and once by the
    # disambiguation SELECT (returns a row with an existing deletion_requested_at).
    conn.fetchrow = AsyncMock(side_effect=[
        None,  # conditional UPDATE returned 0 rows
        {"deletion_requested_at": NOW - datetime.timedelta(days=1)},  # already pending
    ])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/user/delete-account",
                json={"confirmation_phrase": "DELETE MY ACCOUNT"},
            )

    assert response.status_code == 409
    assert "Deletion already pending" in response.text


async def test_delete_account_missing_user_returns_404():
    """POST /user/delete-account when user row does not exist returns 404."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[None, None])  # UPDATE matched 0; SELECT also None

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/user/delete-account",
                json={"confirmation_phrase": "DELETE MY ACCOUNT"},
            )

    assert response.status_code == 404


async def test_delete_account_unauthenticated_returns_401():
    """POST /user/delete-account without auth returns 401."""
    app.dependency_overrides.pop(get_current_user, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/user/delete-account",
            json={"confirmation_phrase": "DELETE MY ACCOUNT"},
        )
    app.dependency_overrides[get_current_user] = _mock_user
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /user/cancel-deletion
# ---------------------------------------------------------------------------

async def test_cancel_deletion_clears_column():
    """WR-01: POST /user/cancel-deletion runs a single atomic conditional UPDATE.

    The conditional UPDATE ``WHERE id = $1 AND deletion_requested_at IS NOT NULL
    RETURNING id`` returns a row on success. On success an audit_log INSERT is
    also issued for symmetry with the deletion-request audit trail.
    """
    conn = AsyncMock()
    # First fetchrow is the conditional UPDATE — returns RETURNING id on success.
    conn.fetchrow = AsyncMock(return_value={"id": USER_ID})
    conn.execute = AsyncMock()

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/cancel-deletion")

    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    # Conditional UPDATE ran (single fetchrow).
    assert conn.fetchrow.await_count == 1
    update_sql = conn.fetchrow.await_args.args[0]
    assert "deletion_requested_at = NULL" in update_sql
    assert "deletion_requested_at IS NOT NULL" in update_sql
    # Symmetric audit_log row written on cancel.
    assert conn.execute.await_count == 1
    audit_sql = conn.execute.await_args.args[0]
    assert "audit_log" in audit_sql
    assert "user_deletion_cancelled" in audit_sql


async def test_cancel_deletion_when_none_pending_returns_404():
    """POST /user/cancel-deletion returns 404 when no deletion is pending.

    Conditional UPDATE returns None (no rows matched), disambiguation SELECT
    finds the row but with deletion_requested_at IS NULL.
    """
    conn = AsyncMock()
    # fetchrow 1: conditional UPDATE → None; fetchrow 2: SELECT → row exists.
    conn.fetchrow = AsyncMock(side_effect=[None, {"deletion_requested_at": None}])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/cancel-deletion")

    assert response.status_code == 404
    assert "No pending deletion" in response.text


async def test_cancel_deletion_missing_user_returns_404():
    """POST /user/cancel-deletion returns 404 when user row is missing entirely.

    Conditional UPDATE returns None, disambiguation SELECT also None.
    """
    conn = AsyncMock()
    # fetchrow 1: UPDATE → None; fetchrow 2: SELECT → None.
    conn.fetchrow = AsyncMock(side_effect=[None, None])

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(conn))

    with patch("user.router.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/user/cancel-deletion")

    assert response.status_code == 404
    assert "User not found" in response.text


# ---------------------------------------------------------------------------
# execute_hard_delete — race guard (T-10.04-04)
# ---------------------------------------------------------------------------

async def test_execute_hard_delete_aborts_when_deletion_requested_at_cleared():
    """T-10.04-04: execute_hard_delete must abort if the user cancelled between
    cron fetch and executor. No R2, Stripe, or Supabase admin calls allowed.
    """
    from user.deletion import execute_hard_delete

    # Guard row returns NULL for deletion_requested_at — caller already cancelled.
    guard_conn = AsyncMock()
    guard_conn.fetchrow = AsyncMock(return_value={"deletion_requested_at": None})
    guard_conn.transaction = MagicMock(return_value=_make_ctx(guard_conn))  # nested ctx

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(guard_conn))

    with patch("user.deletion.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.deletion.list_and_delete_user_objects") as mock_r2, \
         patch("user.deletion.stripe") as mock_stripe, \
         patch("user.deletion.delete_auth_user") as mock_auth_delete, \
         patch("user.deletion.send_deletion_completed_email") as mock_email:
        await execute_hard_delete(USER_ID, "user@example.com", "cus_abc")

    # No destructive call made.
    mock_r2.assert_not_called()
    mock_stripe.Customer.delete.assert_not_called()
    mock_auth_delete.assert_not_called()
    mock_email.assert_not_called()


async def test_execute_hard_delete_missing_row_aborts():
    """Row missing entirely — execute_hard_delete must also abort, not crash."""
    from user.deletion import execute_hard_delete

    guard_conn = AsyncMock()
    guard_conn.fetchrow = AsyncMock(return_value=None)
    guard_conn.transaction = MagicMock(return_value=_make_ctx(guard_conn))

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(guard_conn))

    with patch("user.deletion.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.deletion.list_and_delete_user_objects") as mock_r2, \
         patch("user.deletion.stripe") as mock_stripe, \
         patch("user.deletion.delete_auth_user") as mock_auth_delete:
        await execute_hard_delete(USER_ID, "user@example.com", "cus_abc")

    mock_r2.assert_not_called()
    mock_stripe.Customer.delete.assert_not_called()
    mock_auth_delete.assert_not_called()


async def test_execute_hard_delete_happy_path_runs_r2_stripe_auth():
    """With deletion_requested_at set, execute_hard_delete runs R2 → Stripe → auth delete."""
    from user.deletion import execute_hard_delete

    guard_conn = AsyncMock()
    guard_conn.fetchrow = AsyncMock(return_value={"deletion_requested_at": NOW - datetime.timedelta(days=31)})
    guard_conn.transaction = MagicMock(return_value=_make_ctx(guard_conn))

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(guard_conn))

    with patch("user.deletion.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.deletion.list_and_delete_user_objects", return_value=5) as mock_r2, \
         patch("user.deletion.stripe") as mock_stripe, \
         patch("user.deletion.delete_auth_user") as mock_auth_delete, \
         patch("user.deletion.send_deletion_completed_email", new_callable=AsyncMock) as mock_email:
        await execute_hard_delete(USER_ID, "user@example.com", "cus_abc")

    mock_r2.assert_called_once_with(USER_ID)
    mock_stripe.Customer.delete.assert_called_once_with("cus_abc")
    mock_auth_delete.assert_called_once_with(USER_ID)
    mock_email.assert_awaited_once()


async def test_execute_hard_delete_stripe_failure_does_not_block_auth_delete():
    """Stripe failure must NOT abort the flow — invoice-retention on Stripe's
    side cannot hold the DB row hostage."""
    from user.deletion import execute_hard_delete

    guard_conn = AsyncMock()
    guard_conn.fetchrow = AsyncMock(return_value={"deletion_requested_at": NOW - datetime.timedelta(days=31)})
    guard_conn.transaction = MagicMock(return_value=_make_ctx(guard_conn))

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(guard_conn))

    with patch("user.deletion.get_db_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("user.deletion.list_and_delete_user_objects", return_value=0), \
         patch("user.deletion.stripe") as mock_stripe, \
         patch("user.deletion.delete_auth_user") as mock_auth_delete, \
         patch("user.deletion.send_deletion_completed_email", new_callable=AsyncMock):
        mock_stripe.Customer.delete.side_effect = RuntimeError("Stripe blocked")
        await execute_hard_delete(USER_ID, "user@example.com", "cus_abc")

    # Auth delete still ran despite Stripe failure.
    mock_auth_delete.assert_called_once_with(USER_ID)
