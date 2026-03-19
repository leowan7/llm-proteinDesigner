"""FastAPI router for agent chat with SSE streaming.

Endpoints:
    POST /agent/session       — create a new session
    POST /agent/message       — send a message, stream response via SSE
    DELETE /agent/session/{id} — delete a session (clear conversation)

SSE event types streamed by POST /agent/message:
    {"type": "status",      "text": "..."}                    — status during tool execution
    {"type": "text",        "text": "..."}                    — text chunk from Claude
    {"type": "tool_result", "tool_name": "...", "result": {}} — structured result for card rendering
    {"type": "done"}                                           — end of response
    {"type": "error",       "text": "..."}                    — error during generation
"""

import json

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.session import session_manager
from agent.system_prompt import AGENT_SYSTEM_PROMPT
from agent.tools import TOOL_DEFINITIONS, dispatch_tool
from auth.dependencies import get_current_user
from config import settings

router = APIRouter(prefix="/agent", tags=["agent"])


class MessageRequest(BaseModel):
    """Request body for the agent message endpoint."""

    session_id: str
    message: str


class NewSessionResponse(BaseModel):
    """Response body for the create session endpoint."""

    session_id: str


@router.post("/session")
async def create_session(
    user_id: str = Depends(get_current_user),
) -> NewSessionResponse:
    """Create a new agent session, replacing any existing active session.

    A session stores the full multi-turn message history in Redis. The
    returned session_id must be included in all subsequent /agent/message
    calls for this conversation.
    """
    session_id = await session_manager.create(user_id)
    return NewSessionResponse(session_id=session_id)


@router.post("/message")
async def agent_message(
    req: MessageRequest,
    user_id: str = Depends(get_current_user),
):
    """Send a message to the agent and stream the response via SSE.

    Loads the message history from Redis, appends the user message, then
    runs the Claude tool-use loop until end_turn. During the loop, tool
    results are dispatched server-side and streamed as tool_result events.
    The final message history (including all tool_use and tool_result blocks)
    is saved back to Redis.

    Returns a text/event-stream with events described in module docstring.
    """
    try:
        messages = await session_manager.load(user_id, req.session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    messages.append({"role": "user", "content": req.message})

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def event_generator():
        """Async generator that runs the Claude tool-use loop and yields SSE lines."""
        # nonlocal so we can reassign messages as we accumulate tool results
        nonlocal messages

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
                # ToolUseBlock objects which are not JSON-serializable. We convert them
                # to plain dicts so Redis (via session_manager.save) can serialize them.
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
                            result_json = await dispatch_tool(block.name, block.input)
                            result_data = json.loads(result_json)

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

            # Save updated message history (includes all tool_use + tool_result blocks)
            await session_manager.save(user_id, req.session_id, messages)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except anthropic.APIError as exc:
            error_msg = getattr(exc, "message", str(exc))
            yield f"data: {json.dumps({'type': 'error', 'text': f'Agent error: {error_msg}'})}\n\n"
        except Exception:
            # Do not leak internal error details to the client
            yield f"data: {json.dumps({'type': 'error', 'text': 'An unexpected error occurred. Please try again.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
):
    """Delete an agent session, clearing the conversation history from Redis."""
    await session_manager.delete(user_id, session_id)
    return {"status": "deleted"}


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
    }
    return status_map.get(tool_name, "Processing...")
