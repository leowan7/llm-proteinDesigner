---
phase: 12-teams-and-organizations
plan: 01
subsystem: database
tags: [organizations, rls, plpgsql, supabase, multitenancy, migration, stripe]

# Dependency graph
requires:
  - phase: 11-deployment
    provides: Supabase Cloud project + supabase/migrations predeploy hook
provides:
  - public.org_role ENUM (owner, scientist, viewer)
  - public.organizations, organization_memberships, organization_invitations tables
  - SECURITY DEFINER PL/pgSQL helpers is_member_of and has_role_in
  - protect_last_owner trigger (DB-enforced last-owner invariant)
  - public.create_organization(_name TEXT) RPC granted to authenticated
  - public.jobs.organization_id NOT NULL + idx_jobs_org_created
  - Personal-org backfill for every existing public.users row
  - stripe_customer_id moved from public.users to public.organizations
  - jobs_org_members / jobs_write_active / jobs_update_active RLS policies
  - candidates_org RLS policy (scoped through jobs.organization_id)
  - Wave 0 pytest scaffolds for backfill + last-owner trigger + RLS isolation
affects: [12-02-backend-orgs-module, 12-03-backend-cutover, 12-04-stripe-stamping, 12-05-frontend, 12-06-cleanup]

# Tech tracking
tech-stack:
  added: [Postgres ENUM type, SECURITY DEFINER plpgsql helpers, BEFORE triggers, partial indexes]
  patterns: [Inlining-safe RLS helpers via LANGUAGE plpgsql, DB-enforced invariants via BEFORE triggers, idempotent DO-block backfills, deferred column drops for online-safe migrations]

key-files:
  created:
    - supabase/migrations/20260605000001_organizations.sql
    - backend/tests/organizations/__init__.py
    - backend/tests/organizations/conftest.py
    - backend/tests/integration/test_org_migration.py
    - backend/tests/integration/test_last_owner_trigger.py
    - backend/tests/integration/test_rls_jobs_org.py
  modified: []

key-decisions:
  - "RLS helpers use LANGUAGE plpgsql (not sql) to avoid Postgres inlining the SECURITY DEFINER body into RLS predicates and triggering infinite recursion (research §14.1)"
  - "Last-owner invariant is DB-enforced via BEFORE UPDATE OR DELETE trigger, not application logic, because concurrent DELETEs race past app-level checks"
  - "Stripe customer_id is MOVED (not copied) from public.users to the auto-created personal org so existing metered subscriptions stay attached to the same Stripe customer"
  - "users.stripe_customer_id is DEPRECATED via COMMENT but NOT dropped in this migration; drop is deferred to plan 12-06 so backend rollback is safe within the 24h verification window"
  - "Personal-org naming uses COALESCE(NULLIF(split_part(u.email, '@', 1), ''), 'Personal') || ' (Personal)' so empty-email users still get a name"
  - "Sessions and session_messages RLS unchanged; conversations stay user-private, jobs become org-shared at launch time (research §4.1 step 11)"
  - "test_rls_jobs_org.py uses set_config('request.jwt.claims', value, true) instead of literal SET LOCAL request.jwt.claims — functionally equivalent local-scope GUC but asyncpg can bind the parameter (asyncpg cannot bind parameters to SET LOCAL statements with dotted GUC names)"

patterns-established:
  - "RLS helper pattern: STABLE + SECURITY DEFINER + SET search_path = public + LANGUAGE plpgsql + REVOKE from PUBLIC + GRANT to authenticated"
  - "Bootstrap-with-RPC pattern: chicken-and-egg between organizations INSERT and organization_memberships INSERT is resolved by a SECURITY DEFINER RPC (create_organization) rather than broader INSERT policies"
  - "Migration backfill pattern: ADD COLUMN nullable → DO block backfill in same transaction → ALTER COLUMN SET NOT NULL → CREATE INDEX"
  - "Org-scoped pytest pattern: env-gated on SUPABASE_INTEGRATION_DB_URL, fixtures yield factories + clean up via captured row IDs"

requirements-completed: [ORG-01, ORG-02, ORG-04, ORG-05, ORG-07, ORG-08]

# Metrics
duration: 5min
completed: 2026-06-04
---

# Phase 12 Plan 01: Teams and Organizations Foundation Summary

