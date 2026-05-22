"""FastAPI dependencies for authentication."""

import jwt
from fastapi import Cookie, HTTPException, status

from auth.jwks import jwks_verifier


async def get_current_user(access_token: str | None = Cookie(default=None)) -> str:
    """
    FastAPI dependency that validates a Supabase JWT from an HTTP-only cookie.

    Reads the access_token from the HTTP-only cookie set by the login endpoint.
    Verification is delegated to ``auth.jwks.SupabaseJWKSVerifier`` which does
    dual-algorithm verification: ES256 via the project's JWKS for asymmetric
    tokens (the post-2026-04 default), HS256 against ``settings.supabase_jwt_secret``
    when the token's header explicitly claims HS256 and the legacy secret is
    still configured. Algorithms are pinned per code path; the two paths are
    NEVER combined in one ``jwt.decode`` call (algorithm-confusion mitigation —
    see ``auth/jwks.py`` module docstring and Phase 11 Plan 04 sub-plan).

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
        payload = await jwks_verifier.verify(access_token)
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
