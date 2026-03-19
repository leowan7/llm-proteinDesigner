"""Stripe client functions for billing operations.

All public functions are synchronous wrappers around the stripe SDK (v14.4.1).
The stripe library uses synchronous HTTP internally; wrap calls in a thread
executor if you need to call from async context without blocking the event loop.

Key design decisions:
- stripe_customer_id is cached in the users table to avoid redundant Stripe API calls.
- record_gpu_usage uses Stripe Billing Meters API (not legacy Usage Records).
  The 'value' field in the meter event payload MUST be a string, not an int.
- check_payment_method inspects invoice_settings.default_payment_method,
  which is set when a customer completes a Checkout setup session.
"""

import stripe
import asyncpg

from config import settings

# Configure stripe at module import using the settings value.
# Tests that mock stripe functions should patch after import.
stripe.api_key = settings.stripe_secret_key


async def get_or_create_customer(
    email: str,
    user_id: str,
    pool: asyncpg.Pool,
) -> str:
    """Return the Stripe customer ID for a user, creating one if needed.

    Checks the users table for an existing stripe_customer_id. If absent,
    creates a new Stripe Customer and stores the ID in the database.

    Args:
        email: User's email address (used when creating a new Stripe customer).
        user_id: Application user UUID (stored as Stripe customer metadata).
        pool: asyncpg connection pool for DB reads/writes.

    Returns:
        Stripe customer ID string (e.g. "cus_...").
    """
    # Check DB first to avoid redundant Stripe API calls
    row = await pool.fetchrow(
        "SELECT stripe_customer_id FROM public.users WHERE id = $1",
        user_id,
    )
    if row and row["stripe_customer_id"]:
        return row["stripe_customer_id"]

    # Create a new Stripe customer
    customer = stripe.Customer.create(
        email=email,
        metadata={"user_id": user_id},
    )
    customer_id: str = customer.id

    # Persist to DB
    await pool.execute(
        "UPDATE public.users SET stripe_customer_id = $1 WHERE id = $2",
        customer_id,
        user_id,
    )
    return customer_id


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


def record_gpu_usage(stripe_customer_id: str, gpu_seconds: int) -> None:
    """Record GPU usage as a Stripe Billing Meter event.

    Uses the Stripe Billing Meters API (not the legacy Usage Records API).
    The 'value' field MUST be a string — Stripe rejects integer values.

    Args:
        stripe_customer_id: Stripe customer to charge for the usage.
        gpu_seconds: Number of GPU-seconds consumed by the job.
    """
    stripe.billing.MeterEvent.create(
        event_name=settings.stripe_meter_event_name,
        payload={
            "stripe_customer_id": stripe_customer_id,
            "value": str(gpu_seconds),  # Must be string, not int
        },
    )
