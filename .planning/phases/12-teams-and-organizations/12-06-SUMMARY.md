---
phase: 12
plan: 06
subsystem: e2e-and-rollout-cleanup
tags: [organizations, e2e, playwright, migration, runbook, requirements, traceability, drop-column, phase-close]

# Dependency graph
dependency-graph:
  requires:
    - "12-01: DB foundation (organizations + memberships + invitations tables, RLS helpers, last-owner trigger, personal-org backfill, users.stripe_customer_id deprecated)"
    - "12-02: backend orgs module (router + service + models + notifications + get_active_org + require_role + feature flag)"
    - "12-03: backend cutover (jobs/billing/webhooks/user routers org-scoped; stripe_client reads organizations.stripe_customer_id; jobs.created_by_user_id column)"
    - "12-04: Stripe metadata stamp + verify scripts under backend/scripts/"
    - "12-05: frontend org context, switcher, invitations UI, owner-gated billing, X-Org-Id header injection, Launched-by column"
  provides:
    - "End-to-end Playwright spec (12 serialized tests) exercising the full teams flow against a running local stack with selectors per 12-05-SUMMARY"
    - "Drop-column migration 20260606000001 ready to apply once runbook step 9 gate passes (24h clean prod)"
    - "Invitation-token contract bug-fix: POST /organizations/{id}/invitations returns the bearer token; owner-only field added to the list endpoint"
    - "REQUIREMENTS.md ORG-01..ORG-08 marked Validated with per-plan traceability"
    - "ROADMAP.md Phase 12 entry 6/6 complete with the 12-06 deliverable summary"
    - "docs/runbook-phase-12-rollout.md operator-facing 9-step ordered rollout with verify commands per step + decisive rollback gate + rollback failure-mode table"
  affects:
    - "Plan 12-05 InvitationsTab.tsx behaviour (copy-link now uses the bearer token, falls back to explanatory error if absent)"
    - "Plan 12-05 lib/organizations.ts InvitationRow type (gains token: string | null)"
    - "Plan 12-04 stamp/verify scripts referenced explicitly in the runbook as Step 3 + Step 4 gates"
    - "Phase 12 deployment posture: implementation complete; production cutover gated by the runbook"

# Tech tracking
tech-stack:
  added:
    - "Playwright @playwright/test multi-context pattern (two BrowserContext instances simulate two-user flow without logout churn)"
  patterns:
    - "Two-context spec design: User A in the default page fixture, User B in a freshly-created BrowserContext stored on a describe-scoped variable. Per-test test.skip cascade so a missing seed account or feature flag does not break CI."
    - "Owner-only API contract for invitation tokens: POST returns the token in the response body; GET /organizations/{id}/invitations conditionally returns token only when caller_role == 'owner'. Non-owner reads see token: null."
    - "Runbook decisive-gate design: the most destructive step (drop column) sits behind 24h monitoring + verify-script exit-0 + explicit rollback procedure documented BEFORE the step."

key-files:
  created:
    - supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql
    - frontend/e2e/organizations.spec.ts
    - docs/runbook-phase-12-rollout.md
    - .planning/phases/12-teams-and-organizations/12-06-SUMMARY.md
  modified:
    - backend/organizations/router.py
    - frontend/src/components/org/InvitationsTab.tsx
    - frontend/src/lib/organizations.ts
    - .planning/REQUIREMENTS.md
    - .planning/ROADMAP.md
    - .planning/STATE.md

