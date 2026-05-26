"""PostgreSQL-backed session query functions.

Replaces the Redis SessionManager with persistent storage in the sessions and
session_messages tables. All functions use the asyncpg connection pool from
db.connection.

Design note (D-09): sessions.agent_history stores the full Anthropic messages
array (including tool_use/tool_result blocks). session_messages stores
user-visible messages only — one row per user message and one row per
assistant text response.

Exports:
    create_session, list_sessions, get_session_with_messages,
    update_session_title, delete_session, append_message,
    update_agent_history, get_agent_history
"""

import json

from db.connection import get_db_pool


async def create_session(user_id: str) -> dict:
    """Insert a new session row for the given user.

    Args:
        user_id: Authenticated user UUID string.

    Returns:
        Dict with keys: id, title, created_at, updated_at.

    Raises:
        asyncpg.PostgresError: On database error.
    """
    pool = await get_db_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO public.sessions (user_id)
        VALUES ($1)
        RETURNING id, title, created_at, updated_at
        """,
        user_id,
    )
    return dict(row)


async def list_sessions(
    user_id: str,
    limit: int = 50,
    before: str | None = None,
) -> list[dict]:
    """List sessions for a user, ordered by updated_at DESC.

    Supports keyset pagination via the `before` cursor (ISO timestamp). Only
    sessions updated before that timestamp are returned, allowing the client
    to page through older sessions without offset-based drift.

    Args:
        user_id: Authenticated user UUID string.
        limit: Maximum number of sessions to return (default 50, capped at 100).
        before: Optional ISO-8601 timestamp cursor for pagination. When provided,
            only sessions with updated_at < before are returned.

    Returns:
        List of dicts with keys: id, title, created_at, updated_at.

    Raises:
        asyncpg.PostgresError: On database error.
    """
    limit = min(limit, 100)
    pool = await get_db_pool()

    if before is not None:
        rows = await pool.fetch(
            """
            SELECT id, title, created_at, updated_at
            FROM public.sessions
            WHERE user_id = $1
              AND updated_at < $2::TIMESTAMPTZ
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            before,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, title, created_at, updated_at
            FROM public.sessions
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )

    return [dict(row) for row in rows]


async def get_session_with_messages(
    user_id: str,
    session_id: str,
) -> dict | None:
    """Fetch a session and all its messages, enforcing user ownership.

    Returns None if the session does not exist or belongs to a different user.

    Args:
        user_id: Authenticated user UUID string.
        session_id: Session UUID string.

    Returns:
        Dict with keys: id, title, agent_history, messages (list of message
        dicts with id, role, content, cards, sort_order); or None.

    Raises:
        asyncpg.PostgresError: On database error.
    """
    pool = await get_db_pool()

    session_row = await pool.fetchrow(
        """
        SELECT id, title, agent_history, created_at, updated_at
        FROM public.sessions
        WHERE id = $1 AND user_id = $2
        """,
        session_id,
        user_id,
    )

    if session_row is None:
        return None

    message_rows = await pool.fetch(
        """
        SELECT id, role, content, cards, sort_order
        FROM public.session_messages
        WHERE session_id = $1
        ORDER BY sort_order ASC
        """,
        session_id,
    )

    # asyncpg returns JSONB as str; parse to Python objects
    raw_history = session_row["agent_history"]
    agent_history: list[dict] = (
        json.loads(raw_history) if isinstance(raw_history, str) else (raw_history or [])
    )

    messages = []
    for row in message_rows:
        raw_cards = row["cards"]
        cards: list | None = (
            json.loads(raw_cards) if isinstance(raw_cards, str) else raw_cards
        )
        messages.append({
            "id": str(row["id"]),
            "role": row["role"],
            "content": row["content"],
            "cards": cards,
            "sort_order": row["sort_order"],
        })

    return {
        "id": str(session_row["id"]),
        "title": session_row["title"],
        "agent_history": agent_history,
        "messages": messages,
    }


