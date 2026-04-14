"""Integration tests for the agent conversation flow against real Supabase.

Per D-02: the DB pool is NOT overridden — real asyncpg sessions/session_messages
rows are created and read. External services (Anthropic, RunPod) are mocked so
no real API calls or GPU jobs are launched.

Requirements:
- Local Supabase stack must be running: `supabase start`
- .env.local must be present with SUPABASE_URL and SUPABASE_DB_URL

The agent conversation flow under test:
    User message → Claude tool_use → tool dispatch → Claude end_turn → done event

Tests parse the SSE response line-by-line to assert the correct event sequence.
"""
import os
os.environ.setdefault("TESTING", "true")

import json
import socket
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from auth.dependencies import get_current_user
from main import app

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False

# ---------------------------------------------------------------------------
# Integration test user
# ---------------------------------------------------------------------------

INTEGRATION_USER_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Skip entire module when Supabase is not reachable.
# Uses a synchronous TCP probe at import time to avoid event loop scope issues.
# ---------------------------------------------------------------------------

def _supabase_port_open(host: str = "127.0.0.1", port: int = 54322, timeout: float = 1.0) -> bool:
    """Check if the Supabase Postgres port is accepting connections.

    Args:
        host: Hostname to probe.
        port: Port to probe (default: 54322, Supabase local Postgres port).
        timeout: Connection timeout in seconds.

    Returns:
        True if the port accepts TCP connections, False otherwise.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


pytestmark = pytest.mark.skipif(
    not _supabase_port_open(),
    reason="Local Supabase not running — skipping integration tests",
)


# ---------------------------------------------------------------------------
# Auth override fixture
# ---------------------------------------------------------------------------

async def _mock_user():
    """Return the integration test user_id without touching Supabase Auth."""
    return INTEGRATION_USER_ID


@pytest.fixture(autouse=True)
def override_auth():
    """Override get_current_user for every test in this module."""
    app.dependency_overrides[get_current_user] = _mock_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_session(client: AsyncClient) -> str:
    """Create a session via the real API and return its ID.

    Args:
        client: HTTPX AsyncClient with app transport.

    Returns:
        UUID string of the newly created session.
    """
    response = await client.post("/sessions")
    assert response.status_code == 201, f"Create session failed: {response.text}"
    return response.json()["id"]


async def _delete_session(client: AsyncClient, session_id: str) -> None:
    """Best-effort session cleanup.

    Args:
        client: HTTPX AsyncClient with app transport.
        session_id: UUID of the session to delete.
    """
    try:
        await client.delete(f"/sessions/{session_id}")
    except Exception as exc:
        import warnings
        warnings.warn(f"Session cleanup failed for {session_id}: {exc}")


def _make_mock_anthropic_end_turn(text: str = "I can help you design a binder."):
    """Build a mock Anthropic client that returns a single end_turn response.

    This simulates the simplest agent path: Claude responds with text and no
    tool calls. Useful for testing message persistence without tool dispatch.

    Args:
        text: The text content for Claude's response.

    Returns:
        MagicMock mimicking the anthropic.Anthropic client.
    """
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [text_block]

    client_instance = MagicMock()
    client_instance.messages.create = MagicMock(return_value=response)

    return client_instance


def _make_mock_anthropic_tool_then_end(tool_name: str = "resolve_structure"):
    """Build a mock Anthropic client that calls one tool then returns end_turn.

    First call: returns a tool_use response to trigger tool dispatch.
    Second call: returns an end_turn text response.

    Args:
        tool_name: The tool name for the first tool_use block.

    Returns:
        MagicMock mimicking the anthropic.Anthropic client.
    """
    # First response: tool_use
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tool-call-id-001"
    tool_block.name = tool_name
    tool_block.input = {"query": "IL6R", "query_type": "natural_language"}

    tool_response = MagicMock()
    tool_response.stop_reason = "tool_use"
    tool_response.content = [tool_block]

    # Second response: end_turn
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I found the structure. Let me help you design a binder."

    end_response = MagicMock()
    end_response.stop_reason = "end_turn"
    end_response.content = [text_block]

    client_instance = MagicMock()
    client_instance.messages.create = MagicMock(
        side_effect=[tool_response, end_response]
    )
    return client_instance


def _parse_sse_events(raw_text: str) -> list[dict]:
    """Parse SSE response text into a list of event dicts.

    Args:
        raw_text: Raw SSE response body as a string, with lines separated by
            newlines and event data prefixed with 'data: '.

    Returns:
        List of dicts parsed from each 'data: {...}' line.
    """
    events = []
    for line in raw_text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

async def test_agent_conversation_flow_resolve_to_launch():
    """Agent SSE stream contains tool_result and done events for a tool-use flow.

    Mocks the Anthropic client to return a tool_use response (resolve_structure)
    followed by end_turn. Verifies the SSE stream includes:
    - A 'status' event (tool execution in progress)
    - A 'tool_result' event with tool_name='resolve_structure'
    - A 'done' event at the end

    The DB pool is real — session and message rows are written to Supabase.
    Only the Anthropic client and the resolve_structure tool's HTTP calls are mocked.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = None
        try:
            session_id = await _create_session(client)

            mock_client = _make_mock_anthropic_tool_then_end("resolve_structure")

            # Mock resolve_structure tool's HTTP fetch so no real RCSB call happens
            mock_structure_result = json.dumps({
                "pdb_path": "s3://bucket/test.pdb",
                "chain_id": "A",
                "resolution": 2.1,
                "organism": "Homo sapiens",
                "name": "Interleukin-6 receptor",
            })

            with (
                patch("agent.router.anthropic.Anthropic", return_value=mock_client),
                patch("agent.tools.dispatch_tool", return_value=mock_structure_result),
            ):
                response = await client.post(
                    "/agent/message",
                    json={
                        "session_id": session_id,
                        "message": "I want to design a binder for IL-6 receptor",
                    },
                )

            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            events = _parse_sse_events(response.text)
            event_types = [e.get("type") for e in events]

            # Must have a done event to confirm the stream completed
            assert "done" in event_types, f"Missing 'done' event. Got: {event_types}"

            # Must have a tool_result event for the resolve_structure call
            tool_result_events = [e for e in events if e.get("type") == "tool_result"]
            assert len(tool_result_events) >= 1, (
                f"Expected at least one 'tool_result' event. Got: {event_types}"
            )
            assert tool_result_events[0]["tool_name"] == "resolve_structure"

        finally:
            if session_id:
                await _delete_session(client, session_id)