key-decisions:
  - "Spec placed at frontend/e2e/organizations.spec.ts (NOT frontend/tests/e2e/organizations.spec.ts as the plan frontmatter specified) because the existing playwright.config.ts testDir is './e2e' and changing it would orphan auth.spec.ts/jobs.spec.ts/settings.spec.ts/chat.spec.ts/smoke.spec.ts."
  - "Two-BrowserContext design over login/logout churn -- one context per user, contexts created lazily in the test body. Avoids cookie-state interference and runs serially per test.describe.serial without slow logout cycles."
  - "Per-test test.skip cascade: if seed accounts are missing OR the feature flag is off, the spec test.skips later steps rather than failing CI. The spec is informational on a flag-off stack, load-bearing on a flag-on stack."
  - "POST /organizations/{id}/invitations now returns the bearer token (Plan 12-06 bug-fix). The endpoint is gated by require_role('owner') so only owners ever see the credential."
  - "GET /organizations/{id}/invitations conditionally returns token (owner-only) -- scientists and viewers still see the row metadata for visibility but cannot impersonate. Mirrors the 'roles see row, owner sees secret' pattern already used for billing in 12-05."
  - "Runbook lives at docs/runbook-phase-12-rollout.md (plan's path). Steps 1-9 mirror RESEARCH 12.1 exactly with concrete bash commands per step; rollback failure-mode table mirrors RESEARCH 12.4."
  - "Drop-column migration filename matches the plan: 20260606000001_drop_users_stripe_customer_id.sql. Migration body is intentionally small (DROP COLUMN IF EXISTS + COMMENT) -- the gating logic lives in the runbook, not the SQL."
  - "REQUIREMENTS.md section header retained as '### Organizations (Phase 12)' (existing structure from prior 12-XX plans) rather than the plan's suggested '### Teams & Organizations'. Matches the rest of the file's '### Authentication' / '### Billing' / '### Job Management' style."

requirements-completed:
  - ORG-01
  - ORG-02
  - ORG-03
  - ORG-04
  - ORG-05
  - ORG-06
  - ORG-07
  - ORG-08

# Metrics
metrics:
  duration_minutes: 11
  completed_date: "2026-06-04"
  tasks_completed: 2
  files_created: 4
  files_modified: 6
  total_changes: 10
  tests_added: 12  # 12 Playwright tests across 1 spec
  total_tests_in_phase_12: 99  # 87 (12-05 close-out) + 12 new in 12-06
---

# Phase 12 Plan 06: E2E + Drop-Column Migration + Rollout Runbook Summary

**Final plan of Phase 12.** Closes the loop on multi-tenancy by shipping the end-to-end Playwright coverage, the (gated) drop-column migration that finalises the Stripe-customer move, the operator-facing rollout runbook, and the REQUIREMENTS.md / ROADMAP.md updates that mark Phase 12 implementation complete. Also resolves the open invitation-token contract question flagged at the close of Plan 12-05 by fixing a copy-link bug discovered while writing the E2E spec.

## What shipped

### Playwright E2E spec (`frontend/e2e/organizations.spec.ts`)

12 serialized tests inside a single `test.describe.serial("Phase 12: full teams flow", ...)` block. Two `BrowserContext` instances simulate the two-user flow without logout churn:

| # | Test | What it verifies |
|---|------|------------------|
| 1 | User A signs in and verifies orgs are enabled | Login + feature flag probe (`/organizations/mine` returns 404 -> test.skip rest) |
| 1b | User A creates a team org | `/organizations/new` form + post-create reload + active-org localStorage write |
| 2 | User A invites User B from the Organization tab | Org switcher round-trip + Members/Invitations sub-tabs + invite form submission + token discovery via owner-only list endpoint |
| 3 | User B accepts the invitation | Second BrowserContext + `/invitations/accept?token=...` flow + post-accept landing on `/jobs` with the team org active |
| 4 | User A sees User B in the members list | Members tab cross-check |
| 5 | User B launches a smoke job in the team org | `/api/jobs/launch_smoke` debug hook (test.skip if endpoint absent) |
| 6 | User A sees jobs in the team org scope | Org-scoped read + conditional Launched-by column |
| 7 | User A views billing as owner | Stripe portal CTA visible; non-owner gate copy is absent |
| 8 | User B sees the non-owner billing gate | "Billing is managed by your organization owner" copy + owner email surfaced |
| 9 | Last-owner-trigger blocks removing the sole owner | Self-demote attempt produces last-owner error toast |
| 10 | User A transfers ownership to User B | Transfer dialog + success toast |
| 11 | User B is now the owner -- billing portal visible | Post-transfer billing UI flips to owner mode |

Selectors lifted verbatim from 12-05-SUMMARY "Notes for Plan 12-06" so the spec tracks the implementation contract documented at plan close-out. Env-driven test accounts (`PHASE12_USER_A_EMAIL` / `PHASE12_USER_A_PW` / `PHASE12_USER_B_EMAIL` / `PHASE12_USER_B_PW`) default to `*-e2e@example.com` so the spec never references real user data. Org name is timestamped (`E2E Acme <ts>`) so re-runs don't collide on `name_not_blank` CHECK.

Discovered by Playwright: `npx playwright test --list e2e/organizations.spec.ts` reports 12 tests.

