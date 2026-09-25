"""Parameterized permission matrix tests (RESEARCH §5.1).

Each row asserts that a given role hitting a given endpoint produces the
expected HTTP status. Mocks the active org via dependency_overrides so the
test focuses purely on the role check, not the underlying DB call.

Endpoints not yet wired by Plan 12-02 (e.g. POST /jobs/launch with
require_role("owner","scientist")) are intentionally marked xfail — they're
wired in Plan 12-03.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "true")


# organization_memberships.organization_id is uuid, so a fake org id has
# to be one too: auth/org_dependencies.py rejects a non-uuid X-Org-Id
# with 400 before any query runs.
ORG_1 = "11111111-1111-4111-8111-111111111111"
ORG_2 = "22222222-2222-4222-8222-222222222222"

pytestmark = pytest.mark.asyncio


def _build_app(active_role: str | None, user_id: str = "user-test", org_id: str = ORG_1):
    """Build an isolated FastAPI app with the org + invitations routers and
    the auth + active-org dependencies overridden.
    """
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
            return (org_id, active_role)
        app.dependency_overrides[get_active_org] = _active

    return app


def _generic_pool(caller_role: str = "owner"):
    """A pool that returns generic happy-path responses for any query.

    ``caller_role`` is what the membership lookup returns, which is where
    require_path_role now reads the caller's role from: the role is a property
    of the org in the PATH, not of the X-Org-Id header.
    """
    new_id = uuid.uuid4()

    async def _execute(query, *args):
        return "OK"

    async def _fetchval(query, *args):
        # create_invitation asks whether the org is personal before it inserts.
        # This fake's happy path is a team org, matching the is_personal: False
        # the organizations fetchrow below already returns.
        if "is_personal" in query:
            return False
        return new_id

    async def _fetchrow(query, *args):
        if "organization_invitations" in query and "RETURNING id" in query:
            return {"id": new_id}
        if "organizations" in query and "WHERE id" in query:
            return {"name": "Test Org", "is_personal": False, "id": new_id}
        if "users" in query and "email" in query:
            return {"email": "owner@example.com"}
        if "organization_memberships" in query:
            return {"role": caller_role}
        return None

    async def _fetch(query, *args):
        return []

    conn = AsyncMock()
    conn.execute = _execute
    conn.fetchval = _fetchval
    conn.fetchrow = _fetchrow
    conn.fetch = _fetch

    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


@pytest.mark.parametrize(
    "role,method,path,body,expected",
    [
        # Invitations — owner-only writes
        ("owner", "POST", f"/organizations/{ORG_1}/invitations",
         {"email": "x@y.com", "role": "viewer"}, 201),
        ("scientist", "POST", f"/organizations/{ORG_1}/invitations",
         {"email": "x@y.com", "role": "viewer"}, 403),
        ("viewer", "POST", f"/organizations/{ORG_1}/invitations",
         {"email": "x@y.com", "role": "viewer"}, 403),
        # Transfer ownership — owner-only. Generic pool returns a membership
        # row for the target so the happy path returns 200.
        ("owner", "POST", f"/organizations/{ORG_1}/members/transfer",
         {"target_user_id": "stranger", "new_self_role": "scientist"}, 200),
        ("scientist", "POST", f"/organizations/{ORG_1}/members/transfer",
         {"target_user_id": "stranger", "new_self_role": "scientist"}, 403),
        ("viewer", "POST", f"/organizations/{ORG_1}/members/transfer",
         {"target_user_id": "stranger", "new_self_role": "scientist"}, 403),
        # PATCH org name — owner-only
        ("owner", "PATCH", f"/organizations/{ORG_1}",
         {"name": "Renamed"}, 200),
        ("scientist", "PATCH", f"/organizations/{ORG_1}",
         {"name": "Renamed"}, 403),
        ("viewer", "PATCH", f"/organizations/{ORG_1}",
         {"name": "Renamed"}, 403),
        # List members — any role
        ("owner", "GET", f"/organizations/{ORG_1}/members", None, 200),
        ("scientist", "GET", f"/organizations/{ORG_1}/members", None, 200),
        ("viewer", "GET", f"/organizations/{ORG_1}/members", None, 200),
    ],
)
async def test_permission_matrix(role, method, path, body, expected):
    """Per-row assertion: role X hitting endpoint Y -> status Z."""
    from httpx import ASGITransport, AsyncClient

    pool = _generic_pool(caller_role=role)
    app = _build_app(active_role=role)

    async def _capture_email(**kwargs):
        return None

    with patch("organizations.router.get_db_pool", return_value=pool), \
         patch("organizations.router.notifications.send_invitation_email", _capture_email):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Org-Id": ORG_1}
            if method == "GET":
                r = await client.get(path, headers=headers)
            elif method == "POST":
                r = await client.post(path, json=body, headers=headers)
            elif method == "PATCH":
                r = await client.patch(path, json=body, headers=headers)
            elif method == "DELETE":
                r = await client.request("DELETE", path, headers=headers)
            else:
                raise ValueError(f"Unknown method: {method}")

    assert r.status_code == expected, (
        f"{role} {method} {path} expected {expected}, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# Jobs/Billing role gating — wired in Plan 12-03 (was an xfail placeholder
# in 12-02; 12-03 flipped it to a real passing matrix).
# ---------------------------------------------------------------------------


def _build_jobs_app(active_role: str, user_id: str = "user-rl", org_id: str = "org-rl"):
    """Build an isolated FastAPI app with the jobs + billing routers mounted.

    Mirrors `_build_app` above but for the routes whose require_role gates
    Plan 12-03 wired up. Skips rate limiting so the matrix runs deterministically.
    """
    from auth.dependencies import get_current_user
    from auth.org_dependencies import get_active_org
    from billing.router import router as billing_router
    from fastapi import FastAPI
    from jobs.router import router as jobs_router
    from middleware.rate_limit import limiter as _limiter

    _limiter.enabled = False
    app = FastAPI()
    app.state.limiter = _limiter
    app.include_router(jobs_router)
    app.include_router(billing_router)

    async def _user():
        return user_id

    async def _active():
        return (org_id, active_role)

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_active_org] = _active
    return app


@pytest.mark.parametrize(
    "role,expected",
    [
        ("owner", 200),     # any-role list endpoint
        ("scientist", 200),
        ("viewer", 200),    # viewers CAN list (read-only)
    ],
)
async def test_list_jobs_role_matrix(role, expected):
    """Plan 12-03: GET /jobs requires any membership role."""
    from httpx import ASGITransport, AsyncClient

    pool = _generic_pool()
    app = _build_jobs_app(active_role=role)
    with patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/jobs/", headers={"X-Org-Id": "org-rl"})
    assert r.status_code == expected, r.text


@pytest.mark.parametrize(
    "role,expected",
    [
        # /billing/payment-status is the cheapest billing endpoint to exercise:
        # no body, no Stripe call (settings.stripe_secret_key empty in tests).
        ("owner", 200),
        ("scientist", 403),  # require_role("owner") rejects
        ("viewer", 403),
    ],
)
async def test_billing_endpoints_owner_only(role, expected):
    """Plan 12-03: every /billing/* endpoint depends on require_role('owner').

    Uses payment-status as the representative endpoint — same require_role
    dep is on checkout-session, portal-session, payment-method.
    """
    from httpx import ASGITransport, AsyncClient

    pool = _generic_pool()
    app = _build_jobs_app(active_role=role)
    with patch("billing.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/billing/payment-status", headers={"X-Org-Id": "org-rl"})
    assert r.status_code == expected, (
        f"{role} GET /billing/payment-status expected {expected}, got {r.status_code}: {r.text}"
    )


@pytest.mark.parametrize(
    "role,expected",
    [
        ("owner", 404),     # passes role gate, fails at "no running job" — proves gate let it through
        ("scientist", 404),
        ("viewer", 403),    # viewer rejected by require_role
    ],
)
async def test_cancel_job_blocks_viewer(role, expected):
    """Plan 12-03: POST /jobs/{id}/cancel depends on require_role('owner','scientist').

    Owners + scientists pass the role gate and hit the org-scope SQL lookup,
    which returns no row (404). Viewers are blocked at the role gate (403).
    """
    from httpx import ASGITransport, AsyncClient

    pool = _generic_pool()
    app = _build_jobs_app(active_role=role)
    with patch("jobs.router.get_db_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/jobs/some-uuid/cancel", headers={"X-Org-Id": "org-rl"},
            )
    assert r.status_code == expected, (
        f"{role} POST /jobs/.../cancel expected {expected}, got {r.status_code}: {r.text}"
    )


def _scoped_pool(memberships: dict[str, str]):
    """Pool whose membership lookup answers per organization_id.

    require_path_role passes the org id from the URL path, so a caller who owns
    ORG_1 gets no row back when the path names ORG_2.
    """
    async def _fetchrow(query, *args):
        if "organization_memberships" in query and "role::text" in query:
            role = memberships.get(str(args[0]))
            return {"role": role} if role else None
        if "organizations" in query and "WHERE id" in query:
            return {"name": "Victim Org", "is_personal": False, "id": uuid.uuid4()}
        if "organization_memberships" in query:
            return {"role": "scientist"}
        return None

    async def _execute(query, *args):
        return "UPDATE 1"

    async def _fetchval(query, *args):
        return uuid.uuid4()

    async def _fetch(query, *args):
        return []

    conn = AsyncMock()
    conn.fetchrow = _fetchrow
    conn.execute = _execute
    conn.fetchval = _fetchval
    conn.fetch = _fetch

    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("PATCH", f"/organizations/{ORG_2}", {"name": "Owned"}),
        ("DELETE", f"/organizations/{ORG_2}", None),
        ("PATCH", f"/organizations/{ORG_2}/members/victim-id", {"role": "viewer"}),
        ("POST", f"/organizations/{ORG_2}/members/transfer",
         {"target_user_id": "accomplice", "new_self_role": "scientist"}),
        ("POST", f"/organizations/{ORG_2}/invitations",
         {"email": "x@y.com", "role": "owner"}),
        ("DELETE", f"/organizations/{ORG_2}/invitations/"
                   "99999999-9999-4999-8999-999999999999", None),
    ],
)
async def test_owner_of_one_org_cannot_write_to_another(method, path, body):
    """Role is read from the org in the PATH, not from the X-Org-Id header.

    The caller really is the owner of ORG_1, so the header check passes. If the
    role gate reads the header's org instead of the path's, every route here
    writes to an org the caller has no membership in.
    """
    from httpx import ASGITransport, AsyncClient

    pool = _scoped_pool({ORG_1: "owner"})
    app = _build_app(active_role="owner", org_id=ORG_1)

    async def _capture_email(**kwargs):
        return None

    with patch("organizations.router.get_db_pool", return_value=pool),          patch("organizations.router.notifications.send_invitation_email", _capture_email):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(method, path, json=body, headers={"X-Org-Id": ORG_1})

    assert r.status_code == 403, f"{method} {path} -> {r.status_code}: {r.text}"
    assert "Not a member" in r.json()["detail"]
