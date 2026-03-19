"""Auth endpoints. All authentication routes through FastAPI -- frontend never calls Supabase directly."""

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from config import settings
from auth.dependencies import get_current_user

from supabase import create_client

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_supabase():
    """Create a Supabase client for auth operations."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str


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
async def signup(body: SignUpRequest, response: Response):
    """AUTH-01: Create account with email and password via Supabase Auth."""
    try:
        supabase = _get_supabase()
        result = supabase.auth.sign_up({"email": body.email, "password": body.password})
        if result.user is None:
            raise HTTPException(status_code=400, detail="Signup failed")
        # With email verification enabled, no session is returned until email is confirmed
        return {"message": "Account created. Check your email for a verification link."}
    except Exception as exc:
        error_msg = str(exc)
        if "already registered" in error_msg.lower() or "already been registered" in error_msg.lower():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=400, detail=f"Signup failed: {error_msg}")


@router.post("/login")
async def login(body: LoginRequest, response: Response):
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
async def reset_password(body: ResetPasswordRequest):
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
    """
    try:
        # Basic structure check — decode without verification since the token
        # comes from Supabase's own redirect and will be validated by Supabase
        # when used for the password update call.
        jwt.decode(
            body.access_token,
            options={"verify_signature": False},
            audience="authenticated",
        )
        # Set the access_token as an HTTP-only cookie
        response.set_cookie(
            key="access_token",
            value=body.access_token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=3600,
            path="/",
        )
        return {"message": "Token exchanged"}
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid recovery token")


@router.post("/update-password")
async def update_password(
    body: UpdatePasswordRequest,
    response: Response,
    access_token: str | None = Cookie(default=None),
):
    """AUTH-03: Set new password after clicking reset link. Requires valid access token from exchange-token endpoint."""
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
async def get_me(user_id: str = Depends(get_current_user)):
    """Return current user's ID. Validates JWT from cookie."""
    return {"user_id": user_id}
