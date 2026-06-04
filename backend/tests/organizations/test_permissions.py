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


pytestmark = pytest.mark.asyncio


def _build_app(active_role: str | None, user_id: str = "user-test", org_id: str = "org-1"):
    """Build an isolated FastAPI app with the org + invitations routers and
    the auth + active-org dependencies overridden.
    """
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
            return (org_id, active_role)
        app.dependency_overrides[get_active_org] = _active

    return app


def _generic_pool():
    """A pool that returns generic happy-path responses for any query."""
    new_id = uuid.uuid4()

    async def _execute(query, *args):
        return "OK"

    async def _fetchval(query, *args):
        return new_id

    async def _fetchrow(query, *args):
        if "organization_invitations" in query and "RETURNING id" in query:
            return {"id": new_id}
        if "organizations" in query and "WHERE id" in query:
            return {"name": "Test Org", "is_personal": False, "id": new_id}
        if "users" in query and "email" in query:
            return {"email": "owner@example.com"}
        if "organization_memberships" in query:
            return {"role": "scientist"}
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
        ("owner", "POST", "/organizations/org-1/invitations",
         {"email": "x@y.com", "role": "viewer"}, 201),
        ("scientist", "POST", "/organizations/org-1/invitations",
         {"email": "x@y.com", "role": "viewer"}, 403),
        ("viewer", "POST", "/organizations/org-1/invitations",
         {"email": "x@y.com", "role": "viewer"}, 403),
        # Transfer ownership — owner-only. Generic pool returns a membership
        # row for the target so the happy path returns 200.
        ("owner", "POST", "/organizations/org-1/members/transfer",
         {"target_user_id": "stranger", "new_self_role": "scientist"}, 200),
        ("scientist", "POST", "/organizations/org-1/members/transfer",
         {"target_user_id": "stranger", "new_self_role": "scientist"}, 403),
        ("viewer", "POST", "/organizations/org-1/members/transfer",
         {"target_user_id": "stranger", "new_self_role": "scientist"}, 403),
        # PATCH org name — owner-only
        ("owner", "PATCH", "/organizations/org-1",
         {"name": "Renamed"}, 200),
        ("scientist", "PATCH", "/organizations/org-1",
         {"name": "Renamed"}, 403),
        ("viewer", "PATCH", "/organizations/org-1",
         {"name": "Renamed"}, 403),
        # List members — any role
        ("owner", "GET", "/organizations/org-1/members", None, 200),
        ("scientist", "GET", "/organizations/org-1/members", None, 200),
        ("viewer", "GET", "/organizations/org-1/members", None, 200),
    ],
)
async def test_permission_matrix(role, method, path, body, expected):
    """Per-row assertion: role X hitting endpoint Y -> status Z."""
    from httpx import ASGITransport, AsyncClient

    pool = _generic_pool()
    app = _build_app(active_role=role)

    async def _capture_email(**kwargs):
        return None

    with patch("organizations.router.get_db_pool", return_value=pool), \
         patch("organizations.router.notifications.send_invitation_email", _capture_email):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Org-Id": "org-1"}
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
# Jobs/Billing role gating — wired in Plan 12-03, not 12-02
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="Plan 12-03 wires require_role on /jobs/launch and /billing/*; "
           "permission matrix for those rows is covered there.",
    strict=False,
)
async def test_scientist_can_launch_job_xfail():
    """Placeholder for 12-03: scientist + owner can launch; viewer cannot."""
    assert False  # not yet implemented
