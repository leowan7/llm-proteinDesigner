"""Unit tests for invitation create / accept / preview endpoints.

Covers:

- Owner can create an invitation; row inserted, email dispatched
- Scientist + viewer cannot create invitations (403)
- Accept with email-match -> 200 + membership insert + accept stamp
- Accept with mismatched email -> 409
- Accept with expired token -> 410
- Accept with revoked token -> 410
- Double-click idempotency on accept (ON CONFLICT DO NOTHING +
  WHERE accepted_at IS NULL)
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


os.environ.setdefault("TESTING", "true")


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# App + pool helpers (active-org overridable)
# ---------------------------------------------------------------------------


def _build_app(user_id: str = "user-owner", active_role: str | None = "owner", active_org_id: str = "org-1"):
    """Build a minimal FastAPI app with org + invitations routers and overrides."""
    from fastapi import FastAPI

    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from organizations.router import router as orgs_router, invitations_router

    app = FastAPI()
    app.include_router(orgs_router)
    app.include_router(invitations_router)

    async def _user():
        return user_id

    app.dependency_overrides[get_current_user] = _user

    if active_role is not None:
        async def _active():
            return (active_org_id, active_role)
        app.dependency_overrides[get_active_org] = _active

    return app


def _make_invite_pool(
    invite_row=None,
    org_row=None,
    user_row=None,
    membership_execute_result="INSERT 0 1",
    accept_execute_result="UPDATE 1",
):
    """Build a pool that responds to the create-invite + accept-invite flows."""
    fetchrow_responses = [invite_row, org_row, user_row]
    execute_results = [membership_execute_result, accept_execute_result]
    captured = {"execute_calls": [], "fetchrow_calls": [], "fetchval_calls": []}

    async def _execute(query, *args):
        captured["execute_calls"].append((query, args))
        if execute_results:
            return execute_results.pop(0)
        return "OK"

    async def _fetchrow(query, *args):
        captured["fetchrow_calls"].append((query, args))
        if fetchrow_responses:
            return fetchrow_responses.pop(0)
        return None

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchrow = _fetchrow

    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, captured


# ---------------------------------------------------------------------------
# Create invitation
# ---------------------------------------------------------------------------


async def test_invite_creates_row_and_sends_email():
    """Owner -> 201 + DB insert + send_invitation_email called."""
    from httpx import ASGITransport, AsyncClient

    invite_id = uuid.uuid4()
    pool, captured = _make_invite_pool(
        invite_row={"id": invite_id},
        org_row={"name": "Acme Bio"},
        user_row={"email": "owner@example.com"},
    )

    app = _build_app(active_role="owner")
    sent_emails = []

    async def _capture_email(**kwargs):
        sent_emails.append(kwargs)

    with patch("organizations.router.get_db_pool", return_value=pool), \
         patch("organizations.router.notifications.send_invitation_email", _capture_email):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/invitations",
                json={"email": "invitee@example.com", "role": "scientist"},
                headers={"X-Org-Id": "org-1"},
            )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "invitee@example.com"
    assert body["role"] == "scientist"
    # Email was dispatched
    assert sent_emails, "Expected send_invitation_email to be called"
    email = sent_emails[0]
    assert email["to_email"] == "invitee@example.com"
    assert email["organization_name"] == "Acme Bio"
    assert "/invitations/accept?token=" in email["accept_url"]
    # The token in the URL should be 32+ chars
    token_param = email["accept_url"].split("token=")[1]
    assert len(token_param) >= 32


async def test_invite_as_scientist_returns_403():
    """Scientist cannot invite -> 403."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_invite_pool()
    app = _build_app(active_role="scientist")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/invitations",
                json={"email": "x@y.com", "role": "viewer"},
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 403


