"""Dev-only debug routes. Imported into main.py only when settings.debug is true.

Used by Phase 11 SC 8 validation: a synthetic error that Sentry captures to
prove error tracking is wired. Never exposed in production builds.
"""

from config import settings
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/sentry-test")
async def sentry_test():
    """Raise a synthetic ZeroDivisionError so Sentry captures it.

    Guarded on settings.debug -- in prod (debug=false) this returns 404.
    """
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")
    # Intentional unhandled exception -- Sentry should capture this.
    1 / 0
    return {"unreachable": True}
