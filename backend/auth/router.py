"""Auth endpoints. All authentication routes through FastAPI -- frontend never calls Supabase directly."""

import asyncio
import logging
import time

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from config import settings
from auth.dependencies import get_current_user
from auth.jwks import jwks_verifier
from db.connection import get_db_pool
from middleware.rate_limit import limiter

from supabase import create_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_supabase():
    """Create a Supabase client for auth operations."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    tos_version: str  # Must match settings.tos_current_version


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr


class UpdatePasswordRequest(BaseModel):
    password: str


class ExchangeTokenRequest(BaseModel):
    access_token: str
    refresh_token: str


def _set_auth_cookies(response: Response, session) -> None:
    """Set access_token and refresh_token as HTTP-only cookies."""
    response.set_cookie(
        key="access_token",
        value=session.access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=session.expires_in,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=session.refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
        path="/auth/refresh",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Clear both auth cookies."""
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/auth/refresh")


@router.post("/signup")
@limiter.limit("3/minute;10/hour")
async def signup(request: Request, body: SignUpRequest, response: Response):
    """AUTH-01: Create account with email and password via Supabase Auth.

    Plan 10-02: Rejects requests whose `tos_version` does not match
    `settings.tos_current_version`, and persists the acceptance (timestamp +
    version) in ``public.users`` immediately after Supabase sign-up succeeds.
    """
    # Reject stale / spoofed ToS versions BEFORE touching Supabase. The backend
    # constant is the source of truth; frontend always sends the compiled
    # TOS_VERSION from versions.ts.
    if body.tos_version != settings.tos_current_version:
        raise HTTPException(
            status_code=400,
            detail="Terms of Service version mismatch; refresh and try again.",
        )

    try:
        supabase = _get_supabase()
        result = supabase.auth.sign_up({"email": body.email, "password": body.password})
        if result.user is None:
            raise HTTPException(status_code=400, detail="Signup failed")
    except HTTPException:
        raise
    except Exception as exc:
        error_msg = str(exc)
        if "already registered" in error_msg.lower() or "already been registered" in error_msg.lower():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=400, detail=f"Signup failed: {error_msg}")

    # Record ToS acceptance against public.users. A database trigger creates
    # the row from auth.users; on rare races we may arrive before the trigger,
    # so retry once after a short sleep.
    new_user_id = result.user.id
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            update_result = await conn.execute(
                """UPDATE public.users
                   SET tos_accepted_at = now(),
                       tos_version = $2,
                       updated_at = now()
                   WHERE id = $1""",
                new_user_id,
                body.tos_version,
            )
            if update_result == "UPDATE 0":
                # Trigger race — wait briefly, then upsert.
                await asyncio.sleep(0.2)
                await conn.execute(
                    """INSERT INTO public.users (id, email, tos_accepted_at, tos_version)
                       VALUES ($1, $2, now(), $3)
                       ON CONFLICT (id) DO UPDATE
                           SET tos_accepted_at = EXCLUDED.tos_accepted_at,
                               tos_version     = EXCLUDED.tos_version,
                               updated_at      = now()""",
                    new_user_id,
                    body.email,
                    body.tos_version,
                )
    except Exception as exc:  # pragma: no cover - persistence should not fail signup UX
        logger.warning(
            "Signup succeeded for user %s but ToS acceptance write failed: %s",
            new_user_id,
            exc,
        )

    # With email verification enabled, no session is returned until email is confirmed
    return {"message": "Account created. Check your email for a verification link."}


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest, response: Response):
    """AUTH-04: Authenticate and set HTTP-only cookies."""
    try:
        supabase = _get_supabase()
        result = supabase.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
        if result.session is None:
            raise HTTPException(
                status_code=401,
                detail="Verify your email before signing in.",
            )
        _set_auth_cookies(response, result.session)
        return {"message": "Logged in", "user_id": result.user.id}
    except HTTPException:
        raise
    except Exception as exc:
        error_msg = str(exc)
        if "invalid" in error_msg.lower() or "credentials" in error_msg.lower():
            raise HTTPException(status_code=401, detail="Incorrect email or password.")
        if "not confirmed" in error_msg.lower() or "email" in error_msg.lower():
            raise HTTPException(
                status_code=403,
                detail="Verify your email before signing in.",
            )
        raise HTTPException(status_code=401, detail="Incorrect email or password.")


