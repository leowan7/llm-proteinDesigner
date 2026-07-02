"""Tests for per-API-key rate limiting on /api/v1/* routes.

Requirements: API-08 (X-RateLimit-* headers on every 200 + Retry-After on 429).
             API-10 (60 rpm per api_keys.id via separate api_v1_limiter instance).

Constraint: slowapi's api_v1_limiter is constructed with
``enabled=settings.rate_limit_enabled and not settings.testing``. Under the test
harness ``TESTING=true`` so the limiter is DISABLED — the 429 / header round-trip
cannot be exercised over HTTP without a live Redis + a non-testing settings load.
We therefore:
  - unit-test the key_func (``get_api_key_id``) directly (the analog pattern from
    tests/middleware/test_rate_limit.py), which is the load-bearing per-key logic;
  - assert the limiter is configured for 60/minute + headers_enabled;
  - xfail the live 429 / header-emission integration checks with a clear reason.
"""

from types import SimpleNamespace

import pytest

from config import settings
from middleware.rate_limit import api_v1_limiter, get_api_key_id


def _make_request(api_key_id=None, host="1.2.3.4"):
    state = SimpleNamespace()
    if api_key_id is not None:
        state.api_key_id = api_key_id
    client = SimpleNamespace(host=host)
    return SimpleNamespace(state=state, client=client)


def test_key_func_uses_api_key_id():
    """API-10: get_api_key_id keys on request.state.api_key_id when present."""
    req = _make_request(api_key_id="key-abc")
    assert get_api_key_id(req) == "apikey:key-abc"


def test_key_func_falls_back_to_ip():
    """API-10: unauthenticated requests (no api_key_id) fall back to ip: so anon
    traffic is still bucketed and one noisy anon IP cannot swamp a keyed bucket."""
    req = _make_request(api_key_id=None, host="9.9.9.9")
    assert get_api_key_id(req) == "ip:9.9.9.9"


def test_limiter_headers_enabled():
    """API-08: api_v1_limiter emits X-RateLimit-* headers (headers_enabled=True)."""
    assert api_v1_limiter.headers_enabled is True


def test_configured_limit_is_60_per_minute():
    """API-10: the per-key budget applied to v1 routes is 60/minute."""
    assert settings.api_v1_rate_limit == "60/minute"


@pytest.mark.xfail(
    reason="api_v1_limiter is disabled under TESTING=true (needs live Redis + "
    "non-testing settings). The 60rpm 429 + Retry-After round-trip is verified "
    "in staging smoke, not the unit harness.",
    strict=False,
)
def test_60rpm():
    """API-10: the 61st request within 60s from the same key returns 429."""
    raise AssertionError("limiter disabled under TESTING — see xfail reason")


@pytest.mark.xfail(
    reason="api_v1_limiter is disabled under TESTING=true; header emission requires "
    "the live SlowAPIMiddleware path with the limiter enabled.",
    strict=False,
)
def test_headers_on_200():
    """API-08: 2xx responses include X-RateLimit-Limit/Remaining/Reset."""
    raise AssertionError("limiter disabled under TESTING — see xfail reason")


@pytest.mark.xfail(
    reason="api_v1_limiter is disabled under TESTING=true; 429 + Retry-After requires "
    "the live limiter path.",
    strict=False,
)
def test_headers_on_429():
    """API-08: 429 responses include all four headers plus Retry-After."""
    raise AssertionError("limiter disabled under TESTING — see xfail reason")
