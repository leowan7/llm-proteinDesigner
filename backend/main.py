"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from auth.router import router as auth_router
from db.connection import close_db_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    yield
    await close_db_pool()


app = FastAPI(
    title="LLM Protein Designer",
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

# Routers
app.include_router(auth_router)

# PDB pipeline router (Plan 02-02)
from pdb_utils.router import router as pdb_router
app.include_router(pdb_router)

from agent.router import router as agent_router
app.include_router(agent_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