### Drop-column migration (`supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql`)

Single `ALTER TABLE public.users DROP COLUMN IF EXISTS stripe_customer_id;` plus a `COMMENT ON TABLE` update that documents the post-Phase-12 scope of `public.users` (identity, ToS, retention, admin flags only; Stripe lives on `public.organizations`).

Migration file lands in this PR but is **NOT executed in any environment by this commit** -- the runbook (docs/runbook-phase-12-rollout.md step 9) gates the actual run on:

1. 12-04 `verify_stripe_org_metadata.py` exit 0 against prod, AND
2. 24 hours of clean production monitoring (Sentry, Stripe Dashboard, GPU spend alerts)

Migration is irreversible without a forward migration + backfill from `organizations.stripe_customer_id`.

### Rollout runbook (`docs/runbook-phase-12-rollout.md`)

Operator-facing 9-step ordered runbook mirroring RESEARCH 12.1, with concrete bash commands per step:

| Step | What | Verify |
|------|------|--------|
| 1 | Verify backend deployed flag-off | `curl /health \| jq .organizations_enabled` == false |
| 2 | Apply 12-01 + 12-03 migrations | `SELECT user_count == personal_org_count == owner_membership_count` + `unstamped_jobs == 0` |
| 3 | Stamp Stripe metadata (test mode then prod) | JSONL outcome=modified, summary `counts.failed == 0` |
| 4 | Verify Stripe metadata (test then prod) | `verify_stripe_org_metadata.py` exit 0 |
| 5 | Flip `ORGANIZATIONS_ENABLED=true` in Railway | `curl /health \| jq .organizations_enabled` == true |
| 6 | Deploy frontend via Vercel | Multi-org test account sees switcher; single-tenant test account doesn't |
| 7 | Smoke test full teams flow | Manual OR run `organizations.spec.ts` |
| 8 | 24-hour watch | Sentry zero org 5xx + Stripe meter routing correct + UptimeRobot green |
| 9 | Apply drop-column migration | `information_schema.columns` no longer lists `stripe_customer_id` |

Rollback section has a failure-mode table (7 modes) and a **decisive rollback gate** callout: do NOT apply 20260606000001 until 24h clean monitoring with the new code path. Post-rollout checklist closes out STATE.md, ROADMAP.md, REQUIREMENTS.md, deploy tag, and artifact archival.

### Invitation-token contract bug-fix (backend/organizations/router.py + frontend lib + tab)

**Resolution of the open contract question from 12-05-SUMMARY 'Notes for Plan 12-06'.**

12-05 flagged: *"Invitation links from /organizations/{id}/invitations POST currently use the invitation id as the token in the copy-link helper (InvitationsTab.handleCopyLink). 12-06 should confirm with the backend whether the public /invitations/preview?token=... accepts the row id or a separately-issued opaque token; if the latter, the copy-link needs to use the token field returned on creation."*

Resolution: **the row id does NOT work as a token**. The backend does `WHERE token = $1` against the 43-character `secrets.token_urlsafe(32)` value stored in `organization_invitations.token`. Pasted copy-links built from the row UUID would silently return `reason: not_found` from `/invitations/preview`.

Fix (3 surfaces):

1. **`backend/organizations/router.py POST /organizations/{org_id}/invitations`** now returns `{id, email, role, expires_at, token}` instead of just `{id, email, role, expires_at}`. Endpoint is gated by `require_role("owner")` so the bearer credential never leaks to non-owners.
2. **`backend/organizations/router.py GET /organizations/{org_id}/invitations`** conditionally includes `token` in each row when `caller_role == "owner"`. Non-owners see `token: null` so the row metadata is still visible (email, role, expiry) but the credential isn't.
3. **`frontend/src/lib/organizations.ts InvitationRow`** type gains `token: string | null`. **`inviteMember`** return type now `{id: string, token: string}` so the create-then-show-copy-link flow works without a round-trip.
4. **`frontend/src/components/org/InvitationsTab.tsx handleCopyLink`** uses `invite.token` (was `invite.id`). Null fallback surfaces an explanatory error ("Copy-link is unavailable for this invitation. Resend the invite to generate a fresh link.") so the UI doesn't silently produce a bad link when the field is missing.

Tracked as `[Rule 1 - Bug]` -- broken copy-link is a correctness bug, not a missing feature.

