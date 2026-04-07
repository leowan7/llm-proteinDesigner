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
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.redis_url,
    enabled=settings.rate_limit_enabled,
)


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
