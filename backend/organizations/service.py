"""Business logic for Phase 12 organization workflows.

The router calls into these functions for anything that requires more than a
single SQL round-trip:

- ``accept_invitation`` — validates token, email-match, and stamps both the
  membership insert and the accept timestamp in one transaction (idempotent
  on double-click per RESEARCH §14.8).
- ``transfer_ownership`` — promotes the target to owner then demotes the
  current owner inside a single transaction so the protect_last_owner trigger
  sees two owners at demote-time (RESEARCH §5.3).
- ``generate_invitation_token`` / ``expires_default`` — pure helpers.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status


logger = logging.getLogger(__name__)


# Token entropy: 32 bytes of os.urandom yields a 43-char URL-safe base64 string.
# Stored plaintext (single-use bearer credential — DB compromise has bigger
# implications than this token).
_TOKEN_BYTES = 32

# Default invitation lifetime — 7 days matches the email body wording.
_DEFAULT_TTL_DAYS = 7


def generate_invitation_token() -> str:
    """Return a 32-byte URL-safe random invitation token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def expires_default() -> datetime:
    """Return a timezone-aware UTC expiry timestamp (now + 7 days)."""
    return datetime.now(timezone.utc) + timedelta(days=_DEFAULT_TTL_DAYS)


async def accept_invitation(
    token: str,
    user_id: str,
    user_email: str,
    pool,
) -> dict:
    """Accept an organization invitation and add the caller as a member.

    Wraps the membership insert and the accept-timestamp update in one
    transaction. Both writes are idempotent so a double-click is safe:

    - ``INSERT ... ON CONFLICT (organization_id, user_id) DO NOTHING``
    - ``UPDATE ... WHERE id = $1 AND accepted_at IS NULL``

    Email-match check is case-insensitive and uses the JWT-verified email,
    NOT the request body, so a malicious caller cannot bypass the
    invitation-email gate (RESEARCH §6.2 branch B).

    Args:
        token: Invitation token from the email link.
        user_id: Supabase user UUID (from the authenticated JWT).
        user_email: Email address recorded in ``public.users`` for ``user_id``.
        pool: ``asyncpg.Pool`` instance.

    Returns:
        ``{"organization_id": str, "role": str}`` on success.

    Raises:
        HTTPException 404: Token does not exist.
        HTTPException 410: Invitation has been revoked or has expired.
        HTTPException 409: Invitation email does not match the caller.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            invite = await conn.fetchrow(
                """SELECT id, organization_id, email, role::text AS role,
                          expires_at, accepted_at, revoked_at
                   FROM public.organization_invitations
                   WHERE token = $1""",
                token,
            )
            if not invite:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Invitation not found",
                )
            if invite["revoked_at"] is not None:
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail="Invitation has been revoked",
                )
            if invite["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail="Invitation has expired",
                )
            if invite["email"].lower() != user_email.lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"This invitation is for {invite['email']}; sign in "
                        "with that account to accept."
                    ),
                )

            # Idempotent membership insert.
            await conn.execute(
                """INSERT INTO public.organization_memberships
                       (organization_id, user_id, role)
                   VALUES ($1, $2, $3::public.org_role)
                   ON CONFLICT (organization_id, user_id) DO NOTHING""",
                invite["organization_id"], user_id, invite["role"],
            )
            # Idempotent accept stamp.
            await conn.execute(
                """UPDATE public.organization_invitations
                   SET accepted_at = now(), accepted_by = $2
                   WHERE id = $1 AND accepted_at IS NULL""",
                invite["id"], user_id,
            )

            return {
                "organization_id": str(invite["organization_id"]),
                "role": invite["role"],
            }


async def transfer_ownership(
    org_id: str,
    target_user_id: str,
    new_self_role: str,
    current_user_id: str,
    pool,
) -> None:
    """Atomically promote ``target_user_id`` to owner and demote ``current_user_id``.

    Order matters. The ``protect_last_owner`` BEFORE-UPDATE trigger blocks
    demotion of the only owner; we must promote the target FIRST so the
    trigger sees two owners when the current owner is demoted (RESEARCH §5.3).

    Args:
        org_id: Organization UUID.
        target_user_id: User UUID to promote to owner.
        new_self_role: Role the current owner is demoted to (must be
            ``"scientist"`` or ``"viewer"``).
        current_user_id: User UUID of the caller (current owner).
        pool: ``asyncpg.Pool`` instance.

    Raises:
        HTTPException 400: ``new_self_role`` is not scientist/viewer, or
            ``target_user_id == current_user_id``.
        HTTPException 404: target is not a member of the org.
    """
    if new_self_role not in ("scientist", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_self_role must be 'scientist' or 'viewer'",
        )
    if target_user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot transfer ownership to self",
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            target_row = await conn.fetchrow(
                "SELECT role::text AS role FROM public.organization_memberships "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, target_user_id,
            )
            if not target_row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Target user is not a member of this organization",
                )
            # 1. Promote target. Trigger allows owner -> owner / non-owner -> owner.
            await conn.execute(
                "UPDATE public.organization_memberships "
                "SET role = 'owner'::public.org_role "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, target_user_id,
            )
            # 2. Demote self. Trigger sees TWO owners at this point and allows it.
            await conn.execute(
                "UPDATE public.organization_memberships "
                "SET role = $3::public.org_role "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, current_user_id, new_self_role,
            )
