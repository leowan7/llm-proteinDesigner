"""Billing API endpoints.

Exposes checkout session setup, billing portal access, payment status,
and pre-job cost estimation. Authenticated endpoints require a valid
JWT in the access_token HTTP-only cookie.
"""

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.dependencies import get_current_user
from auth.org_dependencies import require_role
from billing.estimate import estimate_cost_range
from billing.stripe_client import (
    check_payment_method,
    create_portal_session,
    create_setup_session,
    get_or_create_customer,
)
from config import settings
from db.connection import get_db_pool

router = APIRouter(prefix="/billing", tags=["billing"], include_in_schema=False)


class ReturnUrlRequest(BaseModel):
    """Request body for endpoints that need a Stripe return URL."""

    return_url: str


async def _resolve_stripe_customer(org_id: str) -> str:
    """Resolve the Stripe customer ID for an organization, creating one if needed.

    Phase 12: Reads the deterministic-first owner's email as the billing contact
    (oldest owner membership wins; predictable for migrated personal orgs).

    Args:
        org_id: Organization UUID.

    Returns:
        Stripe customer ID string (e.g. "cus_...").
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        org_row = await conn.fetchrow(
            "SELECT id, name FROM public.organizations WHERE id = $1", org_id,
        )
        if org_row is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        owner_row = await conn.fetchrow(
            """SELECT u.email FROM public.organization_memberships m
               JOIN public.users u ON u.id = m.user_id
               WHERE m.organization_id = $1 AND m.role = 'owner'
               ORDER BY m.created_at ASC LIMIT 1""",
            org_id,
        )
        if owner_row is None:
            raise HTTPException(
                status_code=409,
                detail="Organization has no owner; cannot resolve billing contact",
            )
    return await get_or_create_customer(
        email=owner_row["email"],
        org_id=str(org_row["id"]),
        org_name=org_row["name"],
        pool=pool,
    )


@router.post("/checkout-session")
async def checkout_session(
    body: ReturnUrlRequest,
    org_id: str = Depends(require_role("owner")),
):
    """BILL-03: Create a Stripe Checkout session in setup mode for card collection.

    Phase 12: owner-only. Scopes the Stripe customer to the active organization
    (X-Org-Id header). The returned URL should be used to redirect the user to
    the Stripe-hosted checkout page where they can enter their payment details.

    Args:
        body.return_url: Base URL Stripe redirects to after setup completes or
                         is cancelled. Query params are appended automatically.

    Returns:
        JSON with `url` field containing the Stripe Checkout session URL.

    Raises:
        503 if Stripe is not configured on this deployment (empty
        STRIPE_SECRET_KEY). Matches the fallback /billing/payment-status uses,
        and the frontend ReviewCard catches the error and surfaces "Payment
        setup unavailable" — better than a 500 traceback.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe billing is not configured on this deployment.",
        )
    stripe_customer_id = await _resolve_stripe_customer(org_id)
    checkout_url = create_setup_session(
        stripe_customer_id=stripe_customer_id,
        return_url=body.return_url,
    )
    return {"url": checkout_url}


@router.post("/portal-session")
async def portal_session(
    body: ReturnUrlRequest,
    org_id: str = Depends(require_role("owner")),
):
    """Create a Stripe Billing Portal session for payment method management.

    Phase 12: owner-only. Allows the org owner to view or update saved payment
    methods for the active organization.

    Args:
        body.return_url: URL Stripe redirects to when the customer exits the portal.

    Returns:
        JSON with `url` field containing the Stripe Billing Portal session URL.
    """
    stripe_customer_id = await _resolve_stripe_customer(org_id)
    portal_url = create_portal_session(
        stripe_customer_id=stripe_customer_id,
        return_url=body.return_url,
    )
    return {"url": portal_url}


@router.get("/payment-status")
async def payment_status(org_id: str = Depends(require_role("owner"))):
    """BILL-03: Check whether the active organization has a payment method on file.

    Phase 12: owner-only. Used by the job dispatch gate to determine whether
    to proceed or prompt the owner to add a payment method.

    Returns:
        JSON with `has_payment_method` bool. Returns True if Stripe is not
        configured (dev mode — skip payment gate).
    """
    if not settings.stripe_secret_key:
        return {"has_payment_method": True}
    stripe_customer_id = await _resolve_stripe_customer(org_id)
    has_method = check_payment_method(stripe_customer_id)
    return {"has_payment_method": has_method}


@router.get("/payment-method")
async def get_payment_method(org_id: str = Depends(require_role("owner"))):
    """Return the active org's default Stripe payment method details.

    Phase 12: owner-only. Retrieves the card brand, last 4 digits, and expiry
    from the Stripe customer's ``invoice_settings.default_payment_method``.
    This is the payment method set when the owner completed a Stripe Checkout
    setup session.

    Args:
        org_id: Injected by require_role("owner") — the active organization.

    Returns:
        Dict with ``has_payment_method`` bool. If True, also includes
        ``brand``, ``last4``, ``exp_month``, ``exp_year``.

        If Stripe is not configured (empty STRIPE_SECRET_KEY), returns
        ``{"has_payment_method": False}`` so the frontend can render its
        "no card on file" UI instead of throwing a 500 on the upstream
        stripe.Customer.retrieve call.
    """
    if not settings.stripe_secret_key:
        return {"has_payment_method": False}
    stripe_customer_id = await _resolve_stripe_customer(org_id)

    customer = stripe.Customer.retrieve(
        stripe_customer_id,
        expand=["default_source", "invoice_settings.default_payment_method"],
    )

    pm = customer.invoice_settings.default_payment_method

    # Self-heal: Stripe Checkout in setup mode attaches the PaymentMethod to
    # the customer (via the SetupIntent) but does NOT automatically set it as
    # invoice_settings.default_payment_method. The canonical fix is a
    # setup_intent.succeeded webhook handler, but until that's wired (see
    # PROVISIONING.md OMITTED list — STRIPE_WEBHOOK_SECRET is empty), we
    # self-heal at read time: if no default is set but ANY card is attached,
    # promote the most recent one. Idempotent and safe — subsequent reads
    # skip the modify because default_payment_method is now populated.
    if not pm:
        pms = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type="card",
            limit=1,
        )
        if pms.data:
            pm = pms.data[0]
            stripe.Customer.modify(
                stripe_customer_id,
                invoice_settings={"default_payment_method": pm.id},
            )

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
