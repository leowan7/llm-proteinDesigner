"""Session CRUD API endpoints.

Provides persistent session management for the agent chat interface. Sessions
survive page refresh and browser close; the agent reconstructs full context on
resume from the agent_history JSONB column.

Endpoints:
    GET    /sessions                         — list sessions (paginated)
    POST   /sessions                         — create a new session
    GET    /sessions/{session_id}            — get session with message history
    PUT    /sessions/{session_id}            — update session title
    DELETE /sessions/{session_id}            — delete session + all messages
    POST   /sessions/{session_id}/generate-title — generate AI title from first message
"""

import asyncio

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.dependencies import get_current_user
from config import settings
from sessions.queries import (
    create_session,
    delete_session,
    get_session_with_messages,
    list_sessions,
    update_session_title,
)

router = APIRouter(prefix="/sessions", tags=["sessions"], include_in_schema=False)


class UpdateTitleRequest(BaseModel):
    """Request body for updating a session's title."""

    title: str


@router.get("")
async def list_sessions_endpoint(
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None),
    user_id: str = Depends(get_current_user),
):
    """List sessions for the authenticated user, newest first.

    Supports keyset pagination via the `before` timestamp cursor — pass the
    updated_at value of the last session in a page to fetch the next page.

    Args:
        limit: Max sessions to return (1–100, default 50).
        before: ISO-8601 timestamp cursor; returns sessions updated before this.

    Returns:
        JSON with `sessions` array of {id, title, created_at, updated_at}.
    """
    sessions = await list_sessions(user_id=user_id, limit=limit, before=before)
    return {"sessions": sessions}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    user_id: str = Depends(get_current_user),
):
    """Create a new empty session for the authenticated user.

    Returns:
        JSON with the newly created session {id, title, created_at, updated_at}
        and HTTP 201.
    """
    session = await create_session(user_id=user_id)
    return session


@router.get("/{session_id}")
async def get_session_endpoint(
    session_id: str,
    user_id: str = Depends(get_current_user),
):
    """Retrieve a session with its full message history.

    Returns the session metadata plus all session_messages ordered by
    sort_order, and the agent_history JSONB for context reconstruction.

    Args:
        session_id: UUID of the session to fetch.

    Returns:
        JSON with {id, title, agent_history, messages: [...]}.

    Raises:
        404: If the session does not exist or belongs to a different user.
    """
    session = await get_session_with_messages(user_id=user_id, session_id=session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session


@router.put("/{session_id}")
async def update_session_endpoint(
    session_id: str,
    body: UpdateTitleRequest,
    user_id: str = Depends(get_current_user),
):
    """Update the title of a session.

    Args:
        session_id: UUID of the session to update.
        body.title: New title string.

    Returns:
        JSON with {session_id, title}.

    Raises:
        404: If the session does not exist or belongs to a different user.
    """
    updated = await update_session_title(
        user_id=user_id,
        session_id=session_id,
        title=body.title,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return {"session_id": session_id, "title": body.title}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session_endpoint(
    session_id: str,
    user_id: str = Depends(get_current_user),
):
    """Delete a session and all its messages (cascade).

    Args:
        session_id: UUID of the session to delete.

    Raises:
        404: If the session does not exist or belongs to a different user.
    """
    deleted = await delete_session(user_id=user_id, session_id=session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    # 204 No Content — no response body


@router.post("/{session_id}/generate-title")
async def generate_title_endpoint(
    session_id: str,
    user_id: str = Depends(get_current_user),
):
    """Generate an AI-powered title for the session from its first user message.

    Calls Claude Haiku with the first user message as context and writes the
    generated title back to the session. This endpoint responds synchronously
    and returns the generated title; the agent router calls it fire-and-forget
    via asyncio.create_task.

    Args:
        session_id: UUID of the session to title.

    Returns:
        JSON with {title: "..."}. Returns None if the session has no messages.

    Raises:
        404: If the session does not exist or belongs to a different user.
    """
    session = await get_session_with_messages(user_id=user_id, session_id=session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Find the first user message
    first_user_message = next(
        (m["content"] for m in session["messages"] if m["role"] == "user"),
        None,
    )
    if first_user_message is None:
        return {"title": None}

    title = await _generate_title_from_message(first_user_message)
    await update_session_title(user_id=user_id, session_id=session_id, title=title)
    return {"title": title}


async def _generate_title_background(
    session_id: str,
    user_id: str,
    first_message: str,
) -> None:
    """Background task: generate a session title and persist it.

    Designed to run via asyncio.create_task so it does not block the SSE
    response stream. Errors are logged but do not propagate (best-effort).

    Args:
        session_id: UUID of the session to title.
        user_id: Authenticated user UUID (needed for update_session_title).
        first_message: The first user message text.
    """
    try:
        title = await _generate_title_from_message(first_message)
        await update_session_title(user_id=user_id, session_id=session_id, title=title)
    except Exception:
        # Title generation is best-effort; do not crash the session on failure
        pass


async def _generate_title_from_message(first_message: str) -> str:
    """Call Claude Haiku to produce a short session title.

    Args:
        first_message: The first user message in the conversation.

    Returns:
        A short title string (max ~8 words, trimmed).
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Run sync SDK call in executor to avoid blocking the event loop
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
    return title
