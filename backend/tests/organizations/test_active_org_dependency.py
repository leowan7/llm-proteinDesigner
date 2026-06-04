"""Unit tests for backend.auth.org_dependencies (get_active_org + require_role).

These tests exercise the dependency callables directly with mocked asyncpg
pools -- no FastAPI app, no real DB. The point is to lock down the contract:

- Missing ``X-Org-Id`` header -> HTTP 400
- Non-member -> HTTP 403
- Member -> returns ``(org_id, role)`` tuple
- ``require_role(...)`` -> 403 on insufficient role; returns ``org_id`` otherwise
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchrow_return):
    """Build an asyncpg-like pool whose acquire().fetchrow returns the given value."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


# ---------------------------------------------------------------------------
# get_active_org
# ---------------------------------------------------------------------------


async def test_missing_header_returns_400():
    """No X-Org-Id header -> HTTPException 400."""
    from auth.org_dependencies import get_active_org

    with pytest.raises(HTTPException) as exc_info:
        await get_active_org(x_org_id=None, user_id="user-abc")
    assert exc_info.value.status_code == 400
    assert "X-Org-Id" in exc_info.value.detail


async def test_non_member_returns_403():
    """Header set but caller has no membership row -> HTTPException 403."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return=None)
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        with pytest.raises(HTTPException) as exc_info:
            await get_active_org(x_org_id="org-uuid", user_id="user-abc")
    assert exc_info.value.status_code == 403
    assert "member" in exc_info.value.detail.lower()


async def test_owner_returns_role():
    """Owner membership returns (org_id, 'owner')."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return={"role": "owner"})
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id="org-uuid", user_id="user-abc")
    assert result == ("org-uuid", "owner")


async def test_scientist_returns_role():
    """Scientist membership returns (org_id, 'scientist')."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return={"role": "scientist"})
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id="org-xyz", user_id="user-abc")
    assert result == ("org-xyz", "scientist")


async def test_viewer_returns_role():
    """Viewer membership returns (org_id, 'viewer')."""
    from auth.org_dependencies import get_active_org

    pool = _make_pool(fetchrow_return={"role": "viewer"})
    with patch("auth.org_dependencies.get_db_pool", return_value=pool):
        result = await get_active_org(x_org_id="org-xyz", user_id="user-abc")
    assert result == ("org-xyz", "viewer")


# ---------------------------------------------------------------------------
# require_role(...) factory
# ---------------------------------------------------------------------------


async def test_require_role_owner_allows_owner():
    """require_role('owner') passes when active role is 'owner'."""
    from auth.org_dependencies import require_role

    dep = require_role("owner")
    org_id = await dep(active=("org-1", "owner"))
    assert org_id == "org-1"


async def test_require_role_owner_rejects_scientist():
    """require_role('owner') rejects scientist with 403."""
    from auth.org_dependencies import require_role

    dep = require_role("owner")
    with pytest.raises(HTTPException) as exc_info:
        await dep(active=("org-1", "scientist"))
    assert exc_info.value.status_code == 403
    assert "owner" in exc_info.value.detail


async def test_require_role_owner_rejects_viewer():
    """require_role('owner') rejects viewer with 403."""
    from auth.org_dependencies import require_role

    dep = require_role("owner")
    with pytest.raises(HTTPException) as exc_info:
        await dep(active=("org-1", "viewer"))
    assert exc_info.value.status_code == 403


async def test_require_role_owner_scientist_allows_both():
    """require_role('owner', 'scientist') permits both owner and scientist."""
    from auth.org_dependencies import require_role

    dep = require_role("owner", "scientist")
    assert await dep(active=("org-1", "owner")) == "org-1"
    assert await dep(active=("org-1", "scientist")) == "org-1"


async def test_require_role_owner_scientist_rejects_viewer():
    """require_role('owner', 'scientist') rejects viewer with 403."""
    from auth.org_dependencies import require_role

    dep = require_role("owner", "scientist")
    with pytest.raises(HTTPException) as exc_info:
        await dep(active=("org-1", "viewer"))
    assert exc_info.value.status_code == 403
