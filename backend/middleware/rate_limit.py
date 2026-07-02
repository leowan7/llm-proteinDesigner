"""Rate limiting middleware using slowapi with Redis backend.

Provides per-user (authenticated) and per-IP (unauthenticated) rate limiting.
Rate limit keys extract user_id from the access_token cookie when present,
falling back to client IP for unauthenticated requests.

Usage:
    from middleware.rate_limit import limiter, setup_rate_limiting

    # In main.py:
    setup_rate_limiting(app)

    # In routers:
    @router.post("/endpoint")
    @limiter.limit("5/minute")
    async def endpoint(request: Request):
        ...
"""

import jwt
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import settings


def get_rate_limit_key(request: Request) -> str:
    """Extract a rate-limit key from the request.

    For authenticated requests, the key is based on the user_id from the
    access_token cookie (decoded without signature verification — rate limiting
    does not need cryptographic proof, just a stable identifier). For
    unauthenticated requests, falls back to the client IP address.

    Args:
        request: The incoming FastAPI/Starlette request.

    Returns:
        A string key like "user:<uuid>" or "ip:<address>".
    """
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = jwt.decode(access_token, options={"verify_signature": False})
            return f"user:{payload.get('sub', request.client.host)}"
        except Exception:
            pass
    return f"ip:{request.client.host}"


# Create the limiter instance with Redis storage and a global fallback limit.
# Rate limiting is disabled under TESTING=true so unit tests do not depend on Redis.
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.redis_url,
    enabled=settings.rate_limit_enabled and not settings.testing,
)


def get_api_key_id(request: Request) -> str:
    """Key func for api_v1_limiter — reads api_keys.id from request.state.

    ``auth.api_key_dependencies.get_current_api_key`` sets
    ``request.state.api_key_id`` after a successful verify (Phase 13, Plan 13-02).
    If missing (e.g. an unauthenticated request that reached a v1 path), fall
    back to the client IP so anonymous traffic is still rate-limited.

    Returns a key like ``apikey:<uuid>`` or ``ip:<address>``.
    """
    api_key_id = getattr(request.state, "api_key_id", None)
    if api_key_id:
        return f"apikey:{api_key_id}"
    return f"ip:{request.client.host}"


# Phase 13 (RESEARCH §2.4 / §5.4): dedicated per-API-key limiter for /api/v1/*.
# headers_enabled=True emits X-RateLimit-* headers. No default_limits and NO
# second SlowAPIMiddleware — the route decorator @api_v1_limiter.limit(...) is
# the only application path, which avoids the slowapi double-headers bug (#33).
api_v1_limiter = Limiter(
    key_func=get_api_key_id,
    storage_uri=settings.redis_url,
    headers_enabled=True,
    enabled=settings.rate_limit_enabled and not settings.testing,
)

# slowapi stores the flag privately as ``_headers_enabled`` and exposes no public
# ``headers_enabled`` attribute in the installed version. Mirror the private flag
# to a public one so callers (and the plan's acceptance check) can assert
# ``api_v1_limiter.headers_enabled is True`` regardless of slowapi internals.
api_v1_limiter.headers_enabled = api_v1_limiter._headers_enabled


def setup_rate_limiting(app) -> None:
    """Wire slowapi rate limiting into the FastAPI application.

    Sets app.state.limiter (required by SlowAPIMiddleware), adds the middleware,
    and registers the custom exception handler for 429 responses.

    Args:
        app: The FastAPI application instance.
    """
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
