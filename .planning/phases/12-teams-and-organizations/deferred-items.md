# Phase 12 — Deferred Items

Items discovered during plan execution that are intentionally NOT addressed in
the current plan. Track here so plan 12-06 (Wave 3 verification + cleanup) can
sweep them up alongside the `users.stripe_customer_id` column drop.

## From Plan 12-03 execution (2026-06-04)

### `backend/worker/deletion_cron.py:42-58`
- Reads `users.stripe_customer_id` to clean up the Stripe customer on hard-delete.
- 12-01 left the column in place (DEPRECATED via COMMENT) until 12-06 verifies.
- Migration path: switch to reading `organizations.stripe_customer_id` for the
  user's personal org once the deprecated column is dropped. The personal-org
  invariant from 12-01 guarantees this lookup succeeds.

### `backend/admin/router.py:108-151`
- Admin user-list SQL JOINs `public.users.stripe_customer_id` and renders it
  as `payment_status: active|none`. Same column as above.
- Migration path: rewrite to join through `organization_memberships` to the
  user's personal org's `organizations.stripe_customer_id`. Same column-drop
  timing as deletion_cron.py.

Both are correctness-preserving today because the 12-01 backfill kept the
deprecated column populated. The drop is owned by 12-06 per the original
deferred-drop decision (24h backend-rollback safety window).
