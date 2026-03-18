"""Shared test fixtures for auth integration tests.

These tests require a running Supabase local stack (supabase start).
They hit the real Supabase Auth service -- not mocks.
"""

import os
from dotenv import load_dotenv

# Load .env.local BEFORE setting defaults -- this ensures the real
# Supabase JWT secret (from `supabase status`) is used, not a placeholder.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env.local"))

# TESTING=true disables CSRF middleware so POST requests don't get 403
os.environ["TESTING"] = "true"
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:54321")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("CSRF_SECRET", "test-csrf-secret")

import pytest
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Async test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# Pre-verified test user credentials (from seed.sql)
TEST_USER_EMAIL = "test@example.com"
TEST_USER_PASSWORD = "Password123!"
