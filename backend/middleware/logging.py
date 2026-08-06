"""Structured JSON logging middleware for HTTP request/response tracking.

Emits one JSON log line per HTTP request to stdout via the ``kendrew.access``
logger. Each line contains: timestamp, method, path, status_code, duration_ms,
client_ip, and user_id (extracted from access_token cookie when present).

Usage:
    from middleware.logging import StructuredLoggingMiddleware, setup_logging

    setup_logging()
    app.add_middleware(StructuredLoggingMiddleware)
"""

import json
import logging
import sys
import time
from datetime import UTC, datetime

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("kendrew.access")


def setup_logging() -> None:
    """Configure the root logger to emit plain messages to stdout.

    Uses ``force=True`` to override any existing logging configuration.
    The StructuredLoggingMiddleware formats its own JSON — the root logger
    just needs to pass the message through without adding its own formatting.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _extract_user_id(request: Request) -> str | None:
    """Extract user_id from the access_token cookie without signature verification.

    This is the same decode-without-verify pattern used in rate_limit.py.
    Logging does not need cryptographic proof — just a stable identifier for
    correlating requests to users in log analysis.

    Args:
        request: The incoming Starlette request.

    Returns:
        The user UUID string if a valid JWT is present, otherwise None.
    """
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = jwt.decode(access_token, options={"verify_signature": False})
            return payload.get("sub")
        except Exception:
            pass
    return None


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that logs each HTTP request as a JSON line.

    Fields emitted per request:
        - timestamp: ISO 8601 UTC timestamp
        - method: HTTP method (GET, POST, etc.)
        - path: Request URL path
        - status_code: HTTP response status code
        - duration_ms: Request processing time in milliseconds (1 decimal)
        - client_ip: Client IP address from request.client.host
        - user_id: Authenticated user UUID or null
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process the request, measure timing, and emit a structured log line."""
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        log_dict = {
            "timestamp": datetime.now(UTC).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": request.client.host if request.client else None,
            "user_id": _extract_user_id(request),
        }

        logger.info(json.dumps(log_dict))

        return response
