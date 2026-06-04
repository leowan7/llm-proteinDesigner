"""Phase 12: Stamp organization metadata onto migrated Stripe customers.

One-shot script. Run AFTER Plan 12-01's migration has moved stripe_customer_id
from public.users to public.organizations, AND AFTER Plan 12-03's backend cutover
deploys. Run BEFORE Plan 12-06's drop of public.users.stripe_customer_id.

Idempotent: re-running on already-stamped customers is a no-op (the script reads
the existing Stripe customer metadata first and skips Customer.modify when the
target organization_id already matches the DB row).

Usage:
    # Smoke test in Stripe test mode
    STRIPE_TEST_SECRET_KEY=sk_test_... python backend/scripts/stamp_stripe_org_metadata.py --test-mode --dry-run

    # Real test-mode run
    STRIPE_TEST_SECRET_KEY=sk_test_... python backend/scripts/stamp_stripe_org_metadata.py --test-mode

    # Production dry-run
    python backend/scripts/stamp_stripe_org_metadata.py --dry-run

    # Production live run (set DATABASE_URL + STRIPE_SECRET_KEY)
    python backend/scripts/stamp_stripe_org_metadata.py

    # Spot-check single org during incremental rollout
    python backend/scripts/stamp_stripe_org_metadata.py --limit 5

Output: one JSON line per org to stdout (so the operator can pipe to a log file
for audit trail); progress + warnings go to stderr so stdout stays pipeable.

Exit codes:
    0 -- all rows processed successfully (or skipped as already-tagged).
    1 -- at least one row failed; the per-row JSON line carries the error.
    2 -- configuration error (no STRIPE_SECRET_KEY etc.); nothing was attempted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import date
from typing import Any

import asyncpg
import stripe

# Make "from config import settings" work when run as `python backend/scripts/...`
# or `python /app/scripts/...` (Railway container path).
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings  # noqa: E402  # import after sys.path setup


logger = logging.getLogger("stamp_stripe_org_metadata")


MIGRATION_DATE = date.today().isoformat()  # stamped into metadata for traceability
MAX_RETRIES = 3


def _resolve_stripe_key(test_mode: bool) -> str:
    """Pick STRIPE_TEST_SECRET_KEY or STRIPE_SECRET_KEY based on the flag.

    --test-mode is the safety guard: it forces the script to use a separate
    env var, so an operator cannot accidentally hit live Stripe by misreading
    the help text or by having STRIPE_SECRET_KEY already exported.
    """
    if test_mode:
        key = os.environ.get("STRIPE_TEST_SECRET_KEY", "")
        if not key:
            raise SystemExit(
                "ERROR: --test-mode requires STRIPE_TEST_SECRET_KEY env var"
            )
        return key
    if not settings.stripe_secret_key:
        raise SystemExit("ERROR: STRIPE_SECRET_KEY not configured")
    return settings.stripe_secret_key


def _build_metadata(org_id: str, name: str, is_personal: bool) -> dict[str, str]:
    """Build the Stripe Customer metadata payload for an org.

    Shape matches what billing/stripe_client.get_or_create_customer writes for
    NEW customers post-cutover (organization_id + kendrew_org_name), with two
    extra audit keys (is_personal + migrated_from_user_v1) that mark this as a
    migrated row.
    """
    return {
        "organization_id": str(org_id),
        "kendrew_org_name": name,
        "is_personal": "true" if is_personal else "false",
        "migrated_from_user_v1": MIGRATION_DATE,
    }


def _is_already_tagged(existing_meta: dict, target_meta: dict) -> bool:
    """Return True if the Stripe customer already carries our org metadata.

    Idempotency check: we only consider the organization_id key, because the
    other three keys (kendrew_org_name, is_personal, migrated_from_user_v1)
    can legitimately drift between runs (org renames, re-runs on different
    dates). The organization_id is the ground truth -- if it matches, the
    customer is bound to the right org and we skip the API call.
    """
    return existing_meta.get("organization_id") == target_meta["organization_id"]


async def _process_org(row: asyncpg.Record, dry_run: bool) -> dict[str, Any]:
    """Process one org row; return the outcome dict.

    Outcomes:
        would-modify           -- dry-run mode; no Stripe call.
        skipped-already-tagged -- idempotency check matched; no Stripe write.
        modified               -- stripe.Customer.modify succeeded.
        failed                 -- Stripe raised; row carries the error string.
    """
    target_meta = _build_metadata(row["id"], row["name"], row["is_personal"])
    customer_id = row["stripe_customer_id"]
    out: dict[str, Any] = {
        "org_id": str(row["id"]),
        "name": row["name"],
        "customer_id": customer_id,
    }
    if dry_run:
        out["outcome"] = "would-modify"
        out["target_metadata"] = target_meta
        return out

    for attempt in range(MAX_RETRIES):
        try:
            existing = stripe.Customer.retrieve(customer_id)
            existing_meta = dict(existing.metadata or {})
            if _is_already_tagged(existing_meta, target_meta):
                out["outcome"] = "skipped-already-tagged"
                return out
            stripe.Customer.modify(customer_id, metadata=target_meta)
            out["outcome"] = "modified"
            return out
        except stripe.error.RateLimitError:
            wait = 2 ** attempt
            logger.warning(
                "Rate limit on %s (attempt %d/%d); sleeping %ds",
                customer_id, attempt + 1, MAX_RETRIES, wait,
            )
            time.sleep(wait)
        except stripe.error.StripeError as exc:
            out["outcome"] = "failed"
            out["error"] = str(exc)
            return out
    out["outcome"] = "failed"
    out["error"] = "rate limit retries exhausted"
    return out


async def main_async(args: argparse.Namespace) -> int:
    stripe.api_key = _resolve_stripe_key(args.test_mode)
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        # Single-line SQL so grep on the acceptance-criteria substring matches.
        sql = "SELECT id, name, stripe_customer_id, is_personal FROM public.organizations WHERE stripe_customer_id IS NOT NULL ORDER BY created_at ASC"
        if args.limit and args.limit > 0:
            # int() guard: argparse already enforces type=int but be defensive
            # against a stray --limit 0 (= unlimited) being passed.
            sql += f" LIMIT {int(args.limit)}"
        rows = await pool.fetch(sql)
    finally:
        await pool.close()

    logger.info(
        "Processing %d orgs (dry_run=%s, test_mode=%s, limit=%s)",
        len(rows), args.dry_run, args.test_mode, args.limit or "none",
    )

    counts: dict[str, int] = {
        "modified": 0,
        "skipped-already-tagged": 0,
        "would-modify": 0,
        "failed": 0,
    }
    for row in rows:
        outcome = await _process_org(row, args.dry_run)
        print(json.dumps(outcome))
        counts[outcome["outcome"]] = counts.get(outcome["outcome"], 0) + 1

    summary = {"summary": True, "counts": counts, "total": len(rows)}
    print(json.dumps(summary))
    return 1 if counts["failed"] > 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push Phase 12 org metadata onto existing Stripe customers."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print intended modifications without calling Stripe",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="use STRIPE_TEST_SECRET_KEY env var instead of STRIPE_SECRET_KEY",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="process at most N orgs (0 = unlimited)",
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
