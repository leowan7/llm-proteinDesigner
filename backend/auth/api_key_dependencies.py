"""FastAPI dependencies for API-key authentication (Phase 13, Plan 13-02).

Mirrors ``backend/auth/dependencies.py::get_current_user`` (cookie -> JWT -> sub)
but swaps the transport to the ``Authorization: Bearer bw_...`` header and the
verify step to a prefix lookup + HMAC check (``auth.api_keys.verify_api_key``).

Returns ``(org_id, role)`` — the same shape as
``auth.org_dependencies.get_active_org`` — so ``require_role_api`` composes the
same way ``require_role`` does for the web flow. RESEARCH §5.2 keeps this dep
separate from ``require_role`` so the API-key path is not forced through
``get_active_org`` (which requires an ``X-Org-Id`` header the SDK never sends).
"""

from typing import Annotated

from fastapi import BackgroundTasks, Depends, Header, HTTPException, Request, status

from auth.api_keys import verify_api_key
from db.connection import get_db_pool


async def get_current_api_key(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    background_tasks: BackgroundTasks = None,
) -> tuple[str, str]:
    """Validate a bearer API key. Returns ``(org_id, role)``.

    Flow:
      a. reject 401 unless the header is ``Bearer bw_...``
      b. parse the plaintext and take the first 12 chars as the prefix
      c. look the row up by prefix (revoked keys are excluded via WHERE)
      d. reject 401 when no row matches or the HMAC verify fails
      e. write ``request.state.api_key_id = str(row["id"])`` BEFORE returning —
         this is load-bearing for SC 4 / API-10: ``api_v1_limiter``'s key_func
         reads ``request.state.api_key_id`` and silently falls back to client IP
         if it is unset, which would let one noisy key swamp every other key
         behind the same NAT.
      f. schedule the debounced ``last_used_at`` UPDATE
      g. return ``(org_id, role)``
    """
    if not authorization or not authorization.startswith("Bearer bw_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    plaintext = authorization.removeprefix("Bearer ").strip()
    prefix = plaintext[:12]

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, organization_id, role_at_creation, bcrypt_hash, last_used_at
               FROM public.api_keys
               WHERE prefix = $1 AND revoked_at IS NULL""",
            prefix,
        )
    if not row or not verify_api_key(plaintext, row["bcrypt_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # Load-bearing for the per-API-key rate limiter (get_api_key_id reads this).
    request.state.api_key_id = str(row["id"])

    # Debounced last_used_at update (RESEARCH §2.2).
    if background_tasks is not None:
        background_tasks.add_task(
            _maybe_touch_last_used, row["id"], row["last_used_at"]
        )

    return (str(row["organization_id"]), row["role_at_creation"])


async def _maybe_touch_last_used(api_key_id: str, last_used_at) -> None:
    """1-minute debounced ``last_used_at`` bump (RESEARCH §2.2).

    The predicate skips the write when the row was touched within the last
    minute, keeping the hot auth path from writing on every request.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE public.api_keys
               SET last_used_at = now()
               WHERE id = $1
                 AND (last_used_at IS NULL
                      OR last_used_at < now() - INTERVAL '1 minute')""",
            api_key_id,
        )


def require_role_api(*allowed: str):
    """Return an async dep that enforces RBAC at the route boundary.

    Parallel to ``auth.org_dependencies.require_role`` (web flow). Runs
    ``get_current_api_key`` first, then raises 403 if the caller's
    role-at-creation is not in ``allowed``. On success returns just the org_id.

    Role enum values are ``owner`` | ``admin`` | ``member`` (public.org_role,
    Phase 12).
    """

    async def dep(identity: tuple[str, str] = Depends(get_current_api_key)) -> str:
        org_id, role = identity
        if role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role not permitted",
            )
        return org_id

    return dep