async def test_invite_as_viewer_returns_403():
    """Viewer cannot invite -> 403."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_invite_pool()
    app = _build_app(active_role="viewer")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/organizations/org-1/invitations",
                json={"email": "x@y.com", "role": "viewer"},
                headers={"X-Org-Id": "org-1"},
            )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Accept invitation
# ---------------------------------------------------------------------------


def _accept_pool(invite_row, user_email_row=None, execute_log=None):
    """Pool tuned for the accept_invitation flow.

    Router queries users.email first, then service.accept_invitation queries
    the invitation row, then runs two execute() calls (INSERT membership +
    UPDATE invitation).
    """
    if execute_log is None:
        execute_log = []
    fetchrow_responses = [user_email_row, invite_row]

    async def _execute(query, *args):
        execute_log.append((query, args))
        return "OK"

    async def _fetchrow(query, *args):
        if fetchrow_responses:
            return fetchrow_responses.pop(0)
        return None

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchrow = _fetchrow

    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, execute_log


async def test_accept_with_matching_email_inserts_membership():
    """Accept happy path: email matches -> 200 + membership insert + accept stamp."""
    from httpx import ASGITransport, AsyncClient

    org_id = uuid.uuid4()
    invite_id = uuid.uuid4()
    invite_row = {
        "id": invite_id,
        "organization_id": org_id,
        "email": "invitee@example.com",
        "role": "scientist",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=3),
        "accepted_at": None,
        "revoked_at": None,
    }
    pool, exec_log = _accept_pool(
        invite_row=invite_row,
        user_email_row={"email": "invitee@example.com"},
    )

    app = _build_app(user_id="user-invitee", active_role=None)
    valid_token = "x" * 43
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/invitations/accept", json={"token": valid_token})

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["organization_id"] == str(org_id)
    assert data["role"] == "scientist"
    # Confirm both writes ran
    insert_membership = [c for c in exec_log if "organization_memberships" in c[0]]
    accept_stamp = [c for c in exec_log if "organization_invitations" in c[0] and "accepted_at" in c[0]]
    assert insert_membership, f"Expected membership INSERT; got: {exec_log}"
    assert accept_stamp, f"Expected accepted_at UPDATE; got: {exec_log}"
    # Idempotency guards present
    assert "ON CONFLICT (organization_id, user_id) DO NOTHING" in insert_membership[0][0]
    assert "accepted_at IS NULL" in accept_stamp[0][0]


async def test_accept_with_mismatched_email_returns_409():
    """Invitation for foo@x, caller is bar@x -> 409."""
    from httpx import ASGITransport, AsyncClient

    invite_row = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "email": "foo@example.com",
        "role": "scientist",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=3),
        "accepted_at": None,
        "revoked_at": None,
    }
    pool, _ = _accept_pool(
        invite_row=invite_row,
        user_email_row={"email": "bar@example.com"},
    )

    app = _build_app(user_id="user-bar", active_role=None)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/invitations/accept", json={"token": "x" * 43})

    assert r.status_code == 409
    assert "foo@example.com" in r.json()["detail"]


async def test_accept_with_expired_token_returns_410():
    """Invitation past expires_at -> 410."""
    from httpx import ASGITransport, AsyncClient

    invite_row = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "email": "invitee@example.com",
        "role": "scientist",
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
        "accepted_at": None,
        "revoked_at": None,
    }
    pool, _ = _accept_pool(
        invite_row=invite_row,
        user_email_row={"email": "invitee@example.com"},
    )

    app = _build_app(user_id="user-invitee", active_role=None)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/invitations/accept", json={"token": "x" * 43})

    assert r.status_code == 410
    assert "expired" in r.json()["detail"].lower()


async def test_accept_with_revoked_token_returns_410():
    """Invitation with revoked_at set -> 410."""
    from httpx import ASGITransport, AsyncClient

    invite_row = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "email": "invitee@example.com",
        "role": "scientist",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=3),
        "accepted_at": None,
        "revoked_at": datetime.now(timezone.utc),
    }
    pool, _ = _accept_pool(
        invite_row=invite_row,
        user_email_row={"email": "invitee@example.com"},
    )

    app = _build_app(user_id="user-invitee", active_role=None)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/invitations/accept", json={"token": "x" * 43})

    assert r.status_code == 410
    assert "revoke" in r.json()["detail"].lower()


async def test_accept_idempotent_on_double_click():
    """Second accept call with same token returns the same payload (no error).

    Backed by ON CONFLICT (organization_id, user_id) DO NOTHING on the
    membership insert + WHERE accepted_at IS NULL on the accept stamp update.
    """
    from httpx import ASGITransport, AsyncClient

    org_id = uuid.uuid4()
    invite_row = {
        "id": uuid.uuid4(),
        "organization_id": org_id,
        "email": "invitee@example.com",
        "role": "scientist",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=3),
        "accepted_at": None,
        "revoked_at": None,
    }

    # First call: fresh state. Second call: row exists but ON CONFLICT
    # returns "INSERT 0 0"; UPDATE WHERE accepted_at IS NULL returns "UPDATE 0".
    # Both should still produce 200 with the same payload from the service.
    async def _run(execute_results: list[str]) -> int:
        captured = list(execute_results)

        async def _execute(query, *args):
            return captured.pop(0) if captured else "OK"

        async def _fetchrow(query, *args):
            if "users" in query.lower() and "email" in query.lower() and "id = $1" in query.lower():
                return {"email": "invitee@example.com"}
            return invite_row

        conn = AsyncMock()
        conn.execute = _execute
        conn.fetchrow = _fetchrow
        txn = AsyncMock()
        txn.__aenter__ = AsyncMock(return_value=None)
        txn.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=txn)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = AsyncMock()
        pool.acquire = MagicMock(return_value=ctx)

        app = _build_app(user_id="user-invitee", active_role=None)
        with patch("organizations.router.get_db_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post("/invitations/accept", json={"token": "x" * 43})
        return r.status_code, r.json()

    # First click
    sc1, body1 = await _run(["INSERT 0 1", "UPDATE 1"])
    # Second click — membership already exists, accept already stamped
    sc2, body2 = await _run(["INSERT 0 0", "UPDATE 0"])

    assert sc1 == 200 == sc2
    assert body1 == body2 == {"organization_id": str(org_id), "role": "scientist"}
