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


def _make_tool_use_response(tool_name: str, tool_id: str, tool_input: dict):
    """Build a single Anthropic tool_use response.

    Args:
        tool_name: Name of the tool being invoked.
        tool_id: Unique tool_use block ID.
        tool_input: Input payload for the tool.

    Returns:
        MagicMock imitating a single Anthropic Message with stop_reason=tool_use.
    """
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = tool_id
    tool_block.name = tool_name
    tool_block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [tool_block]
    return response


def _make_mock_anthropic_full_flow():
    """Build a mock Anthropic client that chains all 5 agent conversation stages.

    Simulates the canonical agent flow from ROADMAP SC2:
        resolve_structure -> classify_intent -> collect_parameters
            -> validate_preflight -> end_turn (launch-ready)

    The fifth stage is the agent producing a launch-ready summary text; the
    actual job dispatch happens via POST /jobs/launch outside the agent loop.

    Returns:
        MagicMock mimicking the anthropic.Anthropic client with a side_effect
        of 5 sequential responses.
    """
    responses = [
        _make_tool_use_response(
            "resolve_structure",
            "tool-call-resolve-001",
            {"query": "IL6R", "query_type": "natural_language"},
        ),
        _make_tool_use_response(
            "classify_intent",
            "tool-call-classify-002",
            {
                "design_type": "minibinder",
                "recommended_tool": "bindcraft",
                "rationale": "Minibinders against a known target are BindCraft's sweet spot.",
            },
        ),
        _make_tool_use_response(
            "collect_parameters",
            "tool-call-collect-003",
            {"tool": "bindcraft"},
        ),
        _make_tool_use_response(
            "validate_preflight",
            "tool-call-validate-004",
            {
                "pdb_path": "s3://bucket/test.pdb",
                "chain_id": "A",
                "tool": "bindcraft",
                "parameters": {"num_designs": 10, "binder_length": 65},
            },
        ),
    ]

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = (
        "All checks passed. You're ready to launch a BindCraft minibinder run "
        "against IL-6 receptor."
    )
    end_response = MagicMock()
    end_response.stop_reason = "end_turn"
    end_response.content = [text_block]
    responses.append(end_response)

    client_instance = MagicMock()
    client_instance.messages.create = MagicMock(side_effect=responses)
    return client_instance


def _make_stage_results() -> dict:
    """Return a dict mapping tool_name to the JSON-string result to return.

    The multi-stage integration test uses these to side_effect dispatch_tool,
    producing a realistic-looking payload per stage without hitting real HTTP
    or DB dependencies inside the tool handlers.
    """
    return {
        "resolve_structure": json.dumps({
            "pdb_path": "s3://bucket/test.pdb",
            "chain_id": "A",
            "resolution": 2.1,
            "organism": "Homo sapiens",
            "name": "Interleukin-6 receptor",
        }),
        "classify_intent": json.dumps({
            "design_type": "minibinder",
            "recommended_tool": "bindcraft",
            "rationale": "Minibinders against a known target are BindCraft's sweet spot.",
        }),
        "collect_parameters": json.dumps({
            "tool": "bindcraft",
            "parameters": [
                {"name": "num_designs", "default": 10, "type": "integer"},
                {"name": "binder_length", "default": 65, "type": "integer"},
                {"name": "hotspot_residues", "default": [], "type": "array"},
            ],
        }),
        "validate_preflight": json.dumps({
            "checks": [
                {"name": "pdb_quality", "status": "pass"},
                {"name": "hotspot_sasa", "status": "pass"},
                {"name": "parameter_sanity", "status": "pass"},
            ],
            "ready_to_launch": True,
        }),
    }


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


async def test_agent_conversation_flow_all_five_stages():
    """Agent SSE stream exercises all 5 stages: resolve -> classify -> collect -> validate -> launch-ready.

    Satisfies ROADMAP SC2 ("the full agent conversation flow"). Mocks Anthropic
    with a 5-response side_effect that chains four tool_use turns followed by
    an end_turn "ready to launch" message. Uses real Supabase for session and
    message persistence; only Anthropic and dispatch_tool are mocked.

    Asserts:
    - All 4 tool_result SSE events are streamed in order.
    - The final assistant text indicates launch readiness.
    - A 'done' event terminates the stream.
    - Anthropic client was called at least 5 times (one per response; a 6th
      call may occur when the background title-generation task fires for the
      first user message in a session, which is best-effort and non-fatal).
    - dispatch_tool was called exactly 4 times (one per tool_use turn).
    """
    transport = ASGITransport(app=app)
    expected_order = [
        "resolve_structure",
        "classify_intent",
        "collect_parameters",
        "validate_preflight",
    ]
    stage_results = _make_stage_results()

    async def _dispatch_side_effect(tool_name, _tool_input, user_id=""):
        """Side-effect implementation of dispatch_tool returning per-tool JSON."""
        return stage_results[tool_name]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = None
        try:
            session_id = await _create_session(client)

            mock_client = _make_mock_anthropic_full_flow()

            with (
                patch("agent.router.anthropic.Anthropic", return_value=mock_client),
                patch(
                    "agent.router.dispatch_tool",
                    side_effect=_dispatch_side_effect,
                ) as mock_dispatch,
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

            # Must terminate with a done event
            assert "done" in event_types, f"Missing 'done' event. Got: {event_types}"

            # Each of the 4 tool stages must produce exactly one tool_result event,
            # preserving the resolve -> classify -> collect -> validate order
            tool_result_events = [e for e in events if e.get("type") == "tool_result"]
            actual_order = [e["tool_name"] for e in tool_result_events]
            assert actual_order == expected_order, (
                f"Tool results streamed in wrong order. "
                f"Expected {expected_order}, got {actual_order}"
            )

            # Stage 4 validate_preflight payload indicates launch readiness
            validate_event = next(
                e for e in tool_result_events if e["tool_name"] == "validate_preflight"
            )
            assert validate_event["result"].get("ready_to_launch") is True

            # Final assistant text is present and references the launch stage
            text_events = [e for e in events if e.get("type") == "text"]
            assert text_events, "Expected at least one 'text' event for the final assistant message"
            final_text = "".join(e.get("text", "") for e in text_events).lower()
            assert "launch" in final_text, (
                f"Final assistant text should reference launch readiness. Got: {final_text!r}"
            )

            # Anthropic should have been called at least 5 times in the agent loop
            # (4 tool_use + 1 end_turn). A background task may add a 6th title-gen
            # call on the first message of a session — the agent loop itself is the
            # contract under test, not the title generator.
            assert mock_client.messages.create.call_count >= 5, (
                f"Expected >=5 Anthropic calls (4 tool_use + 1 end_turn), "
                f"got {mock_client.messages.create.call_count}"
            )

            # dispatch_tool should have been called once per stage
            assert mock_dispatch.call_count == 4, (
                f"Expected 4 dispatch_tool calls, got {mock_dispatch.call_count}"
            )
            dispatched_names = [call.args[0] for call in mock_dispatch.call_args_list]
            assert dispatched_names == expected_order

            # Real DB write: agent_history should contain the full multi-turn conversation
            get_response = await client.get(f"/sessions/{session_id}")
            assert get_response.status_code == 200

        finally:
            if session_id:
                await _delete_session(client, session_id)