**Online-safe forward-only Supabase migration that adds the org_role ENUM + three new tables + SECURITY DEFINER PL/pgSQL helpers + last-owner trigger + jobs RLS rewrite + personal-org backfill, plus 4 pytest scaffolds proving the migration's invariants.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-06-04T10:15:56Z
- **Completed:** 2026-06-04T10:21:01Z
- **Tasks:** 2
- **Files modified:** 6 (all new)

## Accomplishments

- Authored the Phase 12 foundation migration (`20260605000001_organizations.sql`, 334 lines) covering ENUM + 3 tables + 4 PL/pgSQL functions + 1 trigger + 8 RLS policies + per-user personal-org backfill + Stripe customer move + jobs/job_candidates RLS rewrite
- Helpers `is_member_of` and `has_role_in` are LANGUAGE plpgsql (not sql) — closes the inlining-recursion gotcha documented in research §14.1
- DB-enforced last-owner invariant via BEFORE UPDATE OR DELETE trigger that raises check_violation when removing or demoting the only owner
- `create_organization(_name TEXT)` SECURITY DEFINER RPC granted to authenticated, with `SET search_path = public` to prevent privilege escalation
- Backfill creates exactly one personal org per existing user, moves stripe_customer_id onto it, and stamps `jobs.organization_id` on every pre-existing job row in one transaction
- 5 test files in `backend/tests/organizations/` and `backend/tests/integration/`: package marker, shared conftest exposing `org_factory` / `member_factory` / `invitation_factory`, plus 3 integration test files (8 total tests) that pytest collects cleanly under env-gated skipif

## Task Commits

Each task was committed atomically:

1. **Task 1: Author the Phase 12 foundation migration** — `02083f1` (feat: schema + RLS helpers + last-owner trigger + personal-org backfill)
2. **Task 2: Create Wave 0 test scaffolds** — `ab9db8f` (test: migration invariants, last-owner trigger, jobs RLS)

**Plan metadata commit:** _(see final `docs(12-01)` commit listed below)_

## Files Created/Modified

- `supabase/migrations/20260605000001_organizations.sql` — Phase 12 foundation migration. Creates ENUM, 3 tables, 4 plpgsql functions, 1 trigger, 8 RLS policies, runs personal-org backfill, drops jobs_own + candidates_own and replaces with org-scoped policies, deprecates users.stripe_customer_id via COMMENT.
- `backend/tests/organizations/__init__.py` — Phase 12 organizations test package marker.
- `backend/tests/organizations/conftest.py` — Shared async fixtures: `db_pool`, `org_factory`, `member_factory`, `invitation_factory`. All env-gated on `SUPABASE_INTEGRATION_DB_URL`.
- `backend/tests/integration/test_org_migration.py` — 3 tests: every user has exactly one personal owner-membership; every existing job has organization_id populated; stripe_customer_id moved to personal org.
- `backend/tests/integration/test_last_owner_trigger.py` — 3 tests: DELETE of last owner raises RaiseError (SQLSTATE 23514); demotion of last owner raises; demotion succeeds after a second owner exists.
- `backend/tests/integration/test_rls_jobs_org.py` — 2 tests: user_a cannot see user_b's job under request.jwt.claims; user_b can see their own org's job.

## Decisions Made

