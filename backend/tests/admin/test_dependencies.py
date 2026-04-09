"""Tests for admin auth dependency — get_current_admin.

Verifies:
- Non-admin users receive 403 Forbidden (not 404, not "Not admin")
- Admin users pass through and return their user_id
- Missing user rows (not found in DB) also receive 403
"""
import os
os.environ.setdefault("TESTING", "true")

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from admin.dependencies import get_current_admin


def _make_ctx(conn):
    """Wrap a mock connection in an async context manager compatible with 'async with'.

    Args:
        conn: The mock connection to wrap.

    Returns:
        AsyncMock configured as an async context manager.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_pool(fetchrow_return_value):
    """Build an asyncpg pool mock where acquire().fetchrow returns the given value.

    Args:
        fetchrow_return_value: The value that conn.fetchrow will return
            (e.g. {"is_admin": True}, {"is_admin": False}, or None).

    Returns:
        AsyncMock simulating an asyncpg pool with proper context manager support.
    """
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return_value)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_make_ctx(mock_conn))

    return mock_pool


async def test_non_admin_gets_403(monkeypatch):
    """Non-admin user (is_admin=False) must receive 403 Forbidden.

    The response detail must be "Forbidden" — not "Not admin" or "Admin required"
    to avoid revealing the admin surface exists (D-04).
    """
    mock_pool = _make_pool({"is_admin": False})

    async def mock_get_db_pool():
        return mock_pool

    monkeypatch.setattr("admin.dependencies.get_db_pool", mock_get_db_pool)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(user_id="user-123")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


async def test_admin_user_returns_user_id(monkeypatch):
    """Admin user (is_admin=True) passes through and the user_id is returned."""
    mock_pool = _make_pool({"is_admin": True})

    async def mock_get_db_pool():
        return mock_pool

    monkeypatch.setattr("admin.dependencies.get_db_pool", mock_get_db_pool)

    result = await get_current_admin(user_id="admin-456")

    assert result == "admin-456"


async def test_user_not_found_gets_403(monkeypatch):
    """User not found in DB (fetchrow returns None) must receive 403 Forbidden.

    Same response as non-admin — does not reveal whether the user exists
    or whether the admin route exists.
    """
    mock_pool = _make_pool(None)

    async def mock_get_db_pool():
        return mock_pool

    monkeypatch.setattr("admin.dependencies.get_db_pool", mock_get_db_pool)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(user_id="nonexistent")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
