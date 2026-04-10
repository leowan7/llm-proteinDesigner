"""Tests for session CRUD API endpoints (/sessions/*).

Covers all 6 endpoints:
- GET  /sessions                         — list sessions
- POST /sessions                         — create a new session
- GET  /sessions/{session_id}            — get session with messages
- PUT  /sessions/{session_id}            — update session title
- DELETE /sessions/{session_id}          — delete session + messages
- POST /sessions/{session_id}/generate-title — generate AI title

Uses FastAPI dependency_overrides to bypass get_current_user auth and patches
the sessions.queries functions since sessions router delegates to those functions
rather than using get_db_pool directly.
"""
import os
os.environ.setdefault("TESTING", "true")

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from auth.dependencies import get_current_user
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_ID = "test-user-uuid"
SESSION_ID = "sess-1111-2222-3333"
NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _mock_user():
    """FastAPI dependency override: returns a fixed user_id string."""
    return USER_ID


def _session_row():
    """Return a dict mimicking a sessions DB row."""
    return {
        "id": SESSION_ID,
        "title": "Test Session",
        "created_at": NOW,
        "updated_at": NOW,
        "agent_history": None,
        "messages": [],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def override_auth():
    """Override get_current_user for every test in this module."""
    app.dependency_overrides[get_current_user] = _mock_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# GET /sessions — list
# ---------------------------------------------------------------------------

async def test_list_sessions_empty():
    """GET /sessions returns 200 with empty sessions list when DB has no rows."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/sessions")

    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert data["sessions"] == []


async def test_list_sessions_returns_rows():
    """GET /sessions returns 200 with sessions when DB has rows."""
    row = {"id": SESSION_ID, "title": "My Session", "created_at": NOW, "updated_at": NOW}
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[row])
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["id"] == SESSION_ID


# ---------------------------------------------------------------------------
# POST /sessions — create
# ---------------------------------------------------------------------------

async def test_create_session():
    """POST /sessions returns 201 with the new session data."""
    new_row = {"id": SESSION_ID, "title": None, "created_at": NOW, "updated_at": NOW}
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=new_row)
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/sessions")

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == SESSION_ID


# ---------------------------------------------------------------------------
# GET /sessions/{session_id} — get with messages
# ---------------------------------------------------------------------------

async def test_get_session():
    """GET /sessions/{id} returns 200 with session and messages list."""
    session_row = {
        "id": SESSION_ID,
        "title": "Test",
        "agent_history": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        # get_session_with_messages calls pool.fetchrow then pool.fetch
        mock_pool.fetchrow = AsyncMock(return_value=session_row)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/sessions/{SESSION_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == SESSION_ID
    assert "messages" in data


async def test_get_session_not_found():
    """GET /sessions/{id} returns 404 when session does not exist."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/sessions/{SESSION_ID}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /sessions/{session_id} — update title
# ---------------------------------------------------------------------------

async def test_update_session_title():
    """PUT /sessions/{id} returns 200 with updated title when session exists."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        # update_session_title calls pool.execute returning "UPDATE 1"
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                f"/sessions/{SESSION_ID}",
                json={"title": "New Title"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    assert data["session_id"] == SESSION_ID


async def test_update_session_title_not_found():
    """PUT /sessions/{id} returns 404 when session does not exist."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                f"/sessions/{SESSION_ID}",
                json={"title": "Ghost Title"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /sessions/{session_id}
# ---------------------------------------------------------------------------

async def test_delete_session():
    """DELETE /sessions/{id} returns 204 when session is deleted successfully."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/sessions/{SESSION_ID}")

    assert response.status_code == 204


async def test_delete_session_not_found():
    """DELETE /sessions/{id} returns 404 when session does not exist."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/sessions/{SESSION_ID}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/generate-title
# ---------------------------------------------------------------------------

async def test_generate_title():
    """POST /sessions/{id}/generate-title returns 200 with AI-generated title."""
    session_row = {
        "id": SESSION_ID,
        "title": "Old Title",
        "agent_history": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    # session_messages includes one user message for the title generator to use
    message_row = {
        "id": "msg-1",
        "role": "user",
        "content": "Design a binder for IL-6",
        "cards": None,
        "sort_order": 0,
    }

    mock_text_block = MagicMock()
    mock_text_block.text = "IL-6 Binder Design"
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]

    mock_client_instance = MagicMock()
    mock_client_instance.messages.create = MagicMock(return_value=mock_response)

    with (
        patch("sessions.queries.get_db_pool") as mock_get_pool,
        patch("sessions.router.anthropic.Anthropic", return_value=mock_client_instance),
    ):
        mock_pool = AsyncMock()
        # get_session_with_messages calls fetchrow then fetch
        mock_pool.fetchrow = AsyncMock(return_value=session_row)
        mock_pool.fetch = AsyncMock(return_value=[message_row])
        # update_session_title calls execute
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/sessions/{SESSION_ID}/generate-title")

    assert response.status_code == 200
    data = response.json()
    assert "title" in data
    assert data["title"] == "IL-6 Binder Design"


async def test_generate_title_no_messages():
    """POST /sessions/{id}/generate-title returns {title: null} when no messages."""
    session_row = {
        "id": SESSION_ID,
        "title": None,
        "agent_history": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=session_row)
        mock_pool.fetch = AsyncMock(return_value=[])  # no messages
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/sessions/{SESSION_ID}/generate-title")

    assert response.status_code == 200
    assert response.json()["title"] is None


async def test_generate_title_session_not_found():
    """POST /sessions/{id}/generate-title returns 404 when session missing."""
    with patch("sessions.queries.get_db_pool") as mock_get_pool:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_get_pool.return_value = mock_pool

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/sessions/{SESSION_ID}/generate-title")

    assert response.status_code == 404
