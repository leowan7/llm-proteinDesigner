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
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

os.environ.setdefault("TESTING", "true")


# organization_memberships.organization_id is uuid, so a fake org id has
# to be one too: auth/org_dependencies.py rejects a non-uuid X-Org-Id
# with 400 before any query runs.
ORG_1 = "11111111-1111-4111-8111-111111111111"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# App + pool helpers (active-org overridable)
# ---------------------------------------------------------------------------


def _build_app(user_id: str = "user-owner", active_role: str | None = "owner", active_org_id: str = ORG_1):
    """Build a minimal FastAPI app with org + invitations routers and overrides."""
    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from fastapi import FastAPI
    from organizations.router import invitations_router
    from organizations.router import router as orgs_router

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
    caller_role="owner",
    insert_raises=None,
    is_personal=False,
):
    """Build a pool that responds to the create-invite + accept-invite flows."""
    fetchrow_responses = [invite_row, org_row, user_row]
    execute_results = [membership_execute_result, accept_execute_result]
    captured = {
        "execute_calls": [], "fetchrow_calls": [], "fetchval_calls": [],
        # execute and fetchrow land in separate lists, so an index into one
        # says nothing about ordering against the other. This is the single
        # ordered log of every statement the handler issued.
        "order": [],
    }

    async def _execute(query, *args):
        captured["execute_calls"].append((query, args))
        captured["order"].append(query)
        if execute_results:
            return execute_results.pop(0)
        return "OK"

    async def _fetchrow(query, *args):
        captured["fetchrow_calls"].append((query, args))
        captured["order"].append(query)
        # require_path_role reads the caller's role straight from the DB before
        # the handler body runs; answer it out of band so it does not consume a
        # queued response meant for the handler.
        if "organization_memberships" in query and "role::text" in query:
            return {"role": caller_role} if caller_role else None
        if insert_raises is not None and "INSERT INTO public.organization_invitations" in query:
            raise insert_raises
        if fetchrow_responses:
            return fetchrow_responses.pop(0)
        return None

    async def _fetchval(query, *args):
        captured["fetchval_calls"].append((query, args))
        captured["order"].append(query)
        if "is_personal" in query:
            return is_personal
        return None

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchrow = _fetchrow
    conn.fetchval = _fetchval

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
                f"/organizations/{ORG_1}/invitations",
                json={"email": "invitee@example.com", "role": "scientist"},
                headers={"X-Org-Id": ORG_1},
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

    pool, _ = _make_invite_pool(caller_role="scientist")
    app = _build_app(active_role="scientist")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/organizations/{ORG_1}/invitations",
                json={"email": "x@y.com", "role": "viewer"},
                headers={"X-Org-Id": ORG_1},
            )
    assert r.status_code == 403


async def test_invite_as_viewer_returns_403():
    """Viewer cannot invite -> 403."""
    from httpx import ASGITransport, AsyncClient

    pool, _ = _make_invite_pool(caller_role="viewer")
    app = _build_app(active_role="viewer")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/organizations/{ORG_1}/invitations",
                json={"email": "x@y.com", "role": "viewer"},
                headers={"X-Org-Id": ORG_1},
            )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Accept invitation
# ---------------------------------------------------------------------------


def _accept_pool(invite_row, user_email_row=None, execute_log=None, still_member=True):
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

    async def _fetchval(query, *args):
        # accept_invitation's "are they still a member?" probe on a used token.
        return still_member

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchrow = _fetchrow
    conn.fetchval = _fetchval

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
        "expires_at": datetime.now(UTC) + timedelta(days=3),
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
        "expires_at": datetime.now(UTC) + timedelta(days=3),
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
        "expires_at": datetime.now(UTC) - timedelta(days=1),
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
        "expires_at": datetime.now(UTC) + timedelta(days=3),
        "accepted_at": None,
        "revoked_at": datetime.now(UTC),
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
        "expires_at": datetime.now(UTC) + timedelta(days=3),
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


async def _accept_used_token(still_member: bool):
    """POST /invitations/accept with an already-stamped invitation."""
    from httpx import ASGITransport, AsyncClient

    invite_row = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "email": "invitee@example.com",
        "role": "scientist",
        "expires_at": datetime.now(UTC) + timedelta(days=3),
        "accepted_at": datetime.now(UTC) - timedelta(seconds=1),
        "revoked_at": None,
    }
    pool, _ = _accept_pool(
        invite_row=invite_row,
        user_email_row={"email": "invitee@example.com"},
        still_member=still_member,
    )
    app = _build_app(user_id="user-invitee", active_role=None)
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/invitations/accept", json={"token": "x" * 43})


async def test_accept_used_token_after_removal_returns_410():
    """A removed member cannot re-enter with the old invitation link.

    accepted_at alone cannot decide this: the membership INSERT is ON CONFLICT
    DO NOTHING, so replaying a used token would silently re-add someone the
    owner removed. The membership probe is what tells the two apart.
    """
    r = await _accept_used_token(still_member=False)
    assert r.status_code == 410, r.text
    assert "already been used" in r.json()["detail"]


