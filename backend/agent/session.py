"""Redis-backed session management for agent message history.

Each session stores the messages[] array replayed to Claude on each turn.
Sessions are keyed as session:{user_id}:{session_id} and expire after
agent_session_ttl_seconds (default: 3600 seconds / 1 hour).

Exports:
    SessionManager: Class for creating, loading, saving, and deleting sessions.
    session_manager: Module-level singleton initialized on first use.
"""

import json
import uuid

import redis.asyncio as aioredis
from config import settings


class SessionManager:
    """Manages agent conversation message history in Redis.

    Each session stores the messages[] array that gets replayed to Claude
    on each turn. Sessions are keyed as session:{user_id}:{session_id}
    and expire after agent_session_ttl_seconds.

    Args:
        redis_client: Optional pre-constructed Redis client. If None, a client
            is created lazily from settings.redis_url on first use. Pass a
            FakeRedis instance in tests to avoid needing a real Redis server.
    """

    def __init__(self, redis_client: aioredis.Redis | None = None):
        self._redis = redis_client

    async def _get_redis(self) -> aioredis.Redis:
        """Return the Redis client, creating it lazily if needed."""
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    def _key(self, user_id: str, session_id: str) -> str:
        """Build the Redis key for a session's message history."""
        return f"session:{user_id}:{session_id}"

    async def create(self, user_id: str) -> str:
        """Create a new session for a user.

        Stores an empty message list and records the session as the user's
        active session. Any previous active_session pointer is overwritten
        (old session data remains in Redis until its TTL expires).

        Args:
            user_id: Authenticated user UUID.

        Returns:
            New session_id (UUID string).
        """
        session_id = str(uuid.uuid4())
        redis_client = await self._get_redis()

        # Store empty message list with TTL
        await redis_client.set(
            self._key(user_id, session_id),
            json.dumps([]),
            ex=settings.agent_session_ttl_seconds,
        )
        # Track active session for this user (for resume-after-refresh)
        await redis_client.set(
            f"active_session:{user_id}",
            session_id,
            ex=settings.agent_session_ttl_seconds,
        )
        return session_id

    async def load(self, user_id: str, session_id: str) -> list[dict]:
        """Load message history for a session.

        Args:
            user_id: Authenticated user UUID.
            session_id: Session UUID.

        Returns:
            List of message dicts with 'role' and 'content' keys.

        Raises:
            ValueError: If session not found or expired.
        """
        redis_client = await self._get_redis()
        data = await redis_client.get(self._key(user_id, session_id))
        if data is None:
            raise ValueError(f"Session {session_id} not found or expired")
        return json.loads(data)

    async def save(self, user_id: str, session_id: str, messages: list[dict]) -> None:
        """Save updated message history for a session, refreshing the TTL.

        Args:
            user_id: Authenticated user UUID.
            session_id: Session UUID.
            messages: Updated messages list to persist.
        """
        redis_client = await self._get_redis()
        await redis_client.set(
            self._key(user_id, session_id),
            json.dumps(messages),
            ex=settings.agent_session_ttl_seconds,
        )

    async def get_active_session(self, user_id: str) -> str | None:
        """Get the active session ID for a user, if any.

        Args:
            user_id: Authenticated user UUID.

        Returns:
            Session UUID string, or None if no active session exists.
        """
        redis_client = await self._get_redis()
        return await redis_client.get(f"active_session:{user_id}")

    async def delete(self, user_id: str, session_id: str) -> None:
        """Delete a session and clear the user's active session pointer.

        Args:
            user_id: Authenticated user UUID.
            session_id: Session UUID to delete.
        """
        redis_client = await self._get_redis()
        await redis_client.delete(self._key(user_id, session_id))
        await redis_client.delete(f"active_session:{user_id}")


# Module-level singleton — initialized on first use via lazy Redis connection
session_manager = SessionManager()
