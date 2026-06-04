"""Active organization resolution + RBAC for Phase 12.

Cross-checks the X-Org-Id header against organization_memberships so a client
cannot freely impersonate an org. The JWT identifies the user; the user must
hold a membership row in the requested org.

NOT mounted on routes that legitimately have no active org context
(/auth/*, /organizations/mine, /invitations/*).

References:
- RESEARCH §5.2 (get_active_org + require_role reference implementation)
- RESEARCH §8.2 (X-Org-Id header propagation)
- RESEARCH §14.1 (RLS helper inlining gotcha — do not depend on Postgres
  helpers here; this dependency runs over the service_role pool against the
  literal organization_memberships table)
"""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, Header, HTTPException, status

from auth.dependencies import get_current_user
from db.connection import get_db_pool


OrgRole = Literal["owner", "scientist", "viewer"]


async def get_active_org(
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    user_id: str = Depends(get_current_user),
) -> tuple[str, OrgRole]:
    """Resolve the active organization for this request.

    Reads the ``X-Org-Id`` header set by the frontend org switcher and
    cross-checks it against ``public.organization_memberships`` for the
    authenticated user. Returns the tuple ``(org_id, role)``.

    Raises:
        HTTPException 400: ``X-Org-Id`` header is missing.
        HTTPException 403: Authenticated user is not a member of the
            requested organization.
    """
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header required for this endpoint",
        )
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role::text AS role FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            x_org_id, user_id,
        )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )
    return x_org_id, row["role"]


def require_role(*allowed: OrgRole):
    """Return a FastAPI dependency that requires one of the given roles.

    The returned dependency runs ``get_active_org`` first (which enforces the
    membership check), then raises 403 if the caller's role is not in
    ``allowed``. On success it returns just the ``org_id`` so handlers can
    use it directly.

    Example::

        @router.post("/jobs/launch")
        async def launch(
            body: LaunchRequest,
            org_id: str = Depends(require_role("owner", "scientist")),
            user_id: str = Depends(get_current_user),
        ):
            ...
    """
    async def dep(active: tuple[str, OrgRole] = Depends(get_active_org)) -> str:
        org_id, role = active
        if role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(allowed)}",
            )
        return org_id

    return dep
