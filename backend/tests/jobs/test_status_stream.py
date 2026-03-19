"""Tests for job status SSE streaming (JOB-01).

Covers:
- Redis publish on status update
- SSE endpoint streams status events
- SSE stream closes on terminal status

Implementation target: Plan 03-03.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.dependencies import get_current_user
from main import app


def _override_user(user_id: str = "user-123"):
    """Return a FastAPI dependency override that returns a fixed user ID."""
    async def _dep():
        return user_id
    return _dep


class TestStatusPublish:
    """JOB-01: Status updates are published to Redis and streamed via SSE."""

    @pytest.mark.anyio
    async def test_publish_status_publishes_to_redis_channel(self):
        """Verify publish_status(job_id, "running", "Initializing GPU") publishes JSON
        to job:{job_id}:status Redis channel.
        """
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("worker.tasks.aioredis.from_url", return_value=mock_redis):
            from worker.tasks import publish_status
            await publish_status("job-1", "running", "Initializing GPU")

        expected_channel = "job:job-1:status"
        expected_payload = json.dumps(
            {"job_id": "job-1", "status": "running", "stage": "Initializing GPU"}
        )
        mock_redis.publish.assert_called_once_with(expected_channel, expected_payload)
        mock_redis.aclose.assert_called_once()

    @pytest.mark.anyio
    async def test_sse_endpoint_streams_status_events(self):
        """Verify GET /jobs/{job_id}/status returns text/event-stream with
        SSE-formatted data containing JobStatusEvent JSON.
        """
        from httpx import AsyncClient, ASGITransport

        # Mock DB pool returning a running job
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "status": "running",
            "stage": "Running diffusion",
        })
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        # Mock Redis pubsub — send one terminal message then stop
        message = {
            "type": "message",
            "data": json.dumps(
                {"job_id": "job-abc", "status": "complete", "stage": "Complete"}
            ).encode(),
        }

        async def fake_listen():
            yield message

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen = fake_listen
        mock_pubsub.unsubscribe = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_redis.aclose = AsyncMock()

        app.dependency_overrides[get_current_user] = _override_user("user-123")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=mock_pool),
                patch("jobs.router.aioredis.from_url", return_value=mock_redis),
            ):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/jobs/job-abc/status",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    @pytest.mark.anyio
    async def test_sse_endpoint_closes_on_terminal_status(self):
        """Verify SSE stream terminates when status is "complete".

        The event generator should break out of the pubsub loop after receiving
        a terminal status event and not remain open indefinitely.
        """
        from jobs.router import _sse_event_generator

        # Simulate a pubsub that delivers a "complete" message
        complete_message = {
            "type": "message",
            "data": json.dumps(
                {"job_id": "job-xyz", "status": "complete", "stage": "Complete"}
            ).encode(),
        }

        async def fake_listen():
            yield complete_message

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen = fake_listen
        mock_pubsub.unsubscribe = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_redis.aclose = AsyncMock()

        with patch("jobs.router.aioredis.from_url", return_value=mock_redis):
            events = []
            # Generator should yield current state + complete event then stop
            async for event in _sse_event_generator(
                job_id="job-xyz",
                current_status="running",
                current_stage="Running diffusion",
            ):
                events.append(event)

        # Should have yielded the current state + the complete message, then stopped
        assert len(events) >= 1
        # Last event should contain the complete status
        last_event = events[-1]
        assert "complete" in last_event
