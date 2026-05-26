"""Integration tests for auth endpoints.

Requires: supabase start (local Supabase stack running)
Test user: test@example.com / Password123! (created by seed.sql)
"""

import pytest
from tests.conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD


@pytest.mark.anyio
async def test_health(client):
    """Health endpoint returns a structured deep-check payload.

    Returns 200 when all dependencies (api, db, redis) are healthy; returns
    503 when any dependency is unreachable. The api check is always expected
    to pass; db and redis may be unavailable in unit-test environments that
    only bring up Supabase without Redis.
    """
    response = await client.get("/health")
    assert response.status_code in (200, 503)
    body = response.json()
    assert body.get("api") == "ok", f"api check must always be ok, got {body}"
    assert "db" in body and "redis" in body, f"response missing expected keys: {body}"


@pytest.mark.anyio
async def test_me_without_cookie(client):
    """AUTH-04: /auth/me returns 401 without access_token cookie."""
    response = await client.get("/auth/me")
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.xfail(reason="needs a live Supabase auth user; CI runs against ephemeral `supabase start` instance without seeded TEST_USER credentials", strict=False)
async def test_login_returns_cookies(client):
    """AUTH-04: Login with valid credentials sets HTTP-only cookies."""
    response = await client.post(
        "/auth/login",
        json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
    )
    assert response.status_code == 200
    assert "access_token" in response.headers.get("set-cookie", "").lower()


@pytest.mark.anyio
@pytest.mark.xfail(reason="needs a live Supabase auth user; CI runs against ephemeral `supabase start` instance without seeded TEST_USER credentials", strict=False)
async def test_login_then_me(client):
    """AUTH-04: After login, /auth/me returns the user_id."""
    login_response = await client.post(
        "/auth/login",
        json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
    )
    assert login_response.status_code == 200

    # Extract cookies from login response and send them with /me request
    cookies = {}
    for header_value in login_response.headers.get_list("set-cookie"):
        if "access_token=" in header_value:
            token = header_value.split("access_token=")[1].split(";")[0]
            cookies["access_token"] = token

    me_response = await client.get("/auth/me", cookies=cookies)
    assert me_response.status_code == 200
    assert "user_id" in me_response.json()


@pytest.mark.anyio
async def test_logout_clears_cookies(client):
    """Logout endpoint clears auth cookies."""
    response = await client.post("/auth/logout")
    assert response.status_code == 200
    # Check that Set-Cookie headers clear the tokens
    set_cookies = response.headers.get_list("set-cookie")
    cookie_str = " ".join(set_cookies).lower()
    assert "access_token" in cookie_str


@pytest.mark.anyio
async def test_reset_password_does_not_reveal_email(client):
    """AUTH-03: Reset password returns same message regardless of email existence."""
    response = await client.post(
        "/auth/reset-password",
        json={"email": "nonexistent@example.com"},
    )
    assert response.status_code == 200
    assert "reset link" in response.json()["message"].lower()


@pytest.mark.anyio
async def test_login_invalid_credentials(client):
    """Login with wrong password returns 401."""
    response = await client.post(
        "/auth/login",
        json={"email": TEST_USER_EMAIL, "password": "wrongpassword"},
    )
    assert response.status_code == 401
