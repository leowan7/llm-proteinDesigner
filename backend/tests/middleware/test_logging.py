"""Tests for StructuredLoggingMiddleware.

Covers:
- Each HTTP request produces a JSON log line with required keys
- Log line contains user_id when an access_token cookie is present
"""
import os
os.environ.setdefault("TESTING", "true")

import json
import logging
from unittest.mock import patch

import jwt
import pytest
from httpx import AsyncClient, ASGITransport

from main import app
from middleware.logging import StructuredLoggingMiddleware, setup_logging

# Disable rate limiting — no Redis in test environment
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_logging_middleware_emits_json(caplog):
    """Each HTTP request produces a valid JSON log line with required keys.

    Uses pytest's caplog fixture to capture log output from the
    kendrew.access logger that StructuredLoggingMiddleware writes to.
    """
    with caplog.at_level(logging.INFO, logger="kendrew.access"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/health")

    # Find the log record emitted by StructuredLoggingMiddleware
    access_records = [r for r in caplog.records if r.name == "kendrew.access"]
    assert len(access_records) >= 1, "Expected at least one access log record"

    log_line = access_records[-1].getMessage()
    log_data = json.loads(log_line)

    # Verify required fields
    assert "method" in log_data
    assert "path" in log_data
    assert "status_code" in log_data
    assert "duration_ms" in log_data
    assert log_data["method"] == "GET"
    assert log_data["path"] == "/health"


async def test_logging_extracts_user_id(caplog):
    """Log line contains user_id when a valid JWT access_token cookie is present."""
    user_id = "test-logging-user-uuid"
    # Encode without signature verification — logging middleware decodes the same way
    token = jwt.encode({"sub": user_id}, "any-secret", algorithm="HS256")

    with caplog.at_level(logging.INFO, logger="kendrew.access"):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"access_token": token},
        ) as client:
            await client.get("/health")

    access_records = [r for r in caplog.records if r.name == "kendrew.access"]
    assert len(access_records) >= 1

    log_data = json.loads(access_records[-1].getMessage())
    assert log_data.get("user_id") == user_id


async def test_logging_no_user_id_without_cookie(caplog):
    """Log line contains null user_id when no access_token cookie is present."""
    with caplog.at_level(logging.INFO, logger="kendrew.access"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/health")

    access_records = [r for r in caplog.records if r.name == "kendrew.access"]
    assert len(access_records) >= 1

    log_data = json.loads(access_records[-1].getMessage())
    assert log_data.get("user_id") is None
