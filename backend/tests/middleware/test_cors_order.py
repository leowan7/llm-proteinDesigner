"""CORS must wrap CSRF and rate limiting so their rejections carry CORS headers."""
import os

os.environ.setdefault("TESTING", "true")

from config import settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from main import CSRF_REJECTED_DETAIL, ReadableCSRFMiddleware, install_middleware
from middleware.logging import StructuredLoggingMiddleware
from slowapi.middleware import SlowAPIMiddleware


def _app_with_production_middleware() -> FastAPI:
    app = FastAPI()

    @app.post("/probe")
    async def probe():
        return {"ok": True}

    install_middleware(app, csrf=True, rate_limit=True)
    return app


def test_middleware_order_outermost_first():
    assert [m.cls for m in _app_with_production_middleware().user_middleware] == [
        StructuredLoggingMiddleware,
        CORSMiddleware,
        SlowAPIMiddleware,
        ReadableCSRFMiddleware,
    ]


async def test_csrf_rejection_is_readable_only_by_allowed_origins():
    allowed = settings.cors_origins[0]
    transport = ASGITransport(app=_app_with_production_middleware())
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"access_token": "x"}
    ) as client:
        from_allowed = await client.post("/probe", headers={"Origin": allowed})
        from_other = await client.post("/probe", headers={"Origin": "https://attacker.example"})

    assert from_allowed.status_code == 403
    assert from_allowed.json() == {"detail": CSRF_REJECTED_DETAIL}
    assert from_allowed.headers["access-control-allow-origin"] == allowed
    assert from_other.status_code == 403
    assert "access-control-allow-origin" not in from_other.headers
