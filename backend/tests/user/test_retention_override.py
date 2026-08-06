"""Tests for PUT /user/retention — per-user retention window override (Plan 10-05).

Covers T-10.05-04 (tampering via retention reduction of other users): the
endpoint uses ``Depends(get_current_user)`` and validates the value server-side
(30 <= n <= 365). The DB CHECK constraint is a second line of defense — we
don't test that here; only the HTTP layer.
"""
import os

os.environ.setdefault("TESTING", "true")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auth.dependencies import get_current_user
from httpx import ASGITransport, AsyncClient
from main import app

# Disable rate limiting — no Redis in test environment.
from middleware.rate_limit import limiter as _limiter

_limiter.enabled = False

USER_ID = "test-user-uuid"


async def _mock_user():
    return USER_ID


def _ctx(conn):
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
# Happy path
# ---------------------------------------------------------------------------


async def test_put_retention_valid_persists_and_returns_200():
    """60 is within [30, 365] — UPDATE runs and the new value is echoed back."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_ctx(conn))

    with patch(
        "user.router.get_db_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/user/retention",
                json={"data_retention_days": 60},
            )

    assert response.status_code == 200
    assert response.json() == {"data_retention_days": 60}
    # Atomic UPDATE with the user_id bound as $1 and the new value as $2.
    assert conn.execute.await_count == 1
    args = conn.execute.await_args.args
    assert "UPDATE public.users" in args[0]
    assert "data_retention_days = $2" in args[0]
    assert args[1] == USER_ID
    assert args[2] == 60


@pytest.mark.parametrize("days", [30, 90, 365])
async def test_put_retention_boundary_values_accepted(days):
    """Boundaries 30 and 365 are inclusive; 90 is the default."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_ctx(conn))

    with patch(
        "user.router.get_db_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/user/retention",
                json={"data_retention_days": days},
            )

    assert response.status_code == 200
    assert response.json() == {"data_retention_days": days}


# ---------------------------------------------------------------------------
# Range validation
# ---------------------------------------------------------------------------


async def test_put_retention_below_30_returns_400():
    """10 is below the 30-day minimum."""
    mock_pool = AsyncMock()  # should never be called — validation fails first

    with patch(
        "user.router.get_db_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/user/retention",
                json={"data_retention_days": 10},
            )

    assert response.status_code == 400
    body = response.json()
    assert "30" in body["detail"] and "365" in body["detail"]


async def test_put_retention_above_365_returns_400():
    """500 is above the 365-day maximum."""
    mock_pool = AsyncMock()

    with patch(
        "user.router.get_db_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/user/retention",
                json={"data_retention_days": 500},
            )

    assert response.status_code == 400


@pytest.mark.parametrize("bad_value", [29, 366, 0, -1])
async def test_put_retention_rejects_out_of_range(bad_value):
    """Each of 29, 366, 0, -1 falls outside [30, 365]."""
    with patch(
        "user.router.get_db_pool",
        new_callable=AsyncMock,
        return_value=AsyncMock(),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/user/retention",
                json={"data_retention_days": bad_value},
            )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 404 missing user
# ---------------------------------------------------------------------------


async def test_put_retention_user_not_found_returns_404():
    """UPDATE 0 rows → 404 User not found."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_ctx(conn))

    with patch(
        "user.router.get_db_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/user/retention",
                json={"data_retention_days": 120},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Unauthenticated
# ---------------------------------------------------------------------------


async def test_put_retention_unauthenticated_returns_401():
    """Remove the auth override — the real get_current_user yields 401."""
    app.dependency_overrides.pop(get_current_user, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/user/retention",
            json={"data_retention_days": 90},
        )

    # Reinstate the override for autouse teardown.
    app.dependency_overrides[get_current_user] = _mock_user

    assert response.status_code == 401
