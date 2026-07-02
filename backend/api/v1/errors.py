"""RFC 7807 (application/problem+json) exception handlers, scoped to /api/v1/*.

App-level handlers with a ``request.url.path.startswith("/api/v1/")`` gate
(RESEARCH §2.7). Non-v1 paths fall through to the FastAPI defaults so the web
flow keeps its existing ``{"detail": "..."}`` shape.

Per-endpoint problem-type override: an ``X-Bindwave-Problem-Type`` header on the
raised ``HTTPException``'s ``headers`` dict, when present, is used as the type
slug instead of the status-code default. This lets the jobs router distinguish
409 idempotency-in-progress from a generic 409, and 422 idempotency-key-conflict
from a generic 422.
"""

from fastapi import Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_TYPE_BASE = "https://bindwave.com/errors/"

# Slugify status codes for the type URL. Extend as new HTTPException details appear.
_TYPE_SLUGS = {
    400: "bad-request",
    401: "unauthorized",
    402: "payment-required",
    403: "forbidden",
    404: "not-found",
    409: "conflict",
    422: "unprocessable-entity",
    429: "too-many-requests",
    500: "internal-server-error",
}
_TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    402: "Payment required",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Unprocessable entity",
    429: "Too many requests",
    500: "Internal server error",
}


def _slug_for_status(status_code: int) -> str:
    return _TYPE_SLUGS.get(status_code, "error")


def _title_for_status(status_code: int) -> str:
    return _TITLES.get(status_code, "Error")


async def http_exception_handler(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/api/v1/"):
        from fastapi.exception_handlers import http_exception_handler as default

        return await default(request, exc)

    # Per-endpoint override: honour X-Bindwave-Problem-Type from exc.headers.
    override_slug = None
    if exc.headers:
        override_slug = exc.headers.get("X-Bindwave-Problem-Type")
    type_slug = override_slug or _slug_for_status(exc.status_code)

    return JSONResponse(
        status_code=exc.status_code,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_TYPE_BASE}{type_slug}",
            "title": _title_for_status(exc.status_code),
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": request.url.path,
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/v1/"):
        from fastapi.exception_handlers import (
            request_validation_exception_handler as default,
        )

        return await default(request, exc)
    return JSONResponse(
        status_code=422,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_TYPE_BASE}validation-error",
            "title": "Validation error",
            "status": 422,
            "detail": "One or more fields failed validation.",
            "instance": request.url.path,
            "errors": [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        },
    )
