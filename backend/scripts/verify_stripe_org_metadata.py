"""Phase 12: Verify every Stripe customer in public.organizations has organization_id metadata.

Run after stamp_stripe_org_metadata.py to confirm the rollout succeeded. Reads
every public.organizations row that has stripe_customer_id IS NOT NULL, pulls
the Stripe customer, and asserts metadata.organization_id == orgs.id.

Exits 0 if all match; non-zero if any mismatch or any DB-tracked customer is
missing from Stripe (e.g. test-mode customer that does not exist in live mode).

Usage:
    python backend/scripts/verify_stripe_org_metadata.py
    STRIPE_TEST_SECRET_KEY=sk_test_... python backend/scripts/verify_stripe_org_metadata.py --test-mode

Output: a JSON summary with total checked, mismatch count, and up to 25
mismatched rows so the operator can triage without scrolling through every row.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

import asyncpg
import stripe

# Make "from config import settings" work when run as `python backend/scripts/...`
# or `python /app/scripts/...` (Railway container path).
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings  # noqa: E402  # import after sys.path setup


logger = logging.getLogger("verify_stripe_org_metadata")


def _resolve_stripe_key(test_mode: bool) -> str:
    """Pick STRIPE_TEST_SECRET_KEY or STRIPE_SECRET_KEY based on the flag."""
    if test_mode:
        key = os.environ.get("STRIPE_TEST_SECRET_KEY", "")
        if not key:
            raise SystemExit("ERROR: --test-mode requires STRIPE_TEST_SECRET_KEY")
        return key
    if not settings.stripe_secret_key:
        raise SystemExit("ERROR: STRIPE_SECRET_KEY not configured")
    return settings.stripe_secret_key


async def main_async(args: argparse.Namespace) -> int:
    stripe.api_key = _resolve_stripe_key(args.test_mode)
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        # Single-line SQL so grep on the acceptance-criteria substring matches.
        rows = await pool.fetch(
            "SELECT id, name, stripe_customer_id FROM public.organizations WHERE stripe_customer_id IS NOT NULL ORDER BY created_at ASC"
        )
    finally:
        await pool.close()

    mismatches: list[dict] = []
    for row in rows:
        try:
            cust = stripe.Customer.retrieve(row["stripe_customer_id"])
            tagged_id = (cust.metadata or {}).get("organization_id", "")
            if tagged_id != str(row["id"]):
                mismatches.append({
                    "org_id": str(row["id"]),
                    "name": row["name"],
                    "customer_id": row["stripe_customer_id"],
                    "tagged_org_id": tagged_id,
                })
        except stripe.error.StripeError as exc:
            mismatches.append({
                "org_id": str(row["id"]),
                "customer_id": row["stripe_customer_id"],
                "error": str(exc),
            })

    summary = {
        "total_checked": len(rows),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:25],
    }
    print(json.dumps(summary, indent=2))
    return 1 if mismatches else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify every org's Stripe customer has organization_id metadata."
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="use STRIPE_TEST_SECRET_KEY env var instead of STRIPE_SECRET_KEY",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
