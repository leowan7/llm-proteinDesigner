"""Tests for rate limit key extraction middleware.

Covers:
- get_rate_limit_key returns "user:{user_id}" when a valid JWT cookie is present
- get_rate_limit_key returns "ip:{host}" when no cookie is present
- get_rate_limit_key falls back to IP on malformed JWT
"""
import os
os.environ.setdefault("TESTING", "true")

from unittest.mock import MagicMock

import jwt
import pytest

from middleware.rate_limit import get_rate_limit_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(cookies: dict = None, client_host: str = "127.0.0.1"):
    """Build a minimal mock Starlette Request for rate limit key tests.

    Args:
        cookies: Dict of cookie name -> value. Defaults to empty.
        client_host: Client IP to report.

    Returns:
        MagicMock imitating a Starlette Request with .cookies and .client.host.
    """
    request = MagicMock()
    request.cookies = cookies or {}
    request.client = MagicMock()
    request.client.host = client_host
    return request


def _make_jwt(sub: str, secret: str = "test-secret") -> str:
    """Encode a minimal JWT with a 'sub' claim.

    Args:
        sub: The subject (user_id) to embed.
        secret: Signing secret (does not need to match prod secret).

    Returns:
        Encoded JWT string.
    """
    return jwt.encode({"sub": sub}, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_rate_limit_key_with_jwt_cookie():
    """A valid JWT access_token cookie yields 'user:{sub}' as the rate limit key."""
    user_id = "user-uuid-12345"
    token = _make_jwt(user_id)
    request = _make_request(cookies={"access_token": token})

    key = get_rate_limit_key(request)

    assert key == f"user:{user_id}"


def test_rate_limit_key_without_cookie():
    """No access_token cookie yields 'ip:{client_host}' as the rate limit key."""
    request = _make_request(cookies={}, client_host="10.0.0.5")

    key = get_rate_limit_key(request)

    assert key == "ip:10.0.0.5"


def test_rate_limit_key_invalid_jwt():
    """A malformed JWT cookie falls back to 'ip:{client_host}'."""
    request = _make_request(
        cookies={"access_token": "not.a.valid.jwt"},
        client_host="192.168.1.1",
    )

    key = get_rate_limit_key(request)

    assert key == "ip:192.168.1.1"


def test_rate_limit_key_jwt_missing_sub():
    """A valid JWT without a 'sub' claim falls back to client IP.

    The get_rate_limit_key function uses payload.get('sub', request.client.host)
    so a JWT with no sub clause should return the client host.
    """
    # Encode a token with no 'sub' field
    token = jwt.encode({"role": "anon"}, "test-secret", algorithm="HS256")
    request = _make_request(
        cookies={"access_token": token},
        client_host="172.16.0.1",
    )

    key = get_rate_limit_key(request)

    # Without sub, it uses request.client.host as the fallback
    assert key == f"user:172.16.0.1"
