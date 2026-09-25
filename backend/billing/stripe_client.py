"""Stripe client functions for billing operations.

All public functions are synchronous wrappers around the stripe SDK (v14.4.1).
The stripe library uses synchronous HTTP internally; wrap calls in a thread
executor if you need to call from async context without blocking the event loop.

Key design decisions:
- Phase 12: stripe_customer_id lives on public.organizations (not public.users).
  Personal orgs (one per user, auto-created at signup) hold the customer ID that
  used to live on public.users.stripe_customer_id. Reads go through
  public.org_stripe_customer(), which still falls back to the deprecated
  public.users.stripe_customer_id -- an old replica mid-rolling-deploy writes
  only that column, and creating a second Stripe customer for a user who has
  just added a card is a billing failure, not a cosmetic one.
- record_gpu_usage uses Stripe Billing Meters API (not legacy Usage Records).
  The 'value' field in the meter event payload MUST be a string, not an int.
- check_payment_method inspects invoice_settings.default_payment_method,
  which is set when a customer completes a Checkout setup session.
"""

import logging

import asyncpg
import stripe
from config import settings

logger = logging.getLogger(__name__)

# Configure stripe at module import using the settings value.
# Tests that mock stripe functions should patch after import.
stripe.api_key = settings.stripe_secret_key


async def get_or_create_customer(
    email: str,
    org_id: str,
    org_name: str,
    pool: asyncpg.Pool,
) -> str:
    """Return the Stripe customer ID for an organization, creating one if needed.

    Phase 12: Stripe customer lives at the org level. Personal orgs (one per
    user, auto-created at signup) hold the customer ID that used to live on
    public.users.stripe_customer_id.

    Resolution goes through public.org_stripe_customer (migration
    20260605000003 section 4), which reads organizations.stripe_customer_id and
    falls back to the personal org creator's deprecated
    public.users.stripe_customer_id. An existing payer therefore never gets a
    second Stripe customer, even in the window where an old replica wrote only
    the legacy column.

    A NEW customer for a personal org is written to both columns, because the
    fallback only covers "old replica wrote first". Pre-Phase-12 code reads and
    writes public.users.stripe_customer_id only, so without the second write it
    would not see this customer, would create its own, and would attach the
    card to the customer nobody meters (railway.toml numReplicas = 2 keeps both
    versions serving for the length of a deploy). The legacy write is a
    compare-and-set: if an old replica got there first its id wins and is
    adopted onto the org, so the card and the meter always land on the same
    customer. The whole leg goes away with the column in the drop-column PR
    (runbook step 8).

    Args:
        email: Billing contact email (owner's email or org's billing_email).
        org_id: Organization UUID.
        org_name: Organization name (used as Stripe customer metadata).
        pool: Database pool.

    Returns:
        Stripe customer ID (cus_...).
    """
    existing = await pool.fetchval(
        "SELECT public.org_stripe_customer($1::uuid)", org_id,
    )
    if existing:
        return existing
    customer = stripe.Customer.create(
        email=email,
        metadata={
            "organization_id": org_id,
            "kendrew_org_name": org_name,
        },
    )
    await pool.execute(
        "UPDATE public.organizations SET stripe_customer_id = $1, updated_at = now() WHERE id = $2",
        customer.id, org_id,
    )
    legacy = await pool.fetchval(
        """WITH cas AS (
               UPDATE public.users u
                  SET stripe_customer_id = $1
                 FROM public.organizations o
                WHERE o.id = $2::uuid AND o.is_personal
                  AND u.id = o.created_by
                  AND u.stripe_customer_id IS NULL
            RETURNING u.id)
           SELECT u.stripe_customer_id
             FROM public.users u
             JOIN public.organizations o ON o.created_by = u.id
            WHERE o.id = $2::uuid AND o.is_personal
              AND NOT EXISTS (SELECT 1 FROM cas)""",
        customer.id, org_id,
    )
    if legacy and legacy != customer.id:
        # An old replica created its own customer between our read and our
        # write. It holds the payment method, so it wins.
        await pool.execute(
            "UPDATE public.organizations SET stripe_customer_id = $1, updated_at = now() WHERE id = $2",
            legacy, org_id,
        )
        logger.warning(
            "get_or_create_customer: adopted legacy customer %s for org %s, "
            "discarding freshly created %s", legacy, org_id, customer.id,
        )
        return legacy
    return customer.id


def create_setup_session(stripe_customer_id: str, return_url: str) -> str:
    """Create a Stripe Checkout session in setup mode for card collection.

    Args:
        stripe_customer_id: Existing Stripe customer ID.
        return_url: Base URL Stripe redirects to after the session completes
                    or is cancelled. Query params ?setup=success / ?setup=cancelled
                    are appended automatically.

    Returns:
        Checkout session URL to redirect the user to.
    """
    session = stripe.checkout.Session.create(
        mode="setup",
        customer=stripe_customer_id,
        payment_method_types=["card"],
        success_url=f"{return_url}?setup=success",
        cancel_url=f"{return_url}?setup=cancelled",
    )
    return session.url


def create_portal_session(stripe_customer_id: str, return_url: str) -> str:
    """Create a Stripe Billing Portal session for payment method management.

    Args:
        stripe_customer_id: Existing Stripe customer ID.
        return_url: URL Stripe redirects to when the customer exits the portal.

    Returns:
        Billing portal session URL to redirect the user to.
    """
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=return_url,
    )
    return session.url


def check_payment_method(stripe_customer_id: str) -> bool:
    """Check whether a customer has a default payment method configured.

    Inspects invoice_settings.default_payment_method, which is populated
    when the customer completes a Checkout setup session.

    Args:
        stripe_customer_id: Stripe customer ID to check.

    Returns:
        True if a default payment method is set, False otherwise.
    """
    customer = stripe.Customer.retrieve(
        stripe_customer_id,
        expand=["invoice_settings.default_payment_method"],
    )
    return bool(customer.invoice_settings.default_payment_method)


def record_gpu_usage(stripe_customer_id: str, job_id: str, gpu_seconds: int) -> None:
    """Record GPU usage as a Stripe Billing Meter event.

    Uses the Stripe Billing Meters API (not the legacy Usage Records API).
    The 'value' field MUST be a string — Stripe rejects integer values.

    Idempotency: Uses ``gpu_usage_{job_id}`` as the idempotency key. Stripe
    ignores duplicate meter events with the same key within 24 hours, so a job
    dispatched or webhooks received multiple times will produce exactly one
    billing event.

    Args:
        stripe_customer_id: Stripe customer to charge for the usage.
        job_id: Kendrew job UUID — used as the idempotency key to prevent
                double-billing from retry storms or duplicate webhooks.
        gpu_seconds: Number of GPU-seconds consumed by the job.
    """
    stripe.billing.MeterEvent.create(
        event_name=settings.stripe_meter_event_name,
        payload={
            "stripe_customer_id": stripe_customer_id,
            "value": str(gpu_seconds),  # Must be string, not int
        },
        idempotency_key=f"gpu_usage_{job_id}",
    )
