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
import logging

import anthropic
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.session import session_manager
from agent.system_prompt import AGENT_SYSTEM_PROMPT
from agent.tools import TOOL_DEFINITIONS, dispatch_tool
from auth.dependencies import get_current_user
from config import settings
from middleware.rate_limit import limiter

logger = logging.getLogger(__name__)

MAX_SSE_PER_USER = settings.max_sse_connections_per_user

# Maximum allowed length for agent chat messages (characters).
MAX_MESSAGE_LENGTH = 10_000


async def _check_sse_limit(user_id: str) -> None:
    """Check if user has exceeded max concurrent SSE connections.

    Uses a Redis key with TTL to track active SSE connections.
    Raises HTTPException 429 if limit exceeded.
    """
    r = aioredis.from_url(settings.redis_url)
    key = f"sse_count:{user_id}"
    count = await r.incr(key)
    await r.expire(key, 300)  # Auto-expire after 5 min (safety net)
    if count > MAX_SSE_PER_USER:
        await r.decr(key)
        await r.aclose()
        raise HTTPException(status_code=429, detail="Too many active connections")
    await r.aclose()


async def _release_sse_slot(user_id: str) -> None:
    """Decrement the SSE connection counter when a stream closes."""
    r = aioredis.from_url(settings.redis_url)
    key = f"sse_count:{user_id}"
    await r.decr(key)
    await r.aclose()

router = APIRouter(prefix="/agent", tags=["agent"])


class MessageRequest(BaseModel):
    """Request body for the agent message endpoint."""

    session_id: str
    message: str


class NewSessionResponse(BaseModel):
    """Response body for the create session endpoint."""

    session_id: str


@router.post("/session")
@limiter.limit("20/minute")
async def create_session(
    request: Request,
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
@limiter.limit("20/minute")
async def agent_message(
    request: Request,
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
    # Validate message length to prevent oversized payloads.
    if len(req.message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Message too long ({len(req.message)} chars). Maximum is {MAX_MESSAGE_LENGTH}.",
        )

    # Enforce per-user SSE connection limit.
    await _check_sse_limit(user_id)

    try:
        messages = await session_manager.load(user_id, req.session_id)
    except ValueError:
        await _release_sse_slot(user_id)
        raise HTTPException(status_code=404, detail="Session not found or expired")

    messages.append({"role": "user", "content": req.message})

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def event_generator():
        """Async generator that runs the Claude tool-use loop and yields SSE lines."""
        # nonlocal so we can reassign messages as we accumulate tool results
        nonlocal messages

        try:
            while True:
                # Retry with backoff on overload (529)
                for attempt in range(3):
                    try:
                        response = client.messages.create(
                            model=settings.agent_model,
                            max_tokens=settings.agent_max_tokens,
                            system=AGENT_SYSTEM_PROMPT,
                            tools=TOOL_DEFINITIONS,
                            messages=messages,
                        )
                        break
                    except anthropic.APIStatusError as retry_exc:
                        if retry_exc.status_code == 529 and attempt < 2:
                            import asyncio
                            await asyncio.sleep(2 ** attempt)  # 1s, 2s
                            continue
                        raise

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
                            result_json = await dispatch_tool(block.name, block.input, user_id=user_id)
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
        finally:
            await _release_sse_slot(user_id)

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
        "resolve_structure": "Searching RCSB for structure",
        "classify_intent": "Selecting design tool",
        "collect_parameters": "Setting parameters",
        "validate_preflight": "Running pre-flight checks",
        "extract_interface": "Analyzing binding interface",
    }
    return status_map.get(tool_name, "Processing")


import asyncio


async def _mock_event_generator(user_message: str, user_id: str):
    """Scripted SSE responses for UI testing without Anthropic API credits.

    Simulates the full agent flow: resolve_structure → classify_intent →
    collect_parameters → validate_preflight → review card. Includes realistic
    delays to mimic network/processing time.
    """
    msg = user_message.lower()

    # Step 1: Acknowledge and resolve structure
    yield f"data: {json.dumps({'type': 'status', 'text': 'Fetching structure...'})}\n\n"
    await asyncio.sleep(0.8)

    # Resolve structure tool result
    structure_result = {
        "status": "success",
        "pdb_id": "5FXS",
        "pdb_path": "/tmp/structures/5FXS.pdb",
        "protein_name": "INSULIN-LIKE GROWTH FACTOR 1 RECEPTOR",
        "resolution": 1.90,
        "method": "X-RAY DIFFRACTION",
        "chain_count": 1,
        "selected_chain": "A",
        "residue_count": 308,
        "chains": [
            {"id": "A", "name": "Insulin-like growth factor 1 receptor", "residue_count": 308, "organism": "Homo sapiens"},
        ],
        "normalization_changes": [],
        "file_size_bytes": 245000,
        "message": "Structure 5FXS fetched from RCSB (245000 bytes).",
    }
    yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': 'resolve_structure', 'result': structure_result})}\n\n"
    await asyncio.sleep(0.3)

    # Agent text explaining the structure
    yield f"data: {json.dumps({'type': 'text', 'text': 'I found IGF1R extracellular domain at 1.90 A resolution (PDB 5FXS, Homo sapiens). This is the ligand-binding L1 domain — a good target for blocking IGF1/IGF2 binding.'})}\n\n"
    await asyncio.sleep(0.5)

    # Check if user already specified enough to skip questions
    if "minibinder" in msg or "miniprotein" in msg or "binder" in msg:
        # Skip design type question — recommend tool directly
        yield f"data: {json.dumps({'type': 'text', 'text': 'BindCraft is the best fit here — it uses induced-fit interface design through AF2 hallucination, which handles the flexible surfaces common in receptor extracellular domains. It produces ready-to-express sequences without a separate sequence design step.'})}\n\n"
        await asyncio.sleep(0.3)
    else:
        yield f"data: {json.dumps({'type': 'text', 'text': 'What type of molecule would you like to design against this target? Options: miniprotein binder, VHH/nanobody, cyclic peptide, or full antibody.'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # classify_intent
    yield f"data: {json.dumps({'type': 'status', 'text': 'Analyzing your design goal...'})}\n\n"
    await asyncio.sleep(0.5)

    intent_result = {
        "design_type": "minibinder",
        "recommended_tool": "bindcraft",
        "rationale": "BindCraft uses induced-fit AF2 hallucination, ideal for targeting receptor ligand-binding domains with flexible surfaces.",
    }
    yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': 'classify_intent', 'result': intent_result})}\n\n"
    await asyncio.sleep(0.3)

    # Pilot vs production question
    yield f"data: {json.dumps({'type': 'text', 'text': 'Would you like to start with a **pilot run** (10 designs) to validate the setup, or go straight to **production scale** (100-500 designs)?'})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
