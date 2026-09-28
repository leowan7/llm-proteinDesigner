"""Customer-facing error details from /auth/update-password, /auth/signup and /auth/login.

The Supabase client is stubbed by patching ``auth.router._get_supabase``.
"""
import logging
import os

os.environ.setdefault("TESTING", "true")

from unittest.mock import MagicMock, patch

import pytest
from auth.router import RESET_LINK_INVALID
from config import settings
from httpx import ASGITransport, AsyncClient
from main import app
from middleware.rate_limit import limiter as _limiter
from supabase_auth.errors import (
    AuthApiError,
    AuthRetryableError,
    AuthSessionMissingError,
    AuthWeakPasswordError,
)

_limiter.enabled = False

NEW_PASSWORD = "N3w-password!"
SUPABASE_INTERNALS = RuntimeError(
    "Client error '500 Internal Server Error' for url 'https://project-ref.supabase.co/auth/v1/user'"
)


async def _post(path: str, body: dict, cookies: dict | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
        return await client.post(path, json=body)


async def _update_password(supabase: MagicMock):
    with patch("auth.router._get_supabase", return_value=supabase):
        return await _post(
            "/auth/update-password", {"password": NEW_PASSWORD}, {"access_token": "recovery-jwt"}
        )


async def test_update_password_success_clears_auth_cookies():
    supabase = MagicMock()
    response = await _update_password(supabase)

    assert response.status_code == 200
    supabase.auth.set_session.assert_called_once_with("recovery-jwt", "")
    supabase.auth.update_user.assert_called_once_with({"password": NEW_PASSWORD})
    assert any(
        c.startswith("access_token=") and "Max-Age=0" in c
        for c in response.headers.get_list("set-cookie")
    )


@pytest.mark.parametrize(
    "refusal",
    [
        AuthWeakPasswordError("Password should be at least 10 characters.", 422, ["length"]),
        AuthApiError("New password should be different from the old password.", 422, "same_password"),
    ],
)
async def test_update_password_refused_by_supabase_returns_its_reason(refusal):
    supabase = MagicMock()
    supabase.auth.update_user.side_effect = refusal
    response = await _update_password(supabase)

    assert response.status_code == 400
    assert response.json() == {"detail": refusal.message}


async def test_update_password_without_cookie_is_401():
    with patch("auth.router._get_supabase") as get_supabase:
        response = await _post("/auth/update-password", {"password": NEW_PASSWORD})

    assert response.status_code == 401
    assert response.json() == {"detail": RESET_LINK_INVALID}
    get_supabase.assert_not_called()


async def test_update_password_expired_token_is_401():
    supabase = MagicMock()
    supabase.auth.set_session.side_effect = AuthSessionMissingError()
    response = await _update_password(supabase)

    assert response.status_code == 401
    assert response.json() == {"detail": RESET_LINK_INVALID}


async def test_update_password_unexpected_error_hides_internals():
    supabase = MagicMock()
    supabase.auth.update_user.side_effect = SUPABASE_INTERNALS
    response = await _update_password(supabase)

    assert response.status_code == 400
    assert "supabase" not in response.json()["detail"].lower()


async def _signup(error: Exception):
    supabase = MagicMock()
    supabase.auth.sign_up.side_effect = error
    with patch("auth.router._get_supabase", return_value=supabase):
        return await _post(
            "/auth/signup",
            {
                "email": "new@example.com",
                "password": NEW_PASSWORD,
                "tos_version": settings.tos_current_version,
            },
        )


@pytest.mark.parametrize(
    "refusal",
    [
        AuthWeakPasswordError("Password should be at least 10 characters.", 422, ["length"]),
        AuthApiError('Email address "new@example.com" is invalid', 400, "email_address_invalid"),
    ],
)
async def test_signup_refused_by_supabase_returns_its_reason(refusal):
    response = await _signup(refusal)

    assert response.status_code == 400
    assert response.json() == {"detail": refusal.message}


async def test_signup_unexpected_error_hides_internals():
    response = await _signup(SUPABASE_INTERNALS)

    assert response.status_code == 400
    assert "supabase" not in response.json()["detail"].lower()


LOGIN_EMAIL = "someone@example.com"
LOGIN_PASSWORD = "Wr0ng-password!"
SIGN_IN_FAILED = "We could not sign you in right now. Try again in a moment."


async def _login(supabase: MagicMock):
    with patch("auth.router._get_supabase", return_value=supabase):
        return await _post("/auth/login", {"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD})


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (AuthApiError("Invalid login credentials", 400, "invalid_credentials"), 401, "Incorrect email or password."),
        (AuthApiError("Email not confirmed", 400, "email_not_confirmed"), 403, "Verify your email before signing in."),
        (AuthApiError("Email rate limit exceeded", 429, "over_email_send_rate_limit"), 400, SIGN_IN_FAILED),
        (AuthRetryableError("Server disconnected", 0), 400, SIGN_IN_FAILED),
        (RuntimeError(f"unexpected reply for {LOGIN_EMAIL}"), 400, SIGN_IN_FAILED),
    ],
)
async def test_login_refusal_names_its_cause_and_logs_no_credentials(error, status, detail, caplog):
    supabase = MagicMock()
    supabase.auth.sign_in_with_password.side_effect = error
    with caplog.at_level(logging.WARNING, logger="auth.router"):
        response = await _login(supabase)

    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert f"{type(error).__name__} code={getattr(error, 'code', None)}" in caplog.text
    assert LOGIN_EMAIL not in caplog.text
    assert LOGIN_PASSWORD not in caplog.text


async def test_login_without_a_session_is_logged():
    supabase = MagicMock()
    supabase.auth.sign_in_with_password.return_value = MagicMock(session=None)
    with patch("auth.router.logger") as logger:
        response = await _login(supabase)

    assert response.status_code == 401
    logger.warning.assert_called_once_with("Login refused: no session returned")
