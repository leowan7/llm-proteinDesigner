"""Shared test fixtures for auth integration tests and Phase 2 unit tests.

Auth integration tests require a running Supabase local stack (supabase start).
They hit the real Supabase Auth service -- not mocks.

Phase 2 fixtures (test_pdb_path, temp_dir, mock_redis) are lightweight and
require no external services.
"""

import os
import pathlib
import tempfile

from dotenv import load_dotenv

# Load .env.local BEFORE setting defaults -- this ensures the real
# Supabase JWT secret (from `supabase status`) is used, not a placeholder.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env.local"))

# TESTING=true disables CSRF middleware so POST requests don't get 403
os.environ["TESTING"] = "true"
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:54321")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("CSRF_SECRET", "test-csrf-secret")

import pytest  # noqa: E402  # imports after env setup above
from httpx import ASGITransport, AsyncClient  # noqa: E402
from main import app  # noqa: E402  # app reads env vars at import time


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


# ---------------------------------------------------------------------------
# Phase 2 fixtures — no external services required
# ---------------------------------------------------------------------------


@pytest.fixture
def test_pdb_path():
    """Path to the minimal test PDB fixture file."""
    return str(pathlib.Path(__file__).parent / "fixtures" / "test_structure.pdb")


@pytest.fixture
def temp_dir():
    """Temporary directory for PDB output files, cleaned up after test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_redis():
    """Fake Redis-like dict for session storage tests (no real Redis needed)."""

    class FakeRedis:
        def __init__(self):
            self._store = {}

        async def get(self, key):
            return self._store.get(key)

        async def set(self, key, value, ex=None):
            self._store[key] = value

        async def delete(self, key):
            self._store.pop(key, None)

    return FakeRedis()
