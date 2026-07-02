"""Web-flow API-key CRUD — what the Settings page (Plan 13-06) calls.

Three endpoints under ``/user/api-keys`` (WEB auth path per RESEARCH §5.5: cookie
JWT + ``X-Org-Id`` header + ``require_role``, NOT the Bearer API-key path):
  GET  /user/api-keys                 — list the caller's org keys (revoked excluded)
  POST /user/api-keys                 — mint a key; plaintext shown EXACTLY ONCE
  POST /user/api-keys/{key_id}/revoke — org-scoped revoke

Mint-once invariant (API-01): the POST response is the ONLY place the plaintext
key crosses the network. The DB stores only the HMAC-SHA256 hex digest; GET never
returns plaintext. Mirrors the auth/router.py signup mint-once shape.

D-15 hidden-router invariant: this APIRouter is constructed with
``include_in_schema=False`` DIRECTLY. FastAPI does NOT inherit the flag from the
parent /user router, so it must be set here for /user/api-keys to stay out of the
published /api/openapi.json spec.

Note the actual ``public.org_role`` enum is ``owner | scientist | viewer``
(migration 20260605000001). Revoke is restricted to ``owner`` — there is no
``admin`` role in this codebase.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from pydantic import BaseModel, Field

from auth.api_keys import generate_api_key
from auth.dependencies import get_current_user
from auth.org_dependencies import get_active_org, require_role
from db.connection import get_db_pool
from middleware.rate_limit import limiter

# include_in_schema=False set DIRECTLY on the child router (D-15): FastAPI does
# not propagate the flag from the parent /user router.
router = APIRouter(prefix="/user/api-keys", tags=["user_api_keys"], include_in_schema=False)

# public.org_role is (owner, scientist, viewer). Any member may list/create;
# revoke is restricted to the privileged role (owner).
_LIST_ROLES = ("owner", "scientist", "viewer")
_REVOKE_ROLES = ("owner",)


class CreateKeyRequest(BaseModel):
    """Body for POST /user/api-keys. name must be non-blank (mirrors the DB
    ``name_not_blank`` CHECK constraint; Pydantic rejects first with 422)."""

    name: str = Field(min_length=1, max_length=80)


@router.get("/")
async def list_my_keys(org_id: str = Depends(require_role(*_LIST_ROLES))):
    """GET /user/api-keys — org-scoped list; NEVER returns plaintext."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, prefix, created_at, last_used_at, role_at_creation
               FROM public.api_keys
               WHERE organization_id = $1 AND revoked_at IS NULL
               ORDER BY created_at DESC""",
            org_id,
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "prefix": r["prefix"],
            "role": r["role_at_creation"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
        }
        for r in rows
    ]


@router.post("/")
@limiter.limit("10/hour")
async def create_key(
    request: Request,
    body: CreateKeyRequest,
    user_id: str = Depends(get_current_user),
    active: tuple[str, str] = Depends(get_active_org),
):
    """POST /user/api-keys — mint a key. Returns plaintext EXACTLY ONCE (API-01).

    After this response, only the prefix is queryable. The row records
    ``created_by_user_id`` (caller) and ``role_at_creation`` (caller's active role)
    so the key inherits the minter's role at creation time.
    """
    org_id, role = active
    plaintext, prefix, h = generate_api_key(env="live")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO public.api_keys
                   (organization_id, created_by_user_id, name, prefix, bcrypt_hash, role_at_creation)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::public.org_role)
               RETURNING id, created_at""",
            org_id,
            user_id,
            body.name,
            prefix,
            h,
            role,
        )
    return {
        "id": str(row["id"]),
        "name": body.name,
        "prefix": prefix,
        "plaintext": plaintext,  # SHOWN ONCE. Never returned by GET.
        "role": role,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.post("/{key_id}/revoke")
async def revoke_my_key(
    key_id: str,
    org_id: str = Depends(require_role(*_REVOKE_ROLES)),
):
    """POST /user/api-keys/{key_id}/revoke — org-scoped revoke. 404 on no-op."""
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
