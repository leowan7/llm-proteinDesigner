"""FastAPI routers for Phase 12 organizations and invitations.

Two routers are exported:

- ``router`` — prefix ``/organizations``, all org-scoped endpoints. Most use
  ``Depends(require_role(...))`` to gate by role; a few (``/mine``, ``POST /``)
  use just ``get_current_user`` because they exist BEFORE the caller has an
  active org context.
- ``invitations_router`` — prefix ``/invitations``, holds ``/accept`` and
  ``/preview``. These cannot live under ``/organizations`` because the caller
  is by definition not yet a member (RESEARCH §9.2 chicken-and-egg).

The org-create endpoint calls the ``public.create_organization`` SECURITY
DEFINER RPC. The RPC reads ``auth.uid()`` to identify the caller, so we
``SET LOCAL request.jwt.claims`` on the connection inside the transaction to
make ``auth.uid()`` return the right user ID (the default service_role pool
has no JWT context). The claims are scoped to the transaction via
``SET LOCAL`` and are dropped on COMMIT.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

import asyncpg

from auth.dependencies import get_current_user
from auth.org_dependencies import get_active_org, require_role
from config import settings
from db.connection import get_db_pool
from organizations import models, notifications, service


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/organizations", tags=["organizations"])
invitations_router = APIRouter(prefix="/invitations", tags=["invitations"])


# ---------------------------------------------------------------------------
# Listing + creation (no active-org required)
# ---------------------------------------------------------------------------


@router.get("/mine", response_model=models.ListMineResponse)
async def list_my_orgs(user_id: str = Depends(get_current_user)) -> models.ListMineResponse:
    """Return every org the caller is a member of, with role + is_personal.

    Sorted with the personal org first so the frontend org switcher can default
    to it without a second sort.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT o.id, o.name, o.is_personal, m.role::text AS role
               FROM public.organization_memberships m
               JOIN public.organizations o ON o.id = m.organization_id
               WHERE m.user_id = $1
               ORDER BY o.is_personal DESC, o.name""",
            user_id,
        )
    return models.ListMineResponse(orgs=[
        models.OrgResponse(
            id=str(r["id"]),
            name=r["name"],
            role=r["role"],
            is_personal=r["is_personal"],
        )
        for r in rows
    ])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=models.OrgResponse)
async def create_org(
    body: models.CreateOrgRequest,
    user_id: str = Depends(get_current_user),
) -> models.OrgResponse:
    """Create a new (non-personal) organization and add the caller as owner.

    Delegates to the ``public.create_organization`` SECURITY DEFINER RPC so
    the org INSERT and the owner-membership INSERT happen atomically without
    needing a broader RLS policy on memberships (RESEARCH §4.2).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # The SECURITY DEFINER RPC reads auth.uid(); the service-role pool
            # has no JWT context, so we plant claims on the connection for the
            # duration of this transaction. SET LOCAL is dropped at COMMIT.
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": user_id, "role": "authenticated"}),
            )
            org_id = await conn.fetchval(
                "SELECT public.create_organization($1)",
                body.name,
            )
            row = await conn.fetchrow(
                "SELECT id, name, is_personal FROM public.organizations WHERE id = $1",
                org_id,
            )
    return models.OrgResponse(
        id=str(row["id"]),
        name=row["name"],
        role="owner",
        is_personal=row["is_personal"],
    )


# ---------------------------------------------------------------------------
# Single-org reads + writes (active-org required)
# ---------------------------------------------------------------------------


@router.get("/{org_id}", response_model=models.OrgResponse)
async def get_org(
    org_id: str,
    active: tuple[str, str] = Depends(get_active_org),
) -> models.OrgResponse:
    """Return basic details for the active org."""
    active_org_id, role = active
    if active_org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header must match the path org_id",
        )
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, is_personal FROM public.organizations WHERE id = $1",
            org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return models.OrgResponse(
        id=str(row["id"]),
        name=row["name"],
        role=role,
        is_personal=row["is_personal"],
    )


@router.patch("/{org_id}", response_model=models.OrgResponse)
async def update_org(
    org_id: str,
    body: models.UpdateOrgRequest,
    _org: str = Depends(require_role("owner")),
) -> models.OrgResponse:
    """Update the organization name. Owner-only."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE public.organizations
               SET name = $1, updated_at = now()
               WHERE id = $2
               RETURNING id, name, is_personal""",
            body.name, org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return models.OrgResponse(
        id=str(row["id"]),
        name=row["name"],
        role="owner",
        is_personal=row["is_personal"],
    )


