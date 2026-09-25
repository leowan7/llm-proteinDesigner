"""Active organization resolution + RBAC for Phase 12.

Cross-checks the X-Org-Id header against organization_memberships so a client
cannot freely impersonate an org. The JWT identifies the user; the user must
hold a membership row in the requested org.

When the header is absent the caller's personal org is used. That is what makes
a flag-off deploy behave exactly as pre-Phase-12: the orgs router only mounts
when settings.organizations_enabled is true (main.py), so with the flag off a
client has no endpoint to learn an org id from and would otherwise send no
header. Every route that Phase 12 moved onto require_role -- all of /jobs, all
of /billing, /user/usage -- would then answer 400 for every existing customer.

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
from uuid import UUID

from db.connection import get_db_pool
from fastapi import Depends, Header, HTTPException, status

from auth.dependencies import get_current_user

OrgRole = Literal["owner", "scientist", "viewer"]


def _as_uuid(value: str, what: str) -> UUID:
    """Coerce a client-supplied org id to UUID, or 400.

    organization_memberships.organization_id is a uuid column, so handing
    asyncpg an unparseable string raises before any membership check runs and
    the route answers 500. A malformed id is a bad request.
    """
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed {what}",
        ) from None


async def get_active_org(
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    user_id: str = Depends(get_current_user),
) -> tuple[str, OrgRole]:
    """Resolve the active organization for this request.

    Reads the ``X-Org-Id`` header set by the frontend org switcher and
    cross-checks it against ``public.organization_memberships`` for the
    authenticated user. Returns the tuple ``(org_id, role)``.

    With no ``X-Org-Id`` header, falls back to the caller's personal
    organization via ``public.personal_org_for`` (find-or-create, defined in
    supabase/migrations/20260605000003_personal_org_tolerance.sql), and returns
    it as ``owner`` -- the role every personal-org membership row carries, set
    by the 20260605000001 backfill, by the signup bootstrap in
    backend/auth/router.py, and by personal_org_for itself.

    The fallback creates the org when it is missing rather than 404-ing, because
    a user signed up by an old replica during the rolling deploy has a
    public.users row and no org yet.

    Raises:
        HTTPException 403: Authenticated user is not a member of the
            requested organization.
    """
    pool = await get_db_pool()
    if not x_org_id:
        async with pool.acquire() as conn:
            org_id = await conn.fetchval(
                "SELECT public.personal_org_for($1::uuid)", user_id
            )
        return str(org_id), "owner"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role::text AS role FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            _as_uuid(x_org_id, "X-Org-Id header"), user_id,
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


def require_path_role(*allowed: OrgRole):
    """Return a dependency that requires a role in the org named by the PATH.

    ``require_role`` resolves the org from the ``X-Org-Id`` header, which is
    the right scope for ``/jobs`` and ``/billing`` -- they act on whichever org
    is active. It is the wrong scope for ``/organizations/{org_id}/...``, which
    acts on the org in the path: every user is ``owner`` of their own personal
    org, so a header-scoped ``require_role("owner")`` is satisfied by every
    caller and says nothing about the org being modified. Use this for any
    route that takes ``org_id`` in its path.

    The dependency reads ``org_id`` straight from the path (FastAPI binds it by
    name) and checks the caller's membership row for THAT org.

    Raises:
        HTTPException 400: ``org_id`` is not a UUID.
        HTTPException 403: Caller is not a member of the path org, or holds
            none of ``allowed`` in it.
    """
    async def dep(org_id: str, user_id: str = Depends(get_current_user)) -> str:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT role::text AS role FROM public.organization_memberships "
                "WHERE organization_id = $1 AND user_id = $2",
                _as_uuid(org_id, "organization id"), user_id,
            )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of this organization",
            )
        if row["role"] not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(allowed)}",
            )
        return org_id

    return dep