@router.post("/logout")
async def logout(response: Response):
    """Clear auth cookies."""
    _clear_auth_cookies(response)
    return {"message": "Logged out"}


@router.post("/refresh")
async def refresh(response: Response, refresh_token: str | None = Cookie(default=None)):
    """AUTH-04: Refresh the access token using the refresh_token cookie."""
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        supabase = _get_supabase()
        result = supabase.auth.refresh_session(refresh_token)
        if result.session is None:
            raise HTTPException(status_code=401, detail="Refresh failed")
        _set_auth_cookies(response, result.session)
        return {"message": "Token refreshed"}
    except HTTPException:
        raise
    except Exception:
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh failed. Please log in again.")


@router.post("/reset-password")
@limiter.limit("3/minute")
async def reset_password(request: Request, body: ResetPasswordRequest):
    """AUTH-03: Send password reset email via Supabase Auth."""
    try:
        supabase = _get_supabase()
        supabase.auth.reset_password_for_email(
            body.email,
            {"redirect_to": f"{settings.cors_origins[0]}/reset-password/confirm"},
        )
        return {"message": "If an account with that email exists, a reset link has been sent."}
    except Exception:
        # Do not reveal whether the email exists
        return {"message": "If an account with that email exists, a reset link has been sent."}


@router.post("/exchange-token")
async def exchange_token(body: ExchangeTokenRequest, response: Response):
    """AUTH-03: Exchange recovery tokens from URL hash for HTTP-only cookies.

    After clicking a password reset link, Supabase redirects to the frontend with
    access_token and refresh_token in the URL hash fragment. The frontend cannot
    set HTTP-only cookies, so it calls this endpoint to exchange the tokens.
    The backend sets them as HTTP-only cookies, making /auth/update-password work.

    WR-05: verify the token signature (dual-algorithm: ES256 via the project's
    JWKS, falling back to HS256 against ``settings.supabase_jwt_secret`` only
    when the token's header itself claims HS256 and the legacy secret is set —
    see Phase 11 Plan 04 sub-plan). Cookie ``max_age`` derives from the token's
    ``exp`` claim rather than hardcoding 3600 seconds. Supabase recovery tokens
    are typically short-lived (e.g. 5 minutes) — a 60-minute cookie would
    extend the window the recovery session remains active on the device.
    """
    try:
        # Delegate to the shared JWKS verifier. ``ExpiredSignatureError`` and
        # ``InvalidTokenError`` are both subclasses of ``jwt.PyJWTError`` and
        # are caught explicitly below to surface different user-facing messages.
        payload = await jwks_verifier.verify(body.access_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Recovery token has expired; request a new reset link.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid recovery token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid recovery token")

    # Derive max_age from exp so the cookie lifetime tracks the token lifetime.
    # Clamp at 0 so an already-expired token never produces a negative max_age.
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise HTTPException(status_code=401, detail="Invalid recovery token")
    exp_seconds = max(0, int(exp - time.time()))
    if exp_seconds == 0:
        raise HTTPException(status_code=401, detail="Recovery token has expired; request a new reset link.")

    response.set_cookie(
        key="access_token",
        value=body.access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=exp_seconds,
        path="/",
    )
    return {"message": "Token exchanged"}


@router.post("/update-password")
@limiter.limit("5/minute")
async def update_password(
    request: Request,  # required by slowapi to extract the rate-limit key
    body: UpdatePasswordRequest,
    response: Response,
    access_token: str | None = Cookie(default=None),
):
    """AUTH-03: Set new password after clicking reset link. Requires valid access token from exchange-token endpoint.

    Rate limit (WR-04): 5/minute to throttle recovery-flow brute-forcing in
    line with /auth/login (5/minute) and /auth/reset-password (3/minute).
    The global CSRF middleware applies on top of the rate limit.
    """
    if access_token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        supabase = _get_supabase()
        supabase.auth.set_session(access_token, "")
        supabase.auth.update_user({"password": body.password})
        _clear_auth_cookies(response)
        return {"message": "Password updated. Please sign in with your new password."}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Password update failed: {str(exc)}")


@router.get("/me")
async def get_me(user_id: str = Depends(get_current_user), access_token: str | None = Cookie(default=None)):
    """Return current user's ID and email. Validates JWT from cookie."""
    email = None
    if access_token:
        try:
            payload = jwt.decode(access_token, options={"verify_signature": False})
            email = payload.get("email")
        except Exception:
            pass
    return {"user_id": user_id, "email": email}
