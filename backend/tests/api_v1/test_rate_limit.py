"""Integration tests for per-API-key rate limiting on /api/v1/* routes.

Requirements: API-08 (X-RateLimit-* headers on every 200 + Retry-After on 429).
             API-10 (60 rpm per api_keys.id via separate api_v1_limiter instance).
Downstream plan: Plan 13-03 ships api_v1_limiter in middleware/rate_limit.py
                 and applies @api_v1_limiter.limit(...) on all v1 routes.
"""

import pytest


def test_60rpm():
    """API-10: The 61st request within 60 seconds from the same API key returns 429.

    The rate limit is keyed on api_keys.id (not IP), so different keys get separate
    60 rpm buckets. Uses the separate api_v1_limiter instance (RESEARCH §2.4).
    """
    pytest.skip("Pending: Plan 13-03 ships api_v1_limiter + @api_v1_limiter.limit('60/minute') on v1 routes")


def test_headers_on_200():
    """API-08: Successful /api/v1/* responses include X-RateLimit-Limit,
    X-RateLimit-Remaining, X-RateLimit-Reset (unix epoch seconds) headers.

    headers_enabled=True on api_v1_limiter causes slowapi to emit these on every
    response. RESEARCH §2.4 recommends unix epoch seconds for Reset (GitHub/Stripe style).
    """
    pytest.skip("Pending: Plan 13-03 ships api_v1_limiter with headers_enabled=True")


def test_headers_on_429():
    """API-08: 429 responses additionally include Retry-After (seconds, RFC 7231 §7.1.3).

    The Retry-After header contains integer seconds until the rate-limit window resets.
    """
    pytest.skip("Pending: Plan 13-03 ships api_v1_limiter + 429 response shape")