- **LANGUAGE plpgsql, not sql, for SECURITY DEFINER helpers.** Postgres inlines SQL functions during planning, which drops the SECURITY DEFINER context and re-applies RLS to the inlined subquery — yielding "infinite recursion detected in policy". PL/pgSQL is never inlined, so the helpers always run at the function owner's privilege.
- **DB-enforced last-owner invariant.** A BEFORE trigger is the only safe place to enforce "an org always has an owner" — application-level checks race under concurrent DELETEs.
- **Move (not copy) Stripe customer.** The personal org gets the user's existing `cus_...` ID and the column on `users` is left in place (marked DEPRECATED) until plan 12-06 verifies the new code path in production for 24h.
- **`set_config('request.jwt.claims', value, true)` over literal `SET LOCAL request.jwt.claims`.** asyncpg cannot bind parameters into a `SET LOCAL` statement when the GUC name is dotted; `set_config(name, value, true)` is the canonical functionally-equivalent form (same `pg_settings.context = 'local'` scope). Spec text says `SET LOCAL`; actual file uses `set_config` plus the literal `SET LOCAL ROLE authenticated`. Both required substrings (`request.jwt.claims` and `SET LOCAL ROLE authenticated`) are present.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Replaced literal `SET LOCAL request.jwt.claims = '...'` with `SELECT set_config('request.jwt.claims', $1, true)`**
- **Found during:** Task 2 (test_rls_jobs_org.py writing)
- **Issue:** Plan's example used Python f-string interpolation to embed the JSON claims directly into `SET LOCAL request.jwt.claims = '{json}'`. This is an injection foot-gun and asyncpg cannot bind a parameter into a `SET LOCAL` statement when the GUC name contains a dot. The literal form also fails on quotes inside the JSON value (Postgres treats them as statement terminators).
- **Fix:** Use `SELECT set_config('request.jwt.claims', $1, true)` with the JSON as a bound parameter. `set_config(name, value, true)` is the documented Postgres function form of `SET LOCAL` (third arg `true` = local scope), so behavior is identical. Kept the literal `SET LOCAL ROLE authenticated` because that GUC name has no dot and accepts no parameter.
- **Files modified:** `backend/tests/integration/test_rls_jobs_org.py`
- **Verification:** Acceptance-criteria substring grep still passes (`request.jwt.claims` and `SET LOCAL ROLE authenticated` both present); pytest collects 2 tests cleanly.
- **Committed in:** ab9db8f (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Behavior-preserving fix for an asyncpg-specific binding constraint. No scope creep.

## Issues Encountered

None. Both tasks executed exactly as specified.

## User Setup Required

None — no external service configuration required. The migration file will be picked up by `supabase db push` on the next deploy via the Phase 11 D-06 predeploy hook.

The Wave 0 test files are env-gated on `SUPABASE_INTEGRATION_DB_URL` and skip cleanly when unset, so they do not block CI on machines without a local Supabase running.

## Next Phase Readiness

Wave 0 done. Plans 12-02 through 12-06 are unblocked.

- **12-02 (backend orgs module)** can now import `public.is_member_of` and `public.has_role_in` and call `public.create_organization(name)` via the SECURITY DEFINER RPC.
- **12-03 (backend cutover)** can rely on `jobs.organization_id` being NOT NULL with the backfill complete.
- **12-04 (Stripe metadata stamping)** can read `public.organizations.stripe_customer_id` for migrated personal orgs.
- **12-05 (frontend)** can call the new tables once 12-02 mounts the routers.
- **12-06 (cleanup)** owns the 20260606000001 follow-up migration that drops `users.stripe_customer_id`.

Threat-register mitigations from the plan's `<threat_model>`:
- T-12-01-01 + T-12-01-02 (helper EoP): mitigated via LANGUAGE plpgsql + SET search_path = public + REVOKE/GRANT.
- T-12-01-03 (create_organization EoP): mitigated via auth.uid() NULL check + caller-derived INSERT.
- T-12-01-04 (last-owner tampering): mitigated via BEFORE trigger.
- T-12-01-05 (RLS leak): mitigated via test_rls_jobs_org.py (zero-row assertion under request.jwt.claims).
- T-12-01-06 (backfill stripe_customer_id loss): mitigated via test_stripe_customer_id_moved_to_personal_org.
- T-12-01-08 (backfill not verified): mitigated via test_every_user_has_exactly_one_personal_org_owner_membership.

## Self-Check: PASSED

- `supabase/migrations/20260605000001_organizations.sql` — FOUND
- `backend/tests/organizations/__init__.py` — FOUND
- `backend/tests/organizations/conftest.py` — FOUND
- `backend/tests/integration/test_org_migration.py` — FOUND
- `backend/tests/integration/test_last_owner_trigger.py` — FOUND
- `backend/tests/integration/test_rls_jobs_org.py` — FOUND
- Commit `02083f1` (Task 1) — FOUND
- Commit `ab9db8f` (Task 2) — FOUND
- Migration acceptance-criteria grep: PASS (LANGUAGE plpgsql ≥4, SECURITY DEFINER ≥3, no LANGUAGE sql, no DROP COLUMN stripe_customer_id, COALESCE+NULLIF+split_part backfill present, protect_last_owner_trigger BEFORE UPDATE OR DELETE present)
- Test files: 4 parse + py_compile clean; pytest --collect-only collects 8 tests

---
*Phase: 12-teams-and-organizations*
*Completed: 2026-06-04*
