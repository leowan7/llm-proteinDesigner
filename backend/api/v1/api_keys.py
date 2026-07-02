"""Public /api/v1/api-keys surface — SDK-side key self-management (Phase 13).

Two endpoints (mirrors the org-scoped + role-guard shape of api/v1/jobs.py):
  GET  /api/v1/api-keys                 — list the caller's org keys (revoked excluded)
  POST /api/v1/api-keys/{key_id}/revoke — org-scoped self-revoke

Auth: ``require_role_api(...)`` returns the caller's ``org_id`` (from the Bearer
API key) and writes ``request.state.api_key_id`` so ``api_v1_limiter`` keys the
60/min bucket per API key. RFC 7807 problem+json envelopes come from the
app-level handlers registered in main.py (api.v1.errors).

Note the actual ``public.org_role`` enum is ``owner | scientist | viewer``
(migration 20260605000001). The revoke endpoint restricts to ``owner`` (the
privileged role) since there is no ``admin`` role in this codebase.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status

from auth.api_key_dependencies import require_role_api
from config import settings
from db.connection import get_db_pool
from middleware.rate_limit import api_v1_limiter

router = APIRouter(prefix="/api-keys", tags=["api_v1_api_keys"])

# public.org_role is (owner, scientist, viewer). Listing is open to any member;
# revoke is restricted to the privileged role (owner) — no "admin" exists here.
_LIST_ROLES = ("owner", "scientist", "viewer")
_REVOKE_ROLES = ("owner",)


@router.get("/")
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def list_keys(
    request: Request,
    org_id: str = Depends(require_role_api(*_LIST_ROLES)),
):
    """GET /api/v1/api-keys — org-scoped list of active keys (API-03)."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, prefix, created_at, last_used_at
               FROM public.api_keys
               WHERE organization_id = $1 AND revoked_at IS NULL
               ORDER BY created_at DESC""",
            org_id,
        )
    return {
        "data": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "prefix": r["prefix"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            }
            for r in rows
        ]
    }


@router.post("/{key_id}/revoke")
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def revoke_key(
    request: Request,
    key_id: str,
    org_id: str = Depends(require_role_api(*_REVOKE_ROLES)),
):
    """POST /api/v1/api-keys/{key_id}/revoke — org-scoped self-revoke (API-03).

    404 (not 403) on a key that belongs to a different org, to avoid existence
    disclosure. Second revoke is a no-op (``revoked_at IS NULL`` guard) → 404.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE public.api_keys
               SET revoked_at = now()
               WHERE id = $1 AND organization_id = $2 AND revoked_at IS NULL""",
            key_id,
            org_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
    return {
        "id": key_id,
        "revoked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
