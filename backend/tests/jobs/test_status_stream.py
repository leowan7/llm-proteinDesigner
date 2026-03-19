"""Tests for job status SSE streaming (JOB-01).

Covers:
- Redis publish on status update
- SSE endpoint streams status events
- SSE stream closes on terminal status

Implementation target: Plan 03-03.
"""

import pytest


class TestStatusPublish:
    """JOB-01: Status updates are published to Redis and streamed via SSE."""

    def test_publish_status_publishes_to_redis_channel(self):
        """Verify publish_status(job_id, "running", "Initializing GPU") publishes JSON
        to job:{job_id}:status Redis channel.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_sse_endpoint_streams_status_events(self):
        """Verify GET /jobs/{job_id}/status returns text/event-stream with
        SSE-formatted data containing JobStatusEvent JSON.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_sse_endpoint_closes_on_terminal_status(self):
        """Verify SSE stream terminates when status is "complete", "failed",
        or "cancelled". Stream should not remain open indefinitely after a
        terminal event is received.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
