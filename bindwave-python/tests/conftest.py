"""Pytest scaffold for the bindwave SDK test suite.

Phase 13, Plan 13-02 bootstrap. Provides a respx-backed httpx mock transport
fixture so Plans 13-04/13-05 can drop in test_client.py, test_jobs.py, and
test_pagination.py without re-wiring the HTTP mock. This file intentionally
carries no real tests — tests/test_placeholder.py guards the bootstrap CI run.
"""

import pytest

# asyncio backend for anyio-style async tests (pyproject sets asyncio_mode=auto).
anyio_backend = "asyncio"


@pytest.fixture
def respx_mock():
    """Yield a respx router mocking the bindwave base URL.

    respx is a dev dependency (see pyproject [project.optional-dependencies.dev]).
    Imported lazily so the placeholder test can run even in a minimal environment
    without respx installed.
    """
    import respx

    with respx.mock(base_url="https://api.bindwave.com") as router:
        yield router