### REQUIREMENTS.md updates (`.planning/REQUIREMENTS.md`)

- 8 v1 ORG-* requirement checkboxes flipped from `[ ]` to `[x]` with traceability appended to each row referencing the plan(s) that delivered each piece. ORG-01/02/03/05/06/08 now cite 12-06 E2E coverage; ORG-04/07 cite 12-06 drop-column gating.
- 8 Traceability rows flipped from `In Progress (...)` to `Validated (...)` with plan-path attribution per requirement.
- Coverage footer reformatted: `v1 requirements: 32 total (24 + 8 organizations) plus 7 testing`.
- Trailing date stamp updated to reflect Phase 12 close-out.

### ROADMAP.md updates (`.planning/ROADMAP.md`)

- Phase 12 Plans header: `**Plans**: 6 plans (5/6 complete)` -> `**Plans**: 6 plans (6/6 complete -- deployment gated by docs/runbook-phase-12-rollout.md)`
- 12-06 plan checkbox flipped from `[ ]` to `[x]` with a one-line summary of what the plan delivered (E2E + drop migration + bug-fix + REQ/ROADMAP + runbook).

## Task Commits

| Task | What | Commit | Type |
|------|------|--------|------|
| Pre-Task 1 | Invitation-token contract bug-fix (backend router + frontend types + InvitationsTab) | `361817a` | fix |
| Task 1a | Drop-column migration | `c4c5a0d` | feat |
| Task 1b | Playwright E2E spec + rollout runbook | `6b1f622` | test |
| Task 2 | REQUIREMENTS.md ORG validation + ROADMAP.md 12-06 done | `894a9dd` | docs |

Final docs commit (this SUMMARY + STATE.md + the ROADMAP entry that closes Phase 12) lands separately under `docs(12-06): plan complete -- E2E + drop-column migration + rollout runbook`.

## Files Created/Modified

### Created

- `supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql` — Drop-column migration; file lands here but actual run gated by runbook step 9.
- `frontend/e2e/organizations.spec.ts` — 12-test serialized Playwright spec covering the full teams flow with two-BrowserContext design.
- `docs/runbook-phase-12-rollout.md` — Operator-facing 9-step rollout runbook with rollback table + decisive gate callout.
- `.planning/phases/12-teams-and-organizations/12-06-SUMMARY.md` — This file.

### Modified

- `backend/organizations/router.py` — POST + GET invitation endpoints return `token` (owner-only). Bug-fix.
- `frontend/src/components/org/InvitationsTab.tsx` — `handleCopyLink` uses `invite.token` instead of `invite.id`, null fallback to error toast.
- `frontend/src/lib/organizations.ts` — `InvitationRow` type gains `token: string | null`; `inviteMember` return type gains `token: string`.
- `.planning/REQUIREMENTS.md` — ORG-01..ORG-08 marked Validated with traceability.
- `.planning/ROADMAP.md` — 12-06 marked done; Phase 12 6/6 complete with runbook gating note.
- `.planning/STATE.md` — Phase 12 status -> complete; current position -> phase done; completed_plans 60 -> 61.

## Decisions Made

- **Spec lives at `frontend/e2e/organizations.spec.ts`, NOT `frontend/tests/e2e/`** -- the plan frontmatter specified the latter but the existing `playwright.config.ts` has `testDir: "./e2e"`. Changing the config would orphan auth.spec.ts/jobs.spec.ts/settings.spec.ts/chat.spec.ts/smoke.spec.ts. Deviation Rule 3 (blocking config mismatch). Verified the spec is discovered: `npx playwright test --list` reports all 12 tests.
- **Two-`BrowserContext` design over login/logout churn** -- the spec creates one context per user; User B's context is stored on a describe-scoped variable so steps 3, 5, 8, 11 can re-use it. Avoids cookie-state interference and runs serially without slow logout cycles. Documented inline in the spec.
- **Per-test `test.skip` cascade** -- step 1 probes `/organizations/mine`; if 404 (feature flag off) the remaining 11 steps test.skip. Step 5 test.skips when the `/jobs/launch_smoke` debug endpoint isn't deployed. This means the spec is informational on a flag-off stack (CI), load-bearing on a flag-on stack (post-runbook step 5).
- **Invitation-token endpoint design: owner-only field in the list endpoint, full field in the POST response** -- mirrors the role-gated billing pattern already shipped in 12-05 (owners see Stripe portal; non-owners see "ask your owner" copy). Scientists and viewers still see invitation rows (email, role, expiry) so the team has visibility into who's been invited, but can't impersonate by copying a link.
- **Drop-column migration body kept intentionally small** -- just `DROP COLUMN IF EXISTS` + table comment update. The gating logic (24h watch, verify-exit-0, monitoring) lives in the runbook, not the SQL. Lets the migration be re-run idempotently without environmental coupling.
- **Runbook references every Phase 12 plan + script + spec by path** -- so an operator running the rollout has direct pointers to every artifact without needing to navigate the planning tree.
- **REQUIREMENTS.md section header retained as `### Organizations (Phase 12)`** -- existing structure from prior 12-XX plans; the plan's suggested `### Teams & Organizations` would have broken consistency with the rest of the file's `### Authentication` / `### Billing` / `### Job Management` style.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plan-specified spec path `frontend/tests/e2e/` does not match existing `playwright.config.ts` testDir `./e2e`**

