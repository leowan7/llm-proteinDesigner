"""FastAPI dependency for admin authentication.

Extends the standard get_current_user dependency with an is_admin DB check.
Returns 403 Forbidden for any non-admin user — does not reveal admin routes exist.
"""

from auth.dependencies import get_current_user
from db.connection import get_db_pool
from fastapi import Depends, HTTPException, status


async def get_current_admin(user_id: str = Depends(get_current_user)) -> str:
    """FastAPI dependency that verifies the authenticated user has is_admin = TRUE.

    Calls get_current_user (validates JWT + returns user_id), then queries the
    users table for the is_admin flag. Returns 403 for any unauthenticated or
    non-admin request — identical response regardless of whether the route exists,
    to avoid revealing admin surface to probing users (per D-04).

    Args:
        user_id: Injected by get_current_user (validates JWT).

    Returns:
        user_id string if the user is an admin.

    Raises:
        HTTPException 403: If user is not found or is_admin is False.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_admin FROM public.users WHERE id = $1",
            user_id,
        )

    if not row or not row["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    return user_id
