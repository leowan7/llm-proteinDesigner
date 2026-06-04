-- =============================================================================
-- Phase 12 Plan 12-06: drop deprecated public.users.stripe_customer_id column
-- =============================================================================
--
-- DECISIVE GATE: Do NOT apply this migration until ALL of the following hold:
--
--   1. Migration 20260605000001_organizations.sql has been applied in prod
--      (Phase 12 Plan 12-01) — the backfill moved every existing customer_id
--      from public.users to public.organizations.
--   2. Backend has been deployed with settings.organizations_enabled = true
--      (Phase 12 Plans 12-02 + 12-03) — every job/billing/webhook read now
--      resolves Stripe via public.organizations.stripe_customer_id.
--   3. backend/scripts/verify_stripe_org_metadata.py exited 0 against prod
--      Stripe (Phase 12 Plan 12-04 stamp + verify).
--   4. At least 24 hours have elapsed with monitoring green:
--        - Sentry: zero org-related 5xx
--        - Stripe Dashboard: meter events landing on the correct customer
--        - GPU spend alerts: no unbilled completions
--
-- Plan 12-01 deprecated this column (COMMENT only — column retained for
-- rollback safety). Plan 12-03 cut over all callers to read from
-- public.organizations. After 24 hours of clean production with the new code
-- path, the column is safe to drop. See docs/runbook-phase-12-rollout.md
-- step 9 for the operator-facing checklist.
--
-- This migration is IRREVERSIBLE without a forward migration to recreate the
-- column and a backfill from public.organizations.stripe_customer_id. Treat
-- step 9 of the rollout as the point of no return.
-- =============================================================================

ALTER TABLE public.users DROP COLUMN IF EXISTS stripe_customer_id;

-- Replace the deprecation comment with the post-Phase-12 description so future
-- readers know this table now only holds user-identity, ToS, retention, and
-- admin-flag concerns. Stripe customer_id lives on public.organizations.
COMMENT ON TABLE public.users IS
  'Phase 12: Stripe customer_id moved to public.organizations. This table '
  'now holds user identity (id, email, created_at, updated_at), ToS '
  'acceptance, notification preferences, retention overrides, and admin '
  'flags. Org-level billing reads from public.organizations.stripe_customer_id.';