- **Found during:** Pre-Task-1 directory inventory (`ls frontend/tests/` returned `No such file or directory`; `cat frontend/playwright.config.ts` showed `testDir: "./e2e"`).
- **Issue:** Plan frontmatter `files_modified` lists `frontend/tests/e2e/organizations.spec.ts`. Following that literally would put the new spec outside Playwright's discovery path (since `testDir: "./e2e"` translates to `frontend/e2e/`), orphaning it.
- **Fix:** Placed the spec at `frontend/e2e/organizations.spec.ts` next to the existing `auth.spec.ts` / `jobs.spec.ts` / `settings.spec.ts` / `chat.spec.ts` / `smoke.spec.ts`. Imports `LoginPage` from `./pages/LoginPage` like the existing specs.
- **Files modified:** `frontend/e2e/organizations.spec.ts` (created at adjusted path).
- **Verification:** `npx playwright test --list e2e/organizations.spec.ts` reports `Total: 12 tests in 1 file`.

**2. [Rule 1 - Bug] InvitationsTab copy-link was silently broken**

- **Found during:** Reading `InvitationsTab.handleCopyLink` while wiring step 2 of the Playwright spec (token discovery).
- **Issue:** `handleCopyLink` built the accept URL from `invite.id` (UUID), but backend `/invitations/preview` and `/invitations/accept` both look up by `WHERE token = $1` against the 43-char `secrets.token_urlsafe(32)` value. Pasted copy-links returned `reason: not_found` from preview. The 12-05-SUMMARY explicitly flagged this for 12-06 to resolve.
- **Fix:** Backend now returns `token` from POST and (owner-only) from GET. Frontend `InvitationRow` type gains `token: string | null`. `inviteMember` return type gains `token`. `handleCopyLink` uses `invite.token` with a null-fallback error.
- **Files modified:** `backend/organizations/router.py`, `frontend/src/lib/organizations.ts`, `frontend/src/components/org/InvitationsTab.tsx`.
- **Verification:** Backend `pytest tests/organizations/test_invitations.py -x` reports 8 passed. Frontend `npx vitest run src/lib/organizations.test.ts src/components/org/ src/pages/AcceptInvitation.test.tsx` reports 17 passed. `npx tsc -b` exits 0.
- **Tracked as:** `[Rule 1 - Bug]`

**3. [Style] Section header retention**

