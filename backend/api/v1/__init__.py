"""/api/v1/* router — the public API surface (Phase 13).

The aggregate prefix ``/api/v1`` means the inner routers omit it from their own
prefix. Plan 13-04 will append the api_keys router; this plan stops at jobs.
"""

from fastapi import APIRouter

from api.v1.jobs import router as jobs_router

router = APIRouter(prefix="/api/v1", tags=["api_v1"])
router.include_router(jobs_router)
