"""/api/v1/* router — the public API surface (Phase 13).

The aggregate prefix ``/api/v1`` means the inner routers omit it from their own
prefix. Plan 13-04 appends the api_keys router alongside jobs.
"""

from fastapi import APIRouter

from api.v1.api_keys import router as api_keys_router
from api.v1.jobs import router as jobs_router

router = APIRouter(prefix="/api/v1", tags=["api_v1"])
router.include_router(jobs_router)
router.include_router(api_keys_router)
