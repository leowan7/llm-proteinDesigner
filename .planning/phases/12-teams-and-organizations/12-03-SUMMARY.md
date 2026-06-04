---
phase: 12-teams-and-organizations
plan: 03
subsystem: backend
tags: [organizations, backend-cutover, jobs, billing, webhooks, stripe, rls, signup]

# Dependency graph
requires:
  - phase: 12-teams-and-organizations
    plan: 01
    provides: organizations + organization_memberships + protect_last_owner trigger + jobs.organization_id NOT NULL + personal-org backfill
  - phase: 12-teams-and-organizations
    plan: 02
    provides: get_active_org + require_role + settings.organizations_enabled
provides:
  - jobs.created_by_user_id NOT NULL column + idx_jobs_created_by
  - org-scoped jobs router (launch/list/get/status/download/cancel) gated by require_role
  - owner-only billing router; _resolve_stripe_customer takes org_id
  - billing.stripe_client.get_or_create_customer reads/writes public.organizations.stripe_customer_id with Stripe metadata {organization_id, kendrew_org_name}
  - webhooks router JOIN through jobs.organization_id resolves billing customer
  - jobs.dispatch.launch_job accepts organization_id + created_by_user_id and stamps them on the queued row
  - jobs.service.cancel_job_by_id resolves billing customer via the same org JOIN (Rule 2 inline fix)
  - user/usage scoped through get_active_org; owner sees full org spend, scientist sees own spend, viewer 403
  - signup endpoint auto-creates personal org + owner membership (ORG-07 invariant for new accounts)
  - 5 cutover test files + 9 new permission-matrix rows (replacing the 12-02 xfail placeholder)