@router.delete("/{org_id}")
async def delete_org(
    org_id: str,
    _org: str = Depends(require_role("owner")),
):
    """Delete an organization. Owner-only.

    Refuses if a Stripe customer is attached (v1 — Plan 12-03 will add the
    real "subscription active" check). The owner must cancel any subscription
    in the Billing Portal first.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stripe_customer_id, is_personal FROM public.organizations WHERE id = $1",
            org_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")
        if row["is_personal"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete a personal organization",
            )
        if row["stripe_customer_id"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cancel subscription in the Billing Portal first.",
            )
        await conn.execute(
            "DELETE FROM public.organizations WHERE id = $1",
            org_id,
        )
    return {"status": "deleted", "id": org_id}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{org_id}/members")
async def list_members(
    org_id: str,
    active: tuple[str, str] = Depends(get_active_org),
):
    """List members of the active org. Any member can read."""
    active_org_id, _role = active
    if active_org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header must match the path org_id",
        )
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.user_id, u.email, m.role::text AS role, m.created_at
               FROM public.organization_memberships m
               LEFT JOIN public.users u ON u.id = m.user_id
               WHERE m.organization_id = $1
               ORDER BY m.created_at""",
            org_id,
        )
    return {
        "members": [
            {
                "user_id": str(r["user_id"]),
                "email": r["email"] or "",
                "role": r["role"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }


@router.patch("/{org_id}/members/{user_id}")
async def update_member_role(
    org_id: str,
    user_id: str,
    body: models.MemberRoleUpdate,
    _org: str = Depends(require_role("owner")),
):
    """Owner updates another member's role.

    The ``protect_last_owner`` trigger will refuse to demote the only owner;
    that surfaces as a ``check_violation`` which we translate to HTTP 400.
    """
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE public.organization_memberships
                   SET role = $3::public.org_role
                   WHERE organization_id = $1 AND user_id = $2
                   RETURNING role::text AS role""",
                org_id, user_id, body.role,
            )
    except asyncpg.exceptions.RaiseError as exc:
        # protect_last_owner trigger uses SQLSTATE 23514 (check_violation).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"user_id": user_id, "role": row["role"]}


@router.delete("/{org_id}/members/{user_id}")
async def remove_member(
    org_id: str,
    user_id: str,
    active: tuple[str, str] = Depends(get_active_org),
    caller_id: str = Depends(get_current_user),
):
    """Remove a member. Owner can remove anyone; non-owners can only remove self.

    The last-owner protection trigger blocks owner-leaves-last-owner races.
    """
    active_org_id, caller_role = active
    if active_org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header must match the path org_id",
        )
    if caller_role != "owner" and user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can remove other members",
        )

    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """DELETE FROM public.organization_memberships
                   WHERE organization_id = $1 AND user_id = $2""",
                org_id, user_id,
            )
    except asyncpg.exceptions.RaiseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "removed", "user_id": user_id}


@router.post("/{org_id}/members/transfer")
async def transfer_org_ownership(
    org_id: str,
    body: models.TransferRequest,
    user_id: str = Depends(get_current_user),
    _org: str = Depends(require_role("owner")),
):
    """Owner transfers ownership to another member and self-demotes.

    Promotion + demotion are atomic in a single transaction so the
    protect_last_owner trigger sees two owners at demote-time (RESEARCH §5.3).
    """
    pool = await get_db_pool()
    await service.transfer_ownership(
        org_id=org_id,
        target_user_id=body.target_user_id,
        new_self_role=body.new_self_role,
        current_user_id=user_id,
        pool=pool,
    )
    return {"status": "transferred", "new_owner": body.target_user_id}


# ---------------------------------------------------------------------------
# Invitations (mounted on the org router)
# ---------------------------------------------------------------------------


@router.get("/{org_id}/invitations")
async def list_invitations(
    org_id: str,
    org_status: str = Query(default="pending", alias="status"),
    active: tuple[str, str] = Depends(get_active_org),
):
    """List invitations for the active org, filtered by status."""
    active_org_id, _role = active
    if active_org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header must match the path org_id",
        )
    if org_status not in ("pending", "accepted", "revoked"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be one of: pending, accepted, revoked",
        )
    if org_status == "pending":
        clause = "accepted_at IS NULL AND revoked_at IS NULL"
    elif org_status == "accepted":
        clause = "accepted_at IS NOT NULL"
    else:
        clause = "revoked_at IS NOT NULL"

    # Owner-only fields: only owners get the raw token so they can copy the
    # accept link. Members + scientists + viewers see the invitation row but
    # not the bearer credential. RESEARCH §14.1 and the contract resolution
    # documented in Plan 12-06 SUMMARY.
    _active_org_id, caller_role = active
    include_token = caller_role == "owner"

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id, email, role::text AS role, expires_at,
                       accepted_at, revoked_at, created_at, token
                FROM public.organization_invitations
                WHERE organization_id = $1 AND {clause}
                ORDER BY created_at DESC""",
            org_id,
        )
    return {
        "invitations": [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "role": r["role"],
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "accepted_at": r["accepted_at"].isoformat() if r["accepted_at"] else None,
                "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "token": r["token"] if include_token else None,
            }
            for r in rows
        ]
    }


@router.post("/{org_id}/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    org_id: str,
    body: models.InviteRequest,
    user_id: str = Depends(get_current_user),
    _org: str = Depends(require_role("owner")),
):
    """Owner creates an invitation token and emails it to ``body.email``."""
    pool = await get_db_pool()
    token = service.generate_invitation_token()
    expires_at = service.expires_default()
    async with pool.acquire() as conn:
        invite_row = await conn.fetchrow(
            """INSERT INTO public.organization_invitations
                   (organization_id, email, role, token, invited_by, expires_at)
               VALUES ($1, $2, $3::public.org_role, $4, $5, $6)
               RETURNING id""",
            org_id, body.email, body.role, token, user_id, expires_at,
        )
        org_row = await conn.fetchrow(
            "SELECT name FROM public.organizations WHERE id = $1", org_id,
        )
        inviter_row = await conn.fetchrow(
            "SELECT email FROM public.users WHERE id = $1", user_id,
        )

    accept_url = f"{settings.frontend_base_url}/invitations/accept?token={token}"
    await notifications.send_invitation_email(
        to_email=body.email,
        inviter_email=inviter_row["email"] if inviter_row else "your colleague",
        organization_name=org_row["name"] if org_row else "your organization",
        role=body.role,
        accept_url=accept_url,
        expires_at=expires_at,
    )
    # Return the token so the owner can build a copy-link in the UI without a
    # second round-trip. The endpoint is gated by require_role("owner"), so
    # only owners ever see the bearer credential. Plan 12-06 bug-fix.
    return {
        "id": str(invite_row["id"]),
        "email": body.email,
        "role": body.role,
        "expires_at": expires_at.isoformat(),
        "token": token,
    }


@router.delete("/{org_id}/invitations/{invite_id}")
async def revoke_invitation(
    org_id: str,
    invite_id: str,
    _org: str = Depends(require_role("owner")),
):
    """Owner revokes a pending invitation by stamping ``revoked_at``."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE public.organization_invitations
               SET revoked_at = now()
               WHERE id = $1 AND organization_id = $2 AND revoked_at IS NULL""",
            invite_id, org_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Invitation not found or already revoked")
    return {"status": "revoked", "id": invite_id}


# ---------------------------------------------------------------------------
# Invitation accept / preview (root-mounted, no active-org)
# ---------------------------------------------------------------------------


@invitations_router.post("/accept")
async def accept_invitation_endpoint(
    body: models.AcceptInviteRequest,
    user_id: str = Depends(get_current_user),
):
    """Accept an invitation. Idempotent on double-click (RESEARCH §14.8)."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email FROM public.users WHERE id = $1", user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return await service.accept_invitation(body.token, user_id, row["email"], pool)


@invitations_router.get("/preview")
async def preview_invitation(token: str = Query(...)):
    """Preview an invitation without consuming it.

    Returns ``{valid: false, reason: "not_found"}`` for unknown tokens — the
    same shape as valid-but-stale tokens to avoid leaking org existence
    (RESEARCH threat T-12-02-05).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT i.email, i.role::text AS role, i.expires_at,
                      i.accepted_at, i.revoked_at,
                      o.name AS organization_name
               FROM public.organization_invitations i
               JOIN public.organizations o ON o.id = i.organization_id
               WHERE i.token = $1""",
            token,
        )
    if not row:
        return {"valid": False, "reason": "not_found"}
    if row["revoked_at"] is not None:
        return {
            "valid": False,
            "reason": "revoked",
            "organization_name": row["organization_name"],
        }
    if row["accepted_at"] is not None:
        return {
            "valid": False,
            "reason": "already_accepted",
            "organization_name": row["organization_name"],
        }
    if row["expires_at"] < _dt.datetime.now(_dt.timezone.utc):
        return {
            "valid": False,
            "reason": "expired",
            "organization_name": row["organization_name"],
        }
    return {
        "valid": True,
        "organization_name": row["organization_name"],
        "role": row["role"],
        "email": row["email"],
    }