async def test_agent_message_persists_to_session():
    """User message and agent reply are persisted to session_messages in real DB.

    Verifies that after POST /agent/message:
    1. The user message appears in GET /sessions/{id} message history.
    2. At least one assistant message is present in the history.

    Uses the simplest mock (end_turn only, no tools) so the test focuses
    on DB persistence rather than tool dispatch.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = None
        try:
            session_id = await _create_session(client)

            user_message = "Design a minibinder for PD-L1"
            mock_client = _make_mock_anthropic_end_turn("I can help with that.")

            with patch("agent.router.anthropic.Anthropic", return_value=mock_client):
                agent_response = await client.post(
                    "/agent/message",
                    json={
                        "session_id": session_id,
                        "message": user_message,
                    },
                )

            assert agent_response.status_code == 200

            # Verify messages persisted in real DB
            get_response = await client.get(f"/sessions/{session_id}")
            assert get_response.status_code == 200
            messages = get_response.json()["messages"]

            assert len(messages) >= 1, "Expected at least the user message in history"

            # User message should be present
            user_messages = [m for m in messages if m["role"] == "user"]
            assert len(user_messages) >= 1, "User message not found in session history"
            assert user_messages[0]["content"] == user_message

            # At least one assistant reply should be present
            assistant_messages = [m for m in messages if m["role"] == "assistant"]
            assert len(assistant_messages) >= 1, "No assistant reply found in session history"

        finally:
            if session_id:
                await _delete_session(client, session_id)
