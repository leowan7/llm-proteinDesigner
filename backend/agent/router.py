"""FastAPI router for agent chat with SSE streaming.

Endpoints:
    POST /agent/message       — send a message, stream response via SSE

SSE event types streamed by POST /agent/message:
    {"type": "status",      "text": "..."}                    — status during tool execution
    {"type": "text",        "text": "..."}                    — text chunk from Claude
    {"type": "tool_result", "tool_name": "...", "result": {}} — structured result for card rendering
    {"type": "done"}                                           — end of response
    {"type": "error",       "text": "..."}                    — error during generation

Session management:
    Sessions are stored in PostgreSQL (sessions / session_messages tables).
    See sessions/router.py for CRUD endpoints (create, list, get, update, delete).
    The agent reads/writes agent_history JSONB from the sessions table, and appends
    user-visible rows to session_messages for sidebar display.
"""

import asyncio
import json
import logging

import anthropic
import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.system_prompt import AGENT_SYSTEM_PROMPT
from agent.tools import TOOL_DEFINITIONS, dispatch_tool
from auth.dependencies import get_current_user
from config import settings
from sessions.queries import (
    append_message,
    get_agent_history,
    get_session_with_messages,
    update_agent_history,
    update_session_title,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"], include_in_schema=False)


class MessageRequest(BaseModel):
    """Request body for the agent message endpoint."""

    session_id: str
    message: str


@router.post("/message")
async def agent_message(
    req: MessageRequest,
    user_id: str = Depends(get_current_user),
):
    """Send a message to the agent and stream the response via SSE.

    Loads the agent history from PostgreSQL, appends the user message, then
    runs the Claude tool-use loop until end_turn. During the loop, tool
    results are dispatched server-side and streamed as tool_result events.
    The final message history (including all tool_use and tool_result blocks)
    is saved back to the sessions table.

    User-visible messages (plain user text, plain assistant text) are also
    appended to session_messages for sidebar rendering.

    If this is the first message in the session (sort_order == 0), a background
    task generates a short session title via Claude Haiku.

    Returns a text/event-stream with events described in module docstring.
    """
    # Verify the session belongs to this user before loading history
    session_check = await get_session_with_messages(
        user_id=user_id, session_id=req.session_id
    )
    if session_check is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Load full Anthropic message history (includes tool_use/tool_result blocks)
    messages = await get_agent_history(req.session_id)

    # Append user message to the in-memory history
    messages.append({"role": "user", "content": req.message})

    # Persist the user-visible message and get its sort order
    user_sort = await append_message(req.session_id, "user", req.message)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def event_generator():
        """Async generator that runs the Claude tool-use loop and yields SSE lines."""
        nonlocal messages

        # Accumulate card data from tool_result events during this turn
        cards_accumulated: list[dict] = []
        final_assistant_text = ""

        try:
            while True:
                response = client.messages.create(
                    model=settings.agent_model,
                    max_tokens=settings.agent_max_tokens,
                    system=AGENT_SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                )

                # Serialize ContentBlock objects to dicts before storing in history.
                # The Anthropic SDK returns response.content as a list of TextBlock/
                # ToolUseBlock objects which are not JSON-serializable.
                serialized_content = []
                for block in response.content:
                    if block.type == "text":
                        serialized_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        serialized_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                messages.append({"role": "assistant", "content": serialized_content})

                if response.stop_reason == "end_turn":
                    # Stream the final text content to the client
                    for block in response.content:
                        if hasattr(block, "text"):
                            final_assistant_text += block.text
                            yield f"data: {json.dumps({'type': 'text', 'text': block.text})}\n\n"
                    break

                if response.stop_reason == "tool_use":
                    tool_results = []

                    for block in response.content:
                        if block.type == "tool_use":
                            # Emit a status event so the UI can show progress
                            status_text = _tool_status_text(block.name)
                            yield f"data: {json.dumps({'type': 'status', 'text': status_text})}\n\n"

                            # Execute the tool server-side
                            result_json = await dispatch_tool(block.name, block.input, user_id=user_id)
                            result_data = json.loads(result_json)

                            # Accumulate card data for persisting with assistant message
                            cards_accumulated.append({
                                "tool_name": block.name,
                                "result": result_data,
                            })

                            # Emit structured tool result for frontend card rendering
                            yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': block.name, 'result': result_data})}\n\n"

                            # Anthropic API requires tool_result in a user message
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_json,
                            })

                    # Append all tool results as a single user message (API requirement)
                    messages.append({"role": "user", "content": tool_results})

            # Persist the full Anthropic history (includes tool_use/tool_result blocks)
            await update_agent_history(req.session_id, messages)

            # Persist user-visible assistant text + accumulated card data
            cards_to_save = cards_accumulated if cards_accumulated else None
            await append_message(
                req.session_id,
                "assistant",
                final_assistant_text,
                cards_to_save,
            )

            # If this was the first user message, generate a session title asynchronously
            if user_sort == 0:
                asyncio.create_task(
                    _generate_title_background(
                        session_id=req.session_id,
                        user_id=user_id,
                        first_message=req.message,
                    )
                )

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except anthropic.APIError as exc:
            logger.exception("Agent SSE: anthropic.APIError")
            sentry_sdk.capture_exception(exc)
            error_msg = getattr(exc, "message", str(exc))
            yield f"data: {json.dumps({'type': 'error', 'text': f'Agent error: {error_msg}'})}\n\n"
        except Exception as exc:
            # Do not leak internal error details to the client, but DO log them
            # server-side and capture to Sentry so prod incidents are debuggable.
            logger.exception("Agent SSE: unhandled exception")
            sentry_sdk.capture_exception(exc)
            yield f"data: {json.dumps({'type': 'error', 'text': 'An unexpected error occurred. Please try again.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _generate_title_background(
    session_id: str,
    user_id: str,
    first_message: str,
) -> None:
    """Background task: generate a session title via Claude Haiku and persist it.

    Called via asyncio.create_task so it does not block the SSE response stream.
    Errors are silently swallowed — title generation is best-effort.

    Args:
        session_id: UUID of the session to title.
        user_id: Authenticated user UUID.
        first_message: The first user message text used as title context.
    """
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=32,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Generate a short title (max 8 words) for a protein design "
                            f"conversation that starts with: {first_message}"
                        ),
                    }
                ],
            ),
        )
        title = response.content[0].text.strip().strip('"').strip("'")
        await update_session_title(
            user_id=user_id, session_id=session_id, title=title
        )
    except Exception as exc:
        # Title generation is best-effort; failures are non-fatal but should
        # not be invisible -- log + capture so we know if title generation has
        # silently broken (e.g. anthropic API change, model deprecation).
        logger.exception("Title generation failed for session %s", session_id)
        sentry_sdk.capture_exception(exc)


def _tool_status_text(tool_name: str) -> str:
    """Map tool names to user-facing status messages shown during tool execution.

    Args:
        tool_name: Internal tool name from Claude's tool_use block.

    Returns:
        Human-readable status string for the SSE status event.
    """
    status_map = {
        "resolve_structure": "Fetching structure...",
        "classify_intent": "Analyzing your design goal...",
        "collect_parameters": "Preparing parameters...",
        "validate_preflight": "Running pre-flight checks...",
        "extract_interface": "Extracting interface residues...",
        "load_job_results": "Loading job results...",
        "analyze_candidates": "Analyzing candidates...",
        "flag_red_flags": "Checking for red flags...",
        "generate_report": "Generating report...",
        "submit_refolding_job": "Setting up refolding job...",
    }
    return status_map.get(tool_name, "Processing...")
