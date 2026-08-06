"""Structured /health JSON contract test (SC 3 / SC 4 / SC 5).

The current ``/health`` in backend/main.py returns a flat dict with keys
``api``, ``db``, ``redis``. Phase 11 expands this to include ``r2`` and a
top-level ``status`` summary. Until that ships (Plan 11-04 or 11-05), the
full-contract assertion is xfailed so Wave 0 does not block the wave.

Reference:
    .planning/phases/11-deployment/11-VALIDATION.md row 3/4/5
    backend/main.py lines 110-138 (current /health implementation).
"""

import os

import pytest

os.environ.setdefault("TESTING", "true")


@pytest.mark.xfail(
    reason="/health structured output implemented in Plan 11-04 or 11-05",
    strict=False,
)
def test_health_returns_structured_json():
    """GET /health returns 200 with keys status, db, redis, r2.

    The xfail guard makes this a RED scaffold. Plan 11-04/11-05 adds the R2
    reachability probe and a top-level 'status' summary key.
    """
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text[:200]}"
    )
    body = response.json()
    for key in ("status", "db", "redis", "r2"):
        assert key in body, f"/health response missing key '{key}'. Got: {body}"