affects: [12-04-stripe-stamping, 12-05-frontend, 12-06-cleanup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Owner-first email lookup for org billing contact (organization_memberships ORDER BY created_at ASC LIMIT 1)"
    - "Webhook billing routing via service-role JOIN through jobs.organization_id (RLS-bypass for unauth context)"
    - "Personal-org bootstrap as service_role with explicit user_id (auth.uid() is NULL pre-login)"
    - "Org-scoped pilot eligibility: any org-completed pilot qualifies any org member to launch full_design"

key-files:
  created:
    - supabase/migrations/20260605000002_jobs_created_by.sql
    - backend/tests/organizations/test_list_jobs_org_scope.py
    - backend/tests/organizations/test_cross_org_isolation.py
    - backend/tests/billing/test_meter_org.py
    - backend/tests/webhooks/test_runpod_org_billing.py
    - backend/tests/integration/test_signup_creates_personal_org.py
    - .planning/phases/12-teams-and-organizations/deferred-items.md
  modified:
    - backend/jobs/router.py
    - backend/jobs/dispatch.py
    - backend/jobs/service.py
    - backend/billing/router.py
    - backend/billing/stripe_client.py
    - backend/webhooks/router.py
    - backend/user/router.py
    - backend/auth/router.py
    - backend/tests/organizations/test_permissions.py
    - backend/tests/jobs/test_cancel.py
    - backend/tests/jobs/test_download.py
    - backend/tests/jobs/test_status_stream.py
    - backend/tests/user/test_router.py

key-decisions:
  - "Owner-resolution rule: oldest owner-membership wins. Deterministic for migrated personal orgs (the backfill makes the owner_id well-defined) and predictable for team orgs (the original creator is the billing contact until ownership is transferred)."
  - "Webhook billing JOIN: SELECT o.stripe_customer_id FROM public.jobs j JOIN public.organizations o ON o.id = j.organization_id WHERE j.id = $1. Service-role pool bypasses RLS; no user JWT means no is_member_of() call."
  - "Pilot-gate scope flipped from user_id to organization_id. Any org-completed pilot now qualifies any org member to launch full_design — matches the org-shared-jobs design where scientists collaborate on the same campaign."
  - "Storage-prefix immutability: jobs.created_by_user_id is the launcher (audit), but the S3 prefix users/{user_id}/jobs/{job_id}/outputs/ still uses the row's original user_id. Download endpoint reads user_id from the job row, not from the caller."
  - "Personal-org bootstrap on signup is best-effort (try/except + log warning). Wave 0 backfill handled pre-existing users; this code path covers new signups. Signup UX is not blocked on bootstrap failure (matches the existing ToS-write tolerance)."
  - "/user/usage is the only /user/* endpoint that became org-scoped. The rest (/user/settings, /user/notification-prefs, /user/data-export, /user/delete-account, /user/cancel-deletion, /user/retention) remain user-scoped per RESEARCH §4.1 step 11."
  - "Viewers are blocked from /user/usage (no use case for read-only members to see billing); owners see all org spend, scientists see only their own jobs."
  - "jobs/service.cancel_job_by_id billing path also rewritten via JOIN (Rule 2 inline fix). The cancel path runs from both user and admin routers; neither can rely on is_member_of() so JOIN is the unified resolution pattern."
  - "Single-tenant existing tests gained get_active_org overrides (Rule 1 inline fix). Feature flag governs main.py mounting only — tests that build their own app via include_router still need the new dep override."

patterns-established:
  - "Org-scoped router endpoint signature: handler takes (body, user_id=Depends(get_current_user), org_id=Depends(require_role(*roles))). require_role chains through get_active_org which enforces X-Org-Id + membership cross-check."
  - "Billing customer resolution from a webhook (no JWT context): JOIN public.jobs JOIN public.organizations on jobs.organization_id, read organizations.stripe_customer_id."
  - "Personal-org bootstrap inside one transaction: INSERT organizations RETURNING id + INSERT organization_memberships(owner) ON CONFLICT DO NOTHING."
  - "Single-tenant test override pair: app.dependency_overrides[get_current_user] = ... + app.dependency_overrides[get_active_org] = lambda: (org_id, role)."

requirements-completed: [ORG-03, ORG-04, ORG-06, ORG-07]

# Metrics
duration: 19min
completed: 2026-06-04
---

# Phase 12 Plan 03: Wave 2 Backend Cutover Summary

**Cut over jobs/billing/webhooks/user routes to read and write through public.organizations + organization_memberships instead of public.users. Added jobs.created_by_user_id NOT NULL with backfill, rewrote stripe_client to take org_id and read/write public.organizations, gated every billing endpoint behind require_role('owner'), made signup auto-create a personal organization + owner membership for new users, and proved the cutover correctness with 11 new pytest tests + 9 new permission-matrix rows that replaced the 12-02 xfail placeholder. Feature flag remains default-False — 12-04 owns the flip.**

## Performance

- **Duration:** 19 min
- **Started:** 2026-06-04T10:46:38Z
- **Completed:** 2026-06-04T11:05:28Z
- **Tasks:** 2
- **Files created:** 7 (1 migration + 5 test files + deferred-items tracker)
- **Files modified:** 13 (7 backend modules + 5 test files + 1 xfail flip + auth router)

## Accomplishments

- Migration `20260605000002_jobs_created_by.sql` (12 lines): adds `jobs.created_by_user_id UUID REFERENCES public.users(id)` with `UPDATE ... = user_id WHERE created_by_user_id IS NULL` backfill, `SET NOT NULL`, and `idx_jobs_created_by` index. Comment marks it as the audit trail field complementing `organization_id` as the billing scope.
- `billing/stripe_client.get_or_create_customer` signature now `(email, org_id, org_name, pool)`. Reads/writes `public.organizations.stripe_customer_id`. Stripe metadata stamps `{organization_id, kendrew_org_name}` so 12-04 has a populated customer to stamp against.
- `billing/router._resolve_stripe_customer(org_id)` resolves the deterministic-first owner email via `organization_memberships JOIN users WHERE role='owner' ORDER BY created_at ASC LIMIT 1`. Every billing endpoint (`/checkout-session`, `/portal-session`, `/payment-status`, `/payment-method`) now depends on `require_role("owner")`. `/billing/estimate` remains unauthenticated (informational).
- `jobs/router` cutover endpoint-by-endpoint:
  - `launch_job_endpoint`: `require_role("owner", "scientist")`, all WHERE clauses org-scoped, Stripe customer resolved via the org owner, full-design pilot gate now org-scoped (any org-completed pilot qualifies any org member).
  - `list_jobs`: `require_role("owner", "scientist", "viewer")` (any member), SQL switched to `WHERE j.organization_id = $1` + `LEFT JOIN public.users u ON u.id = j.created_by_user_id`; response carries `created_by_user_id` + `created_by_email` for the "launched by Alice" UI.
  - `job_status_stream` / `download_all_designs` / `get_job`: any-role gate; org-scoped `WHERE id = $1 AND organization_id = $2`. Download's storage prefix still uses the row's original `user_id` (read from DB) since S3 keys are immutable.
  - `cancel_job`: `require_role("owner", "scientist")` (viewers blocked); org-scoped lookup.
- `jobs/dispatch.launch_job` accepts optional `organization_id` + `created_by_user_id`. When supplied, the queued-status UPDATE also stamps both columns. The router always supplies them; legacy code paths (worker resume) don't, hence the optional path.
- `webhooks/router` completion-billing block resolves customer via `SELECT o.stripe_customer_id FROM public.jobs j JOIN public.organizations o ON o.id = j.organization_id WHERE j.id = $1`. Service-role pool bypasses RLS as required for the unauth webhook context.
- `user/router` `/user/usage` now depends on `get_active_org`. Owner queries `WHERE organization_id = $1`; scientist queries `WHERE organization_id = $1 AND created_by_user_id = $2`; viewer 403. Other `/user/*` endpoints stay user-scoped.
- `auth/router.signup` adds a transaction-wrapped post-Supabase-signup block that `INSERT INTO public.organizations (name, is_personal, created_by) VALUES ('{email_local} (Personal)', TRUE, $1)` + `INSERT INTO public.organization_memberships (organization_id, user_id, role) VALUES ($1, $2, 'owner'::public.org_role) ON CONFLICT DO NOTHING`. Wrapped in try/except so signup UX is not blocked on org-bootstrap failure.
- 5 new test files (11 tests):
  - `tests/organizations/test_list_jobs_org_scope.py` (3 tests) — proves member B sees member A's jobs in org X; response includes `created_by_user_id` + `created_by_email`; ORDER BY created_at DESC.
  - `tests/organizations/test_cross_org_isolation.py` (3 tests) — exercises the REAL `get_active_org` dependency: no membership → 403; membership → 200; per-request validation (no caching).
  - `tests/billing/test_meter_org.py` (3 tests) — meter event uses org-resolved customer; `get_or_create_customer` writes `public.organizations` not `public.users` with `{organization_id, kendrew_org_name}` metadata; cached-customer fast path skips Stripe.
  - `tests/webhooks/test_runpod_org_billing.py` (2 tests) — webhook handler resolves customer via the JOIN through `jobs.organization_id`; skips billing silently when org has no Stripe customer.
  - `tests/integration/test_signup_creates_personal_org.py` (1 env-gated test) — proves the bootstrap INSERT block produces exactly one personal owner membership per user (ORG-07 invariant).
- `tests/organizations/test_permissions.py` flipped: the 12-02 xfail placeholder `test_scientist_can_launch_job_xfail` is gone. Replaced with 9 new parametrized rows across 3 matrices: `test_list_jobs_role_matrix` (any-role 200), `test_billing_endpoints_owner_only` (owner 200 / scientist+viewer 403), `test_cancel_job_blocks_viewer` (owner+scientist pass role gate to a 404 'no running job', viewer 403 at role gate).

## Task Commits

1. **Task 1: Migration + jobs/billing/webhooks/user cutover + stripe_client org_id** — `9b3fbd9` (feat: 8 files, 339 insertions, 158 deletions)
2. **Task 2: Personal-org signup bootstrap + 5 test files + xfail flip + single-tenant test fixes** — `5e7d815` (test: 11 files, 943 insertions, 11 deletions)

**Plan metadata commit:** _(see final `docs(12-03)` commit at plan close-out)_

## Files Created/Modified

### Created

- `supabase/migrations/20260605000002_jobs_created_by.sql` — Add `jobs.created_by_user_id NOT NULL` with backfill + `idx_jobs_created_by`.
- `backend/tests/organizations/test_list_jobs_org_scope.py` — 3 tests for org-scoped GET /jobs response.
- `backend/tests/organizations/test_cross_org_isolation.py` — 3 tests for X-Org-Id spoofing → 403 via real `get_active_org`.
- `backend/tests/billing/test_meter_org.py` — 3 tests for org-routed meter events + `get_or_create_customer` SQL targets.
- `backend/tests/webhooks/test_runpod_org_billing.py` — 2 tests for the webhook billing JOIN path.
- `backend/tests/integration/test_signup_creates_personal_org.py` — env-gated integration test for the personal-org bootstrap INSERT.
- `.planning/phases/12-teams-and-organizations/deferred-items.md` — Phase 12 tracker for out-of-scope items found during 12-03 execution.

### Modified

- `backend/jobs/router.py` — every endpoint org-scoped via `require_role`; SQL rewritten; list_jobs response carries `created_by_user_id` + `created_by_email`.
- `backend/jobs/dispatch.py` — `launch_job` accepts `organization_id` + `created_by_user_id` and stamps them on the queued UPDATE.
- `backend/jobs/service.py` — `cancel_job_by_id` billing path resolves customer via JOIN (Rule 2 inline fix, scope outside the plan's enumerated files but on the cutover surface).
- `backend/billing/router.py` — every endpoint `require_role("owner")`; `_resolve_stripe_customer(org_id)` reads owner email from memberships.
- `backend/billing/stripe_client.py` — `get_or_create_customer(email, org_id, org_name, pool)` reads/writes `public.organizations`.
- `backend/webhooks/router.py` — completion billing block resolves customer via `JOIN public.jobs JOIN public.organizations on jobs.organization_id`.
- `backend/user/router.py` — `/user/usage` scoped through `get_active_org`; owner/scientist/viewer split.
- `backend/auth/router.py` — signup post-block creates personal org + owner membership atomically (best-effort, logged on failure).
- `backend/tests/organizations/test_permissions.py` — xfail removed; 9 new passing role-matrix rows for jobs + billing + cancel.
- `backend/tests/jobs/test_cancel.py` — `get_active_org` override added to all 3 cancel tests (Rule 1 inline fix).
- `backend/tests/jobs/test_download.py` — same override + `_make_pool` returns `user_id` for the storage-prefix lookup.
- `backend/tests/jobs/test_status_stream.py` — override for the SSE HTTP-route test.
- `backend/tests/user/test_router.py` — autouse fixture installs the override alongside `get_current_user`.

## Decisions Made

- **Owner-first email lookup.** `_resolve_stripe_customer` picks the oldest owner-membership row (`ORDER BY created_at ASC LIMIT 1`) as the billing contact. Deterministic for migrated personal orgs (12-01 backfill makes the owner well-defined) and stable for team orgs (the original creator remains the billing contact until ownership is transferred).
- **Webhook resolves billing via JOIN, not via the deprecated user column.** The webhook handler runs without a user JWT, so the natural-language alternative ("look up the launcher's stripe_customer_id") would fall back to the still-DEPRECATED `users.stripe_customer_id` and obscure the cutover. Instead the handler reads `organizations.stripe_customer_id` through the job's `organization_id` — same answer for personal orgs, correct answer for team orgs.
- **Pilot eligibility flipped from user_id to organization_id.** The full-design gate now allows any org member to launch full_design once any org member has completed a pilot. Matches the org-shared-jobs design where scientists collaborate on a single campaign.
- **Storage prefix immutability.** S3 outputs are written at `users/{user_id}/jobs/{job_id}/outputs/` at write time and must remain stable. The download endpoint now reads `user_id` from the job row (the original launcher), not from the caller. The caller is gated only on org membership.
- **Personal-org bootstrap is best-effort.** Same pattern as the existing ToS-write block: try/except + WARNING log on failure. Signup UX is not blocked. The Wave 0 migration covers pre-existing users; this branch covers new signups. If the bootstrap fails (e.g. transient DB contention), the user has a valid `public.users` row but no personal org until they explicitly create one via `POST /organizations`.
- **`/user/usage` is the only /user/* endpoint that became org-scoped.** All other `/user/*` paths (settings, prefs, data-export, delete-account, cancel-deletion, retention) remain user-scoped per RESEARCH §4.1 step 11. They're user-identity operations, not org-context operations.
- **Viewers blocked from `/user/usage`.** No use case for read-only members to see spend (they don't launch jobs, they don't pay). Owners see full org spend; scientists see only their own jobs' spend.
- **Plan-scope expansion: `jobs/service.cancel_job_by_id` was not in the plan's `files_modified` but its billing path called the deprecated `users.stripe_customer_id` query.** The cancel path runs from both the user-scoped and admin-scoped routers; both need the org JOIN. Fixed inline (Rule 2 — Critical for org-scoped billing correctness) using the same JOIN pattern as the webhook handler.
- **Single-tenant existing tests got `get_active_org` overrides (not removed).** The plan's gotcha document said: "Existing tests in `backend/tests/` that exercise the single-tenant routes must continue passing... add a fixture that defaults to the test user's personal org." Tests were rebuilt with a `(org_id, role)` tuple override mirroring the new dep contract.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Critical] `jobs/service.cancel_job_by_id` billing resolution still read `users.stripe_customer_id`**
- **Found during:** Task 1 verification grep (`SELECT stripe_customer_id FROM public.users`)
- **Issue:** `service.py:128-133` resolved the meter customer via the pre-cutover `users.stripe_customer_id` path. The plan's `files_modified` enumeration did not include `jobs/service.py`, but the cancel path is invoked from BOTH the user-scoped cancel endpoint AND the admin cancel endpoint — and both need the org-scoped meter to fire correctly post-cutover.
- **Fix:** Rewrote the billing block to use the same `JOIN public.jobs JOIN public.organizations ON o.id = j.organization_id WHERE j.id = $1` pattern as the webhook handler. Documented in deferred-items.md as in-scope for 12-03 (admin/deletion_cron parallel out-of-scope items remain deferred to 12-06).
- **Files modified:** `backend/jobs/service.py`
- **Verification:** Negative grep `SELECT stripe_customer_id FROM public.users` returns zero matches across `backend/jobs/`, `backend/billing/`, `backend/webhooks/`, `backend/user/`.
- **Committed in:** `9b3fbd9` (Task 1 commit)

**2. [Rule 1 - Bug] Existing single-tenant tests broke under cutover routes**
- **Found during:** Task 2 full-backend pytest run after the cutover commit
- **Issue:** 8 existing tests (`tests/jobs/test_cancel.py`, `tests/jobs/test_download.py`, `tests/jobs/test_status_stream.py`, `tests/user/test_router.py::test_get_usage`) hit the cutover routes through `from main import app`. The feature flag governs only the orgs/invitations router mounting in main.py; the jobs/billing/user routers are mounted unconditionally and now require `get_active_org`. The tests were failing with 400 "X-Org-Id header required".
- **Fix:** Per the plan's gotcha guidance ("add a fixture that defaults to the test user's personal org"), each affected test file imports `get_active_org` and installs `app.dependency_overrides[get_active_org] = lambda: ("org-personal", role)` alongside its existing `get_current_user` override. For `test_download.py`, also updated `_make_pool` so its `fetchrow` returns `user_id` (the cutover download endpoint reads `user_id` from the job row for the S3 prefix lookup).
- **Files modified:** `backend/tests/jobs/test_cancel.py`, `backend/tests/jobs/test_download.py`, `backend/tests/jobs/test_status_stream.py`, `backend/tests/user/test_router.py`
- **Verification:** Full backend suite `pytest tests/` reports 362 passed / 19 skipped / 3 xfailed / 0 failed (the 3 xfailed are pre-existing in unrelated modules).
- **Committed in:** `5e7d815` (Task 2 commit)

### Out-of-Scope Items (deferred)

Found during cutover ripple-check grep but deferred to plan 12-06 (with the `users.stripe_customer_id` column drop). Tracked in `.planning/phases/12-teams-and-organizations/deferred-items.md`:

- `backend/worker/deletion_cron.py:42-58` — GDPR hard-delete reads `users.stripe_customer_id` for Stripe customer cleanup. Works today because 12-01 backfill kept the column populated; will switch to reading `organizations.stripe_customer_id` for the user's personal org when 12-06 drops the deprecated column.
- `backend/admin/router.py:108-151` — Admin user list joins `users.stripe_customer_id` for the `payment_status: active|none` indicator. Same column, same timing.

These are correctness-preserving today (the 12-01 backfill maintains the deprecated column) and the column drop is the natural fix; they do not block 12-04 or 12-05.

---

**Total deviations:** 2 auto-fixed (1 Rule 2 critical, 1 Rule 1 test breakage). 2 out-of-scope items deferred to 12-06.
**Impact on plan:** Both fixes preserved cutover correctness without architectural change. The plan-files set widened by 1 file (`jobs/service.py`) and 4 test files (single-tenant test repairs).

## Issues Encountered

None requiring user input.

## User Setup Required

None. The migration is picked up automatically by `supabase db push` on the next deploy via the Phase 11 D-06 predeploy hook. `settings.organizations_enabled` is still default-False, so this code does not change production behavior on deploy — Plan 12-04 owns the flip.

## Next Phase Readiness

Wave 2 cutover done. Plans 12-04 and 12-05 are unblocked:

- **12-04 (Stripe metadata stamping)** can now read `public.organizations.stripe_customer_id` for every migrated personal org AND for any new personal org created on signup. The stamping script's `metadata` payload will be `{organization_id, kendrew_org_name}` — same shape as `get_or_create_customer` writes for new customers.
- **12-05 (frontend)** can call GET /jobs with the X-Org-Id header and receive org-scoped rows including `created_by_user_id` + `created_by_email` for the "launched by Alice" column. The org-switcher can call GET /organizations/mine (mounted in 12-02 when the flag flips). Owner-gated billing UI uses the same `require_role('owner')` semantics on the backend; frontend just hides billing UI when role !== 'owner'.

Threat-register mitigations from the plan's `<threat_model>`:
- T-12-03-01 (X-Org-Id spoofing): mitigated via `get_active_org` DB cross-check + `test_cross_org_isolation.py` proof.
- T-12-03-02 (Viewer launches job): mitigated via `require_role("owner","scientist")` on `launch_job_endpoint` + `test_cancel_job_blocks_viewer` matrix row for cancel.
- T-12-03-03 (Non-owner sees billing): mitigated via `require_role("owner")` on every billing endpoint + `test_billing_endpoints_owner_only` matrix.
- T-12-03-04 (Webhook routes meter to wrong customer): mitigated via JOIN through `jobs.organization_id` + `test_webhook_completion_resolves_customer_via_org_join`.
- T-12-03-05 (created_by_email leak): explicitly accepted (intended UX — org members see each other's emails).
- T-12-03-06 (Signup injects arbitrary user_id): mitigated via `new_user_id = result.user.id` (server-side Supabase return, never request body).
- T-12-03-07 (Race on first job launch creates duplicate Stripe customer): accepted; idempotent UPDATE on the second-to-arrive write means the unused customer is an orphan, cleaned up by a Stripe sweep job (not Phase 12).
- T-12-03-08 (No audit trail on launched job): mitigated via `jobs.created_by_user_id NOT NULL` + dispatch path stamping it from `get_current_user`.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema-level trust boundaries introduced beyond what the plan's `<threat_model>` covers.

## Self-Check: PASSED

- `supabase/migrations/20260605000002_jobs_created_by.sql` — FOUND (contains `created_by_user_id`, `REFERENCES public.users(id)`, `SET NOT NULL`, `idx_jobs_created_by`)
- `backend/jobs/router.py` — FOUND (contains `require_role`; org-scoped WHERE clauses; `created_by_user_id` + `created_by_email` in list_jobs response)
- `backend/billing/router.py` — FOUND (4 endpoints depend on `require_role("owner")`; `_resolve_stripe_customer(org_id)`)
- `backend/billing/stripe_client.py` — FOUND (`get_or_create_customer(email, org_id, org_name, pool)`; reads/writes `public.organizations`)
- `backend/webhooks/router.py` — FOUND (`JOIN public.organizations o ON o.id = j.organization_id` present)
- `backend/user/router.py` — FOUND (`from auth.org_dependencies import get_active_org`; `WHERE organization_id = $1` in /user/usage)
- `backend/auth/router.py` — FOUND (`INSERT INTO public.organizations`, `INSERT INTO public.organization_memberships`, `is_personal`, `(Personal)` suffix, try/except wrap)
- `backend/jobs/dispatch.py` — FOUND (signature accepts `organization_id`, `created_by_user_id`)
- `backend/jobs/service.py` — FOUND (`JOIN public.organizations` in billing block)
- All 5 new test files — FOUND
- `backend/tests/organizations/test_permissions.py` — FOUND (xfail removed; 3 new parametrized matrices)
- Existing single-tenant test repairs — FOUND in test_cancel/test_download/test_status_stream/test_user_router
- Commits `9b3fbd9` (Task 1) and `5e7d815` (Task 2) — FOUND in `git log --oneline`
- Negated grep `SELECT stripe_customer_id FROM public.users` — 0 matches in backend/{jobs,billing,webhooks,user}/
- Compile checks — all 13 touched files `python -m py_compile` exit 0
- Pytest collection — 11 new tests collect cleanly
- Full backend pytest suite — 362 passed / 19 skipped / 3 xfailed / 0 failed

---
*Phase: 12-teams-and-organizations*
*Completed: 2026-06-04*