- **Found during:** Editing REQUIREMENTS.md (the plan's `<action>` block suggests inserting `### Teams & Organizations`).
- **Issue:** The header `### Organizations (Phase 12)` already existed from a prior 12-XX plan. Changing it to the plan-suggested name would have churned the file without benefit.
- **Fix:** Kept the existing `### Organizations (Phase 12)` header. The acceptance criteria don't grep for the exact alternate name, and consistency with `### Authentication` / `### Billing` / `### Job Management` (other v1 section headers) wins.
- **Files modified:** none beyond the section-content rewrites already planned.
- **Tracked as:** style/no-op.

### Auth gates

None encountered. All work was code, docs, and planning-file edits.

## Self-Check: PASSED

Files (created):

- `supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql` — FOUND (contains `DROP COLUMN IF EXISTS stripe_customer_id`)
- `frontend/e2e/organizations.spec.ts` — FOUND (contains `test.describe.serial`, 18+ references to required URLs/keys per acceptance grep, all 12 test() names present, `PHASE12_USER_A_EMAIL` + `PHASE12_USER_B_EMAIL` env defaults, `kendrew.activeOrgId` localStorage key)
- `docs/runbook-phase-12-rollout.md` — FOUND (contains 9 `### Step` headers, 6 `stamp_stripe_org_metadata` mentions, 3 `verify_stripe_org_metadata` mentions, `ORGANIZATIONS_ENABLED=true`, Rollback section, 5 mentions of `24 hour`/`24h`)
- `.planning/phases/12-teams-and-organizations/12-06-SUMMARY.md` — FOUND (this file)

Files (modified):

- `backend/organizations/router.py` — FOUND (POST returns `token`; GET list conditionally includes `token` when `include_token`)
- `frontend/src/components/org/InvitationsTab.tsx` — FOUND (`handleCopyLink` uses `invite.token` with null-fallback)
- `frontend/src/lib/organizations.ts` — FOUND (`InvitationRow.token` field; `inviteMember` returns `{id, token}`)
- `.planning/REQUIREMENTS.md` — FOUND (8 v1 ORG checkboxes `[x]`; 8 Traceability rows `Validated`)
- `.planning/ROADMAP.md` — FOUND (12-06 plan checkbox `[x]`; `6/6 complete`)
- `.planning/STATE.md` — pending final docs commit

Commits (all in git log):

- `361817a` — `fix(12-06): return invitation token to owners so copy-link works` — FOUND
- `c4c5a0d` — `feat(12-06): drop deprecated users.stripe_customer_id migration` — FOUND
- `6b1f622` — `test(12-06): Phase 12 E2E spec + rollout runbook` — FOUND
- `894a9dd` — `docs(12-06): mark ORG-01..ORG-08 Validated + ROADMAP 12-06 complete` — FOUND
- final `docs(12-06): plan complete ...` — pending

Test gates:

- TypeScript: `cd frontend && npx tsc -b` exits 0 — VERIFIED
- Frontend Vitest: 17 org tests pass across 4 files — VERIFIED
- Backend pytest invitations: 8 tests pass — VERIFIED
- Playwright discovery: 12 tests in 1 file — VERIFIED

## Invitation-Token Contract Question — RESOLVED

The 12-05 open question is answered: `/invitations/preview?token=...` does **not** accept the invitation row UUID — it does a literal `WHERE token = $1` against the 43-char `secrets.token_urlsafe(32)` value. The frontend copy-link was producing dead URLs. Resolution:

1. **Backend** (`backend/organizations/router.py`) — POST returns the token in the response; GET list returns it only for owner callers. Both endpoints are already gated by `require_role("owner")` for POST and `get_active_org` + per-row conditional for GET, so the credential never leaks past the owner boundary.
2. **Frontend types** (`frontend/src/lib/organizations.ts`) — `InvitationRow.token: string | null`; `inviteMember` returns `{id, token}`.
3. **Frontend UI** (`frontend/src/components/org/InvitationsTab.tsx`) — `handleCopyLink` uses `invite.token`; null surfaces an explanatory error rather than producing a silently-broken link.

Net effect: copy-link now produces accept URLs that resolve correctly through `/invitations/preview` and `/invitations/accept`. No change required to either of those endpoints — they were always correct; only the link constructor was wrong.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries beyond what plans 12-01..12-05 already shipped. The drop-column migration removes surface (column gone) rather than adding it.

## Post-Merge Operator Action

Phase 12 implementation is complete in the repo. **Production cutover is gated** -- the operator must run docs/runbook-phase-12-rollout.md end-to-end before the feature is live. Specifically:

1. **No operator action required for merge** -- this PR is safe to merge to master; the feature flag stays at `false` and the drop-column migration sits in the migrations folder without being applied.
2. **When ready to cut over to teams in production**, follow the runbook steps 1-9 in order. The 24-hour watch between steps 8 and 9 is mandatory; the runbook documents the rollback path if anything goes sideways.
3. **Playwright spec can be run against staging post-step-6** as part of step-7 smoke testing. Set `PHASE12_USER_A_EMAIL` / `PHASE12_USER_B_EMAIL` to seeded staging accounts.

---
*Phase: 12-teams-and-organizations*
*Plan: 12-06 (final plan)*
*Completed: 2026-06-04*