async def update_session_title(
    user_id: str,
    session_id: str,
    title: str,
) -> bool:
    """Update the title of a session, verifying user ownership.

    Args:
        user_id: Authenticated user UUID string.
        session_id: Session UUID string.
        title: New title (max recommended: 120 characters).

    Returns:
        True if the session was found and updated; False otherwise.

    Raises:
        asyncpg.PostgresError: On database error.
    """
    pool = await get_db_pool()
    result = await pool.execute(
        """
        UPDATE public.sessions
        SET title = $1, updated_at = now()
        WHERE id = $2 AND user_id = $3
        """,
        title,
        session_id,
        user_id,
    )
    # execute() returns a tag like "UPDATE 1"
    return result.endswith(" 1")


async def delete_session(user_id: str, session_id: str) -> bool:
    """Delete a session and cascade-delete all its messages.

    Args:
        user_id: Authenticated user UUID string.
        session_id: Session UUID string.

    Returns:
        True if the session was found and deleted; False otherwise.

    Raises:
        asyncpg.PostgresError: On database error.
    """
    pool = await get_db_pool()
    result = await pool.execute(
        """
        DELETE FROM public.sessions
        WHERE id = $1 AND user_id = $2
        """,
        session_id,
        user_id,
    )
    return result.endswith(" 1")


async def append_message(
    session_id: str,
    role: str,
    content: str,
    cards: list | None = None,
) -> int:
    """Insert a message into session_messages and update the session timestamp.

    Computes the next sort_order as MAX(sort_order) + 1 within the session, so
    messages remain ordered by insertion sequence. Also bumps sessions.updated_at
    so the session floats to the top of the list.

    Args:
        session_id: Session UUID string.
        role: Message author — 'user' or 'assistant'.
        content: Plain text content of the message.
        cards: Optional list of card data dicts (tool_result cards for the
            assistant turn).

    Returns:
        The sort_order value assigned to the new message.

    Raises:
        asyncpg.PostgresError: On database error.
        ValueError: If role is not 'user' or 'assistant'.
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"Invalid role: {role!r}. Must be 'user' or 'assistant'.")

    pool = await get_db_pool()

    cards_json: str | None = json.dumps(cards) if cards is not None else None

    row = await pool.fetchrow(
        """
        WITH next_order AS (
            SELECT COALESCE(MAX(sort_order), -1) + 1 AS sort_order
            FROM public.session_messages
            WHERE session_id = $1
        )
        INSERT INTO public.session_messages (session_id, role, content, cards, sort_order)
        SELECT $1, $2, $3, $4::JSONB, next_order.sort_order
        FROM next_order
        RETURNING sort_order
        """,
        session_id,
        role,
        content,
        cards_json,
    )

    # Bump the parent session's updated_at so it rises in the sidebar list
    await pool.execute(
        "UPDATE public.sessions SET updated_at = now() WHERE id = $1",
        session_id,
    )

    return row["sort_order"]


async def update_agent_history(
    session_id: str,
    agent_history: list[dict],
) -> None:
    """Overwrite the agent_history JSONB column for a session.

    Called after every Claude response turn so the full Anthropic messages
    array (including tool_use/tool_result blocks) is persisted and can be
    replayed on session resume.

    Args:
        session_id: Session UUID string.
        agent_history: Full Anthropic messages list (role/content dicts).

    Raises:
        asyncpg.PostgresError: On database error.
    """
    pool = await get_db_pool()
    history_json = json.dumps(agent_history)
    await pool.execute(
        """
        UPDATE public.sessions
        SET agent_history = $1::JSONB, updated_at = now()
        WHERE id = $2
        """,
        history_json,
        session_id,
    )


async def get_agent_history(session_id: str) -> list[dict]:
    """Read the agent_history JSONB column for a session.

    Called at the start of each agent turn to reconstruct the full Claude
    conversation context. Returns an empty list if the session has no history
    yet (first message in a new session).

    Args:
        session_id: Session UUID string.

    Returns:
        Full Anthropic messages list, or [] if session not found or history
        is empty.

    Raises:
        asyncpg.PostgresError: On database error.
    """
    pool = await get_db_pool()
    row = await pool.fetchrow(
        "SELECT agent_history FROM public.sessions WHERE id = $1",
        session_id,
    )

    if row is None:
        return []

    raw = row["agent_history"]
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or []
