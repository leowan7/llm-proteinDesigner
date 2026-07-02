"""Pytest fixtures for backend/tests/api_v1/ — Phase 13 Wave 0 scaffolds.

Mirrors the shape of backend/tests/conftest.py:31-42 (anyio + ASGITransport).
Three additional fixtures specific to v1 authentication:
- synthetic_api_key: returns a (plaintext, prefix, hash, row_dict) 4-tuple
- idempotency_key: returns a random hex UUID string
- override_api_key: bypasses get_current_api_key via dependency_overrides
"""

import os
import uuid

# Phase 13: seed a dev-only pepper before `from main import app` loads settings,
# so isolated runs of tests/api_v1/ still have a non-empty pepper (verify_api_key
# fails closed on an empty pepper). setdefault so the parent conftest / a real
# env value wins. Mirrors backend/tests/conftest.py.
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("API_KEY_PEPPER", "test_pepper_dev_only")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Async test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def synthetic_api_key():
    """Returns (plaintext, prefix, hash, row_dict) 4-tuple.

    The import of auth.api_keys is guarded with try/except so this conftest is
    committable before Plan 13-02 ships the module. Tests that depend on an
    actual generated key will be skipped automatically when the module is absent.
    """
    try:
        from auth.api_keys import generate_api_key
        plaintext, prefix, h = generate_api_key(env="test")
    except ImportError:
        pytest.skip("Plan 13-02 must ship auth.api_keys before this fixture is usable")

    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "organization_id": "00000000-0000-0000-0000-000000000002",
        "role_at_creation": "owner",
        "bcrypt_hash": h,
        "prefix": prefix,
        "revoked_at": None,
    }
    return plaintext, prefix, h, row


@pytest.fixture
def idempotency_key() -> str:
    """Returns a fresh random hex UUID string suitable as an Idempotency-Key header."""
    return uuid.uuid4().hex


@pytest.fixture
def override_api_key(synthetic_api_key):
    """Bypass get_current_api_key by injecting the synthetic key's (org_id, role).

    Uses the same dependency_overrides + yield + cleanup pattern as
    backend/tests/jobs/test_cancel.py:21-39 (_override_active_org).

    The import of get_current_api_key is guarded with try/except so the fixture
    is committable before Plan 13-02 ships auth.api_key_dependencies.
    """
    try:
        from auth.api_key_dependencies import get_current_api_key
    except ImportError:
        pytest.skip("Plan 13-02 must ship auth.api_key_dependencies before this fixture is usable")

    _, _, _, row = synthetic_api_key

    async def _dep():
        return (row["organization_id"], "owner")

    app.dependency_overrides[get_current_api_key] = _dep
    yield
    app.dependency_overrides.pop(get_current_api_key, None)
