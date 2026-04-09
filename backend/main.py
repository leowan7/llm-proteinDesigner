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

# Initialize Sentry error tracking (disabled when sentry_dsn is empty).
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.0,  # No APM for v1
        profiles_sample_rate=0.0,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        environment="development" if settings.debug else "production",
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

# PDB pipeline router (Plan 02-02)
from pdb_utils.router import router as pdb_router
app.include_router(pdb_router)

from agent.router import router as agent_router
app.include_router(agent_router)

from billing.router import router as billing_router
app.include_router(billing_router)

# Job execution and webhook routers (Plan 03-03)
from webhooks.router import router as webhooks_router
app.include_router(webhooks_router)

from jobs.router import router as jobs_router
app.include_router(jobs_router)

from sessions.router import router as sessions_router
app.include_router(sessions_router)

from user.router import router as user_router
app.include_router(user_router)

from admin.router import router as admin_router
app.include_router(admin_router)


@app.get("/health")
async def health():
    """Deep health check — verifies API, database, and Redis connectivity.

    Returns 200 with all checks "ok" when healthy, 503 with error details
    when any dependency is unreachable.
    """
    checks = {"api": "ok"}

    # Check database connectivity.
    try:
        pool = await get_db_pool()
        await pool.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {str(exc)[:100]}"

    # Check Redis connectivity.
    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {str(exc)[:100]}"

    healthy = all(v == "ok" for v in checks.values())
    status_code = 200 if healthy else 503
    return JSONResponse(content=checks, status_code=status_code)
