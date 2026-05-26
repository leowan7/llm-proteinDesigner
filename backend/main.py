"""FastAPI application entry point."""

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from config import settings
from auth.router import router as auth_router
from db.connection import close_db_pool, get_db_pool
from middleware.logging import StructuredLoggingMiddleware, setup_logging
from pdb_utils.router import router as pdb_router
from agent.router import router as agent_router
from billing.router import router as billing_router
from webhooks.router import router as webhooks_router
from jobs.router import router as jobs_router
from sessions.router import router as sessions_router
from user.router import router as user_router
from admin.router import router as admin_router

# Initialize Sentry error tracking (disabled when sentry_dsn is empty).
# Hot-path Performance sampling per Phase 11 D-14: sample 100% of the 5 routes
# that actually cost money or user-facing latency; 0% of everything else to
# stay inside the free-tier quota.
_HOT_PATHS = {
    "POST /agent/message",
    "POST /jobs/launch",
    "POST /webhooks/runpod",
    "POST /webhooks/heartbeat",
    "POST /jobs/{job_id}/upload-urls",
}


def _traces_sampler(sampling_context: dict) -> float:
    """Sentry traces_sampler per Phase 11 D-14.

    Sample 100% of the 5 hot paths that actually cost money or user-facing
    latency; 0% of everything else to stay within the free-tier quota.
    """
    txn_ctx = sampling_context.get("transaction_context", {}) or {}
    name = txn_ctx.get("name", "") or ""
    return 1.0 if name in _HOT_PATHS else 0.0


if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sampler=_traces_sampler,
        profiles_sample_rate=0.0,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        environment=("production" if not settings.debug else "development"),
    )

# Configure structured JSON logging to stdout.
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    yield
    await close_db_pool()


app = FastAPI(
    title="Kendrew.AI",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS -- must come before CSRF middleware
# allow_credentials=True required for cookie-based auth
# Cannot use ["*"] with allow_credentials=True
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CSRF -- double-submit cookie pattern
# Only register when NOT in test mode; tests cannot perform the double-submit flow
if not settings.testing:
    from starlette_csrf import CSRFMiddleware

    app.add_middleware(
        CSRFMiddleware,
        secret=settings.csrf_secret,
        sensitive_cookies={"access_token", "refresh_token"},
        cookie_samesite="lax",
        cookie_secure=settings.cookie_secure,
    )

# Rate limiting — after CORS, before routers
if settings.rate_limit_enabled:
    from middleware.rate_limit import setup_rate_limiting
    setup_rate_limiting(app)

# Structured logging — added last so it wraps all other middleware (outermost in Starlette).
app.add_middleware(StructuredLoggingMiddleware)

# Routers
app.include_router(auth_router)
app.include_router(pdb_router)
app.include_router(agent_router)
app.include_router(billing_router)
app.include_router(webhooks_router)
app.include_router(jobs_router)
app.include_router(sessions_router)
app.include_router(user_router)
app.include_router(admin_router)

# Phase 11 SC 8: synthetic-error endpoint for Sentry verification (dev only).
if settings.debug or settings.testing:
    from debug_routes import router as debug_router
    app.include_router(debug_router)


import logging as _stdlogging
_health_logger = _stdlogging.getLogger("kendrew.health")


@app.get("/health")
async def health():
    """Deep health check — verifies API, database, and Redis connectivity.

    Returns 200 with all checks "ok" when healthy, 503 with error details
    when any dependency is unreachable. Failure type+message is logged at
    WARNING so deploy-time diagnostics surface in Railway logs.
    """
    checks = {"api": "ok"}

    try:
        pool = await get_db_pool()
        await pool.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {str(exc)[:200]}"
        _health_logger.warning("health.db_failed type=%s msg=%s", type(exc).__name__, str(exc)[:500])

    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {str(exc)[:200]}"
        _health_logger.warning("health.redis_failed type=%s msg=%s", type(exc).__name__, str(exc)[:500])

    healthy = all(v == "ok" for v in checks.values())
    status_code = 200 if healthy else 503
    return JSONResponse(content=checks, status_code=status_code)
