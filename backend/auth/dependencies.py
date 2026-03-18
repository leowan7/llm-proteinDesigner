"""FastAPI dependencies for authentication."""

import jwt
from fastapi import Cookie, HTTPException, status

from config import settings


async def get_current_user(access_token: str | None = Cookie(default=None)) -> str:
    """
    FastAPI dependency that validates a Supabase JWT from an HTTP-only cookie.

    Reads the access_token from the HTTP-only cookie set by the login endpoint.
    Validates using PyJWT with HS256 against the Supabase JWT secret.
    Returns the Supabase user UUID (sub claim).

    Raises:
        HTTPException 401: If cookie is missing, token is expired, or token is invalid.
    """
    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = jwt.decode(
            access_token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