async def test_accept_used_token_while_still_member_succeeds():
    """Double-click: the second request lands after accepted_at is stamped."""
    r = await _accept_used_token(still_member=True)
    assert r.status_code == 200, r.text


async def test_invite_duplicate_pending_returns_409():
    """The one-pending-per-address index surfaces as 409, not 500.

    organization_invitations_one_pending (migration 20260605000003) is a
    partial UNIQUE index, so a second live invite to the same address raises
    SQLSTATE 23505 out of the INSERT.
    """
    from httpx import ASGITransport, AsyncClient

    assert asyncpg.exceptions.UniqueViolationError.sqlstate == "23505"
    pool, _ = _make_invite_pool(
        insert_raises=asyncpg.exceptions.UniqueViolationError(
            "duplicate key value violates unique constraint "
            '"organization_invitations_one_pending"'
        ),
    )
    app = _build_app(active_role="owner")
    with patch("organizations.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/organizations/{ORG_1}/invitations",
                json={"email": "dupe@example.com", "role": "viewer"},
                headers={"X-Org-Id": ORG_1},
            )

    assert r.status_code == 409, r.text
    assert "pending invitation" in r.json()["detail"]


async def test_invite_retires_an_expired_pending_invitation_first():
    """An expired invitation must not lock the address out of a re-invite.

    organization_invitations_one_pending keys on (accepted_at IS NULL AND
    revoked_at IS NULL) and cannot include expires_at > now(), because now() is
    not IMMUTABLE and a partial index predicate has to be. So an expired row
    still occupies the slot, and without the sweep the owner's obvious next
    move -- send it again -- would 409 forever on an invitation nobody can
    accept. The SQL itself is exercised against a real database in
    backend/tests/integration/test_flag_off_rolling_window.py.
    """
    from httpx import ASGITransport, AsyncClient

    pool, captured = _make_invite_pool(
        invite_row={"id": uuid.uuid4()},
        org_row={"name": "Acme Bio"},
        user_row={"email": "owner@example.com"},
    )

    app = _build_app(active_role="owner")
    with patch("organizations.router.get_db_pool", return_value=pool), \
         patch("organizations.router.notifications.send_invitation_email", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/organizations/{ORG_1}/invitations",
                json={"email": "Returning@Example.com", "role": "viewer"},
                headers={"X-Org-Id": ORG_1},
            )

    assert r.status_code == 201, r.text
    sweeps = [
        (q, a) for q, a in captured["execute_calls"]
        if "organization_invitations" in q and "revoked_at = now()" in q
    ]
    assert len(sweeps) == 1, captured["execute_calls"]
    query, args = sweeps[0]
    # Only dead rows, and only this address in this org.
    assert "expires_at <= now()" in query
    assert "accepted_at IS NULL" in query
    assert "revoked_at IS NULL" in query
    assert "lower(email) = lower($2)" in query
    # Pydantic EmailStr lower-cases the DOMAIN but keeps the local part's case,
    # which is why the sweep and the index both compare lower(email) rather than
    # relying on the stored value being normalised.
    assert args == (ORG_1, "Returning@example.com")
    # It has to run BEFORE the INSERT, or the INSERT still hits the index and
    # 409s -- the sweep would be inert while every assertion above still
    # passed. captured["order"] is the one ordered log of both methods.
    def _first(needle):
        return next(
            (i for i, q in enumerate(captured["order"]) if needle in q), -1,
        )

    sweep_at = _first("revoked_at = now()")
    insert_at = _first("INSERT INTO public.organization_invitations")
    assert sweep_at >= 0 and insert_at >= 0, captured["order"]
    assert sweep_at < insert_at, captured["order"]


async def test_invite_into_a_personal_org_is_refused():
    """A personal workspace cannot have a second member.

    guard_personal_org_single_member (migration 20260605000003 section 7)
    rejects the membership INSERT, so without this check the owner gets a 201
    and an email goes out, and the failure surfaces as a 500 for the invitee
    when they click accept. protect_last_owner's user-gone tolerance is only
    sound because a personal org has exactly one member.
    """
    from httpx import ASGITransport, AsyncClient

    pool, captured = _make_invite_pool(
        invite_row={"id": uuid.uuid4()},
        org_row={"name": "owner (Personal)"},
        user_row={"email": "owner@example.com"},
        is_personal=True,
    )

    app = _build_app(active_role="owner")
    sent = []
    with patch("organizations.router.get_db_pool", return_value=pool), \
         patch("organizations.router.notifications.send_invitation_email",
               AsyncMock(side_effect=lambda **kw: sent.append(kw))):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/organizations/{ORG_1}/invitations",
                json={"email": "colleague@example.com", "role": "scientist"},
                headers={"X-Org-Id": ORG_1},
            )

    assert r.status_code == 400, r.text
    assert "personal workspace" in r.json()["detail"].lower()
    assert sent == [], "no invitation may be emailed for an org that cannot accept it"
    assert not any(
        "INSERT INTO public.organization_invitations" in q
        for q in captured["order"]
    ), captured["order"]
