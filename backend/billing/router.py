"""Billing API endpoints.

Exposes checkout session setup, billing portal access, payment status,
and pre-job cost estimation. Authenticated endpoints require a valid
JWT in the access_token HTTP-only cookie.
"""

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.dependencies import get_current_user
from billing.estimate import estimate_cost_range
from billing.stripe_client import (
    check_payment_method,
    create_portal_session,
    create_setup_session,
    get_or_create_customer,
)
from db.connection import get_db_pool

router = APIRouter(prefix="/billing", tags=["billing"])


class ReturnUrlRequest(BaseModel):
    """Request body for endpoints that need a Stripe return URL."""

    return_url: str


async def _resolve_stripe_customer(user_id: str) -> str:
    """Resolve the Stripe customer ID for the authenticated user.

    Fetches or creates the Supabase-linked Stripe customer. Raises 503
    if the database pool cannot be acquired.

    Args:
        user_id: Authenticated user's UUID (from JWT sub claim).

    Returns:
        Stripe customer ID string (e.g. "cus_...").
    """
    pool = await get_db_pool()
    # get_or_create_customer needs the user's email; fetch from users table.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, stripe_customer_id FROM public.users WHERE id = $1",
            user_id,
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return await get_or_create_customer(
        email=row["email"],
        user_id=user_id,
        pool=pool,
    )


@router.post("/checkout-session")
async def checkout_session(
    body: ReturnUrlRequest,
    user_id: str = Depends(get_current_user),
):
    """BILL-03: Create a Stripe Checkout session in setup mode for card collection.

    The returned URL should be used to redirect the user to the Stripe-hosted
    checkout page where they can enter their payment details.

    Args:
        body.return_url: Base URL Stripe redirects to after setup completes or
                         is cancelled. Query params are appended automatically.

    Returns:
        JSON with `url` field containing the Stripe Checkout session URL.
    """
    stripe_customer_id = await _resolve_stripe_customer(user_id)
    checkout_url = create_setup_session(
        stripe_customer_id=stripe_customer_id,
        return_url=body.return_url,
    )
    return {"url": checkout_url}


@router.post("/portal-session")
async def portal_session(
    body: ReturnUrlRequest,
    user_id: str = Depends(get_current_user),
):
    """Create a Stripe Billing Portal session for payment method management.

    Allows authenticated users to view or update their saved payment methods.

    Args:
        body.return_url: URL Stripe redirects to when the user exits the portal.

    Returns:
        JSON with `url` field containing the Stripe Billing Portal session URL.
    """
    stripe_customer_id = await _resolve_stripe_customer(user_id)
    portal_url = create_portal_session(
        stripe_customer_id=stripe_customer_id,
        return_url=body.return_url,
    )
    return {"url": portal_url}


@router.get("/payment-status")
async def payment_status(user_id: str = Depends(get_current_user)):
    """BILL-03: Check whether the authenticated user has a payment method on file.

    Used by the job dispatch gate to determine whether to proceed or prompt
    the user to add a payment method.

    Returns:
        JSON with `has_payment_method` bool. Returns True if Stripe is not
        configured (dev mode — skip payment gate).
    """
    if not settings.stripe_secret_key:
        return {"has_payment_method": True}
    stripe_customer_id = await _resolve_stripe_customer(user_id)
    has_method = check_payment_method(stripe_customer_id)
    return {"has_payment_method": has_method}


@router.get("/payment-method")
async def get_payment_method(user_id: str = Depends(get_current_user)):
    """Return the authenticated user's default Stripe payment method details.

    Retrieves the card brand, last 4 digits, and expiry from the Stripe
    customer's ``invoice_settings.default_payment_method``. This is the
    payment method set when the user completed a Stripe Checkout setup session.

    Args:
        user_id: Injected by the auth dependency.

    Returns:
        Dict with ``has_payment_method`` bool. If True, also includes
        ``brand``, ``last4``, ``exp_month``, ``exp_year``.
    """
    stripe_customer_id = await _resolve_stripe_customer(user_id)

    customer = stripe.Customer.retrieve(
        stripe_customer_id,
        expand=["default_source", "invoice_settings.default_payment_method"],
    )

    pm = customer.invoice_settings.default_payment_method
    if not pm:
        return {"has_payment_method": False}

    card = getattr(pm, "card", None)
    if not card:
        # Payment method exists but is not a card type — still signal present.
        return {"has_payment_method": True}

    return {
        "has_payment_method": True,
        "brand": card.brand,
        "last4": card.last4,
        "exp_month": card.exp_month,
        "exp_year": card.exp_year,
    }


@router.get("/estimate")
async def estimate(tool: str, num_designs: int = 1):
    """BILL-02: Return a (low, high) cost estimate for a job before submission.

    No authentication required — this is informational and shown before job
    launch. Uses internal GPU runtime benchmarks and the configured markup.

    Query params:
        tool: Tool name — one of "rfdiffusion", "rfantibody", "bindcraft", "boltzgen".
        num_designs: Number of designs to estimate cost for (default 1).

    Returns:
        JSON with `low`, `high` (USD floats) and `currency` ("usd").
    """
    if num_designs < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="num_designs must be >= 1",
        )
    low, high = estimate_cost_range(tool=tool, num_designs=num_designs)
    return {"low": low, "high": high, "currency": "usd"}
