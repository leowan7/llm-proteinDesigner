"""Unit tests for backend.auth.org_dependencies (get_active_org + require_role).

These tests exercise the dependency callables directly with mocked asyncpg
pools -- no FastAPI app, no real DB. The point is to lock down the contract:

- Missing ``X-Org-Id`` header -> the caller's personal org, as ``owner``
- Non-member -> HTTP 403
- Member -> returns ``(org_id, role)`` tuple
- ``require_role(...)`` -> 403 on insufficient role; returns ``org_id`` otherwise
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# organization_memberships.organization_id is uuid, so a fake org id has
# to be one too: auth/org_dependencies.py rejects a non-uuid X-Org-Id
# with 400 before any query runs.
ORG_1 = "11111111-1111-4111-8111-111111111111"
ORG_2 = "22222222-2222-4222-8222-222222222222"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchrow_return=None, fetchval_return=None):
    """Build an asyncpg-like pool whose acquire() connection returns these values.

    ``pool.conn`` is exposed (not part of the asyncpg API) so a test can assert
    on the SQL the dependency actually emitted.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetchval = AsyncMock(return_value=fetchval_return)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    pool.conn = conn
    return pool


# ---------------------------------------------------------------------------
# get_active_org
# ---------------------------------------------------------------------------


async def test_missing_header_falls_back_to_personal_org():
    """No X-Org-Id header -> the caller's personal org, returned as owner.

    This is the flag-off path. With organizations_enabled false the orgs router
    is not mounted (main.py), so a client has no endpoint to learn an org id
    from and sends no header; a 400 here would answer 400 on every /jobs,
    /billing and /user/usage request for every existing customer.
    """
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchval_return="org-personal-uuid")
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id=None, user_id="user-abc")

    assert result == ("org-personal-uuid", "owner")
    # Resolved through the find-or-create function, so a user signed up by an
    # old replica mid-rolling-deploy gets an org instead of a NULL org_id.
    assert "public.personal_org_for" in pool.conn.fetchval.call_args.args[0]


async def test_non_member_returns_403():
    """Header set but caller has no membership row -> HTTPException 403."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return=None)
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        with pytest.raises(HTTPException) as exc_info:
            await get_active_org(x_org_id=ORG_1, user_id="user-abc")
    assert exc_info.value.status_code == 403
    assert "member" in exc_info.value.detail.lower()


async def test_owner_returns_role():
    """Owner membership returns (org_id, 'owner')."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return={"role": "owner"})
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id=ORG_1, user_id="user-abc")
    assert result == (ORG_1, "owner")


async def test_scientist_returns_role():
    """Scientist membership returns (org_id, 'scientist')."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return={"role": "scientist"})
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id=ORG_2, user_id="user-abc")
    assert result == (ORG_2, "scientist")


async def test_viewer_returns_role():
    """Viewer membership returns (org_id, 'viewer')."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return={"role": "viewer"})
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id=ORG_2, user_id="user-abc")
    assert result == (ORG_2, "viewer")


# ---------------------------------------------------------------------------
# require_role(...) factory
# ---------------------------------------------------------------------------


async def test_require_role_owner_allows_owner():
    """require_role('owner') passes when active role is 'owner'."""
    from auth.org_dependencies import require_role

    dep = require_role("owner")
    org_id = await dep(active=(ORG_1, "owner"))
    assert org_id == ORG_1


async def test_require_role_owner_rejects_scientist():
    """require_role('owner') rejects scientist with 403."""
    from auth.org_dependencies import require_role

    dep = require_role("owner")
    with pytest.raises(HTTPException) as exc_info:
        await dep(active=(ORG_1, "scientist"))
    assert exc_info.value.status_code == 403
    assert "owner" in exc_info.value.detail


async def test_require_role_owner_rejects_viewer():
    """require_role('owner') rejects viewer with 403."""
    from auth.org_dependencies import require_role

    dep = require_role("owner")
    with pytest.raises(HTTPException) as exc_info:
        await dep(active=(ORG_1, "viewer"))
    assert exc_info.value.status_code == 403


async def test_require_role_owner_scientist_allows_both():
    """require_role('owner', 'scientist') permits both owner and scientist."""
    from auth.org_dependencies import require_role

    dep = require_role("owner", "scientist")
    assert await dep(active=(ORG_1, "owner")) == ORG_1
    assert await dep(active=(ORG_1, "scientist")) == ORG_1


async def test_require_role_owner_scientist_rejects_viewer():
    """require_role('owner', 'scientist') rejects viewer with 403."""
    from auth.org_dependencies import require_role

    dep = require_role("owner", "scientist")
    with pytest.raises(HTTPException) as exc_info:
        await dep(active=(ORG_1, "viewer"))
    assert exc_info.value.status_code == 403
