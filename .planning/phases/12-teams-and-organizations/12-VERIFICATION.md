---
status: gaps_found
phase: 12-teams-and-organizations
verified: 2026-06-04T23:55:00Z
must_haves_score: 41/43
requirements: [ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, ORG-08]
overrides_applied: 0
gaps:
  - truth: "Drop-column migration (12-06) can be safely applied per the runbook without breaking other code paths"
    status: failed
    reason: "deferred-items.md (created by 12-03) explicitly flagged that backend/worker/deletion_cron.py and backend/admin/router.py still read users.stripe_customer_id, and that the migration to organizations.stripe_customer_id was deferred to 12-06. Plan 12-06 did NOT close these items. Codebase still references the deprecated column in both files; the drop-column migration in 20260606000001 will SUCCEED but the next deletion_cron run and any /admin/users list will then raise asyncpg.exceptions.UndefinedColumnError."
    artifacts:
      - path: "backend/worker/deletion_cron.py"
        issue: "Lines 42-58 SELECT stripe_customer_id FROM public.users; execute_hard_delete receives the value to clean up the Stripe customer on GDPR hard-delete. Drop migration removes the column without migrating this caller."
      - path: "backend/admin/router.py"
        issue: "Lines 108, 117, 129, 137, 151 reference u.stripe_customer_id in the user-list query and GROUP BY; payment_status field derives from it. Drop migration breaks the admin users list."
      - path: ".planning/phases/12-teams-and-organizations/deferred-items.md"
        issue: "Documents both call sites + the planned migration path (read through organization_memberships -> personal org's organizations.stripe_customer_id) but 12-06 left both call sites untouched."
    missing:
      - "Rewrite backend/worker/deletion_cron.py:42-58 to JOIN through organization_memberships (role='owner') to organizations.stripe_customer_id for personal orgs"
      - "Rewrite backend/admin/router.py:108-151 same way (JOIN to personal org's organizations.stripe_customer_id; collapse payment_status derivation)"
      - "Add a regression test that asserts deletion_cron processes a soft-deleted user with a stamped personal-org Stripe customer"
      - "Either remove deferred-items.md (now stale) or add a note marking the two items as still-open"
  - truth: "Runbook Step 1 verify command (curl /health | jq .organizations_enabled) succeeds against the actual /health endpoint"
    status: failed
    reason: "docs/runbook-phase-12-rollout.md step 1 expects /health to return an organizations_enabled field, but backend/main.py:146-175 health() returns only {api, db, redis} status keys. The verify command will produce 'null' for both flag-off and flag-on backends. Operator may misread this as the flag being off when in reality the field simply does not exist."
    artifacts:
      - path: "backend/main.py"
        issue: "Lines 146-175 health() returns {api, db, redis} without organizations_enabled"
      - path: "docs/runbook-phase-12-rollout.md"
        issue: "Steps 1 + 5 verify commands assume a field that does not exist"
    missing:
      - "Either add organizations_enabled to the /health JSON response (one line: checks['organizations_enabled'] = settings.organizations_enabled), OR"
      - "Update runbook step 1 + step 5 verify to probe a route that does exist (e.g. curl /organizations/mine -- 404 == flag off, 200/401 == flag on)"
deferred: []
---

# Phase 12: Teams & Organizations — Verification Report

**Phase Goal:** Multi-user accounts, team billing, shared job history, role-based access (admin/scientist/viewer). Biopharma teams can use the platform under a shared organization with centralized billing.
**Verified:** 2026-06-04 (post Plan 12-06 close-out)
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

The phase delivers the **architectural and code foundation** for multi-tenancy end-to-end:

- A scientist CAN belong to multiple orgs (`GET /organizations/mine` returns list, `frontend/src/lib/api.ts` + `OrganizationContext.tsx` switch via `localStorage[kendrew.activeOrgId]`).
- A scientist CAN switch between them (`OrganizationSwitcher.tsx` dropdown; reload-on-switch).
- A scientist CAN invite a teammate (`POST /organizations/{id}/invitations` + Resend email via `notifications.send_invitation_email`).
- A teammate CAN accept (`POST /invitations/accept` + `frontend/src/pages/AcceptInvitation.tsx`).
- Jobs CAN be launched scoped to the active org (`backend/jobs/router.py` line 110 `org_id: str = Depends(require_role("owner", "scientist"))`).
- Stripe billing reads from the org (`backend/billing/stripe_client.py` line 49 reads `organizations.stripe_customer_id`).
- RBAC is enforced (`backend/auth/org_dependencies.py` `require_role(*allowed)` + DB-level `protect_last_owner_trigger` and `jobs_org_members` / `jobs_write_active` / `jobs_update_active` RLS policies).

**However, the phase is gated on a runbook-driven production rollout**, and TWO real issues will surface when that rollout reaches the drop-column step:

1. `deletion_cron.py` and `admin/router.py` still read `users.stripe_customer_id`. The drop migration will break both. The 12-03 `deferred-items.md` flagged this for 12-06 but 12-06 did not close it.
2. The runbook's `/health` probe references an `organizations_enabled` field that the endpoint does not expose.

Neither blocks the **code-complete** judgment of Phase 12 in the repo, but both prevent the runbook from running cleanly end-to-end as written. They are pre-deployment correctness issues.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can belong to multiple orgs | VERIFIED | `backend/organizations/router.py:50-75` GET /organizations/mine; `frontend/src/components/org/OrganizationContext.tsx:1-205` |
| 2 | User can switch between orgs | VERIFIED | `frontend/src/components/org/OrganizationSwitcher.tsx:1-115`; `frontend/src/lib/api.ts:16-72` X-Org-Id injection from `localStorage[kendrew.activeOrgId]` |
| 3 | User can invite a teammate by email | VERIFIED | `backend/organizations/router.py:411-455` POST /organizations/{id}/invitations + `notifications.send_invitation_email`; `frontend/src/components/org/InvitationsTab.tsx` |
| 4 | Teammate can accept an invite | VERIFIED | `backend/organizations/router.py:483-496` POST /invitations/accept; `frontend/src/pages/AcceptInvitation.tsx` 4-branch handling |
| 5 | Job launches bill the org's Stripe customer | VERIFIED | `backend/jobs/router.py:110` require_role; `backend/billing/stripe_client.py:48-65` reads org.stripe_customer_id |
| 6 | Jobs scoped to active org are visible to all members | VERIFIED | `backend/jobs/router.py:420` WHERE j.organization_id = $1; `supabase/migrations/20260605000001_organizations.sql:255-269` jobs_org_members SELECT policy via is_member_of() |
| 7 | Role-based access enforced (owner/scientist/viewer) | VERIFIED | `backend/auth/org_dependencies.py:66-93` require_role; `migrations/20260605000001_organizations.sql:14-21` ENUM with 3 values; jobs_write_active gates owner/scientist (line 261-269) |
| 8 | Last owner cannot leave an organization | VERIFIED | `migrations/20260605000001_organizations.sql:95-123` protect_last_owner BEFORE UPDATE OR DELETE trigger + plpgsql function; `tests/integration/test_last_owner_trigger.py` 3 tests |
| 9 | Personal org backfill — existing users migrated | VERIFIED | `migrations/20260605000001_organizations.sql:216-243` DO block iterates public.users; `tests/integration/test_org_migration.py:442-458` asserts user_count == personal_owner_count |
| 10 | Stripe customer_id moved from users to orgs | VERIFIED | `migrations/20260605000001_organizations.sql:222-235` copies u.stripe_customer_id to new org row; `backend/billing/stripe_client.py:48-65` reads org column |
| 11 | Last-owner trigger is BEFORE UPDATE OR DELETE | VERIFIED | `migrations/20260605000001_organizations.sql:120-123` `BEFORE UPDATE OR DELETE ON public.organization_memberships FOR EACH ROW EXECUTE FUNCTION public.protect_last_owner()` |
| 12 | RLS helpers are PL/pgSQL (not SQL — inlining recursion gotcha) | VERIFIED | `migrations/20260605000001_organizations.sql:134-164` is_member_of + has_role_in both `LANGUAGE plpgsql STABLE SECURITY DEFINER`; zero `LANGUAGE sql` occurrences |
| 13 | jobs_own dropped; jobs_org_members + jobs_write_active + jobs_update_active exist | VERIFIED | `migrations/20260605000001_organizations.sql:255-269` |
| 14 | Feature flag settings.organizations_enabled defaults to False | VERIFIED | `backend/config.py:149` `organizations_enabled: bool = False`; `backend/main.py:129` mount gated |
| 15 | X-Org-Id injected by frontend on org-scoped routes | VERIFIED | `frontend/src/lib/api.ts:56-72` shouldSendOrgHeader + opt-out list; line 176-181 attaches header |
| 16 | Invitation-token bug fix (12-06): backend returns token; owner-only on list | VERIFIED | `backend/organizations/router.py:382` `include_token = caller_role == "owner"`; line 404 conditional token field; line 454 POST returns token |
| 17 | Playwright spec exists with 12 tests | VERIFIED | `frontend/e2e/organizations.spec.ts:139` test.describe.serial; 12 `test("...")` declarations at lines 152, 161, 181, 225, 254, 265, 307, 330, 348, 362, 390, 410 |
| 18 | Wave 0 fixtures expose org_factory / member_factory / invitation_factory | VERIFIED | `backend/tests/organizations/conftest.py:42, 69, 94` all three factories |
| 19 | Wave 0 integration tests cover migration backfill + last-owner + RLS isolation | VERIFIED | 3 files `tests/integration/test_org_migration.py`, `test_last_owner_trigger.py`, `test_rls_jobs_org.py`; pytest --collect-only reports 71 tests across 12-02 + 12-03 + integration set |
| 20 | Stripe metadata stamp + verify scripts exist + idempotent | VERIFIED | `backend/scripts/stamp_stripe_org_metadata.py` 226 lines with --dry-run + --test-mode; `verify_stripe_org_metadata.py` 110 lines with exit-code contract |
| 21 | users.stripe_customer_id NOT dropped in 12-01 migration (drop deferred) | VERIFIED | `migrations/20260605000001_organizations.sql:298-300` only adds COMMENT marking DEPRECATED |
| 22 | Drop migration file exists, gated by runbook step 9 | VERIFIED | `migrations/20260606000001_drop_users_stripe_customer_id.sql:1-41` includes 4-condition gate comment + ALTER TABLE DROP COLUMN; runbook step 9 enforces 24h+verify-exit-0 |
| 23 | Frontend Launched-by column conditional on activeOrg && !is_personal | VERIFIED | `frontend/src/pages/JobHistoryPage.tsx` per 12-05 SUMMARY; backend `jobs/router.py:420` includes j.created_by_user_id |
| 24 | jobs.created_by_user_id column added with backfill | VERIFIED | `migrations/20260605000002_jobs_created_by.sql:1-13` ALTER ADD COLUMN + backfill from user_id + NOT NULL + index |
| 25 | Personal org auto-created at signup | VERIFIED | `backend/auth/router.py:149-178` INSERT organizations + memberships in transaction with email-prefix naming |
| 26 | Active-org cross-check rejects spoofed X-Org-Id | VERIFIED | `backend/auth/org_dependencies.py:46-63` HTTP 400 missing header + HTTP 403 not-a-member |
| 27 | Transfer-ownership endpoint atomic (promote-then-demote in single tx) | VERIFIED | `backend/organizations/router.py:324-344` delegates to `service.transfer_ownership`; tests in `tests/organizations/test_transfer_ownership.py` |
| 28 | Invitation tokens are secrets.token_urlsafe(32) | VERIFIED | `backend/organizations/service.py` `generate_invitation_token` (referenced from router.py:420) |
| 29 | Settings feature flag organizations_enabled gates orgs_router mount | VERIFIED | `backend/main.py:129-135` `if settings.organizations_enabled: include_router(orgs_router, invitations_router)` |
| 30 | candidates_org RLS policy scopes through jobs.organization_id | VERIFIED | `migrations/20260605000001_organizations.sql:277-283` |
| 31 | Drop-column gating chain: backend cutover -> stamp -> verify -> 24h watch | VERIFIED | `docs/runbook-phase-12-rollout.md:9-201` 9-step ordered runbook + rollback table |
| 32 | Drop-column migration safe to apply without breaking call sites | FAILED | deletion_cron.py:42-58 + admin/router.py:108-151 still read users.stripe_customer_id; deferred-items.md flagged this for 12-06 but 12-06 did not close it. See gaps.0. |
| 33 | Runbook /health verify command works | FAILED | backend/main.py:146-175 returns {api, db, redis} only; runbook step 1+5 jq .organizations_enabled returns null. See gaps.1. |

**Score:** 31/33 truths verified (94%). 2 truths failed; both relate to the runbook's terminal step (drop column) and one of the verify probes.

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `supabase/migrations/20260605000001_organizations.sql` | Schema foundation: ENUM, 3 tables, 3 functions, last-owner trigger, RLS rewrites, personal-org backfill | VERIFIED | 335 lines; all required DDL elements present; 4 LANGUAGE plpgsql occurrences; zero LANGUAGE sql; backfill uses `COALESCE(NULLIF(split_part(u.email, '@', 1), ''), 'Personal') || ' (Personal)'`; users.stripe_customer_id marked DEPRECATED not dropped |
| `supabase/migrations/20260605000002_jobs_created_by.sql` | jobs.created_by_user_id column + backfill + NOT NULL + index | VERIFIED | 13 lines; matches plan 12-03 contract |
| `supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql` | Drop deprecated users.stripe_customer_id; gated by runbook | VERIFIED | 41 lines; DROP COLUMN IF EXISTS + table comment update; gate documented in file header |
| `backend/organizations/router.py` | 6 routes + 2 routers (orgs + invitations); RBAC enforcement; SECURITY DEFINER RPC call; invitation token returned | VERIFIED | 543 lines; all 12 documented endpoints present; `include_token = caller_role == "owner"` at line 382 |
| `backend/organizations/service.py` | accept_invitation + generate_invitation_token + transfer_ownership business logic | VERIFIED | 195 lines |
| `backend/organizations/models.py` | Pydantic v2 models | VERIFIED | 99 lines |
| `backend/organizations/notifications.py` | send_invitation_email via Resend | VERIFIED | 74 lines |
| `backend/auth/org_dependencies.py` | get_active_org + require_role factory | VERIFIED | 93 lines; X-Org-Id header dep + membership cross-check + 400/403 contract |
| `backend/scripts/stamp_stripe_org_metadata.py` | One-shot stamper with --dry-run + --test-mode; idempotent | VERIFIED | 226 lines with full CLI + JSONL output + retry loop |
| `backend/scripts/verify_stripe_org_metadata.py` | Verifier with exit-code contract | VERIFIED | 110 lines |
| `frontend/src/lib/organizations.ts` | 13 typed API wrappers + types incl. token field | VERIFIED | 189 lines; InvitationRow.token: string\|null at line 45; inviteMember returns {id, token} at line 147 |
| `frontend/src/lib/api.ts` | X-Org-Id header injection with opt-out list | VERIFIED | line 16 storage key; lines 29-34 opt-out; lines 56-72 shouldSendOrgHeader; lines 176-181 header injection |
| `frontend/src/components/org/OrganizationContext.tsx` | useOrgContext provider + localStorage + reload-on-switch | VERIFIED | 205 lines |
| `frontend/src/components/org/OrganizationSwitcher.tsx` | shadcn dropdown switcher | VERIFIED | 115 lines |
| `frontend/src/components/org/MembersTab.tsx` | Members table + transfer + remove + invite UI | VERIFIED | 444 lines |
| `frontend/src/components/org/InvitationsTab.tsx` | Pending invitations table + copy-link via token | VERIFIED | 184 lines |
| `frontend/src/components/org/OrgSettingsTab.tsx` | Rename + delete (typed-confirmation) | VERIFIED | 199 lines |
| `frontend/e2e/organizations.spec.ts` | 12-test serialized Playwright spec | VERIFIED | 436 lines; test.describe.serial at line 139; 12 test() declarations |
| `docs/runbook-phase-12-rollout.md` | 9-step rollout + rollback table + decisive gate | VERIFIED | 247 lines; 9 steps + rollback table + decisive gate callout |
| Wave 0 test scaffolds (`tests/organizations/conftest.py` + 3 integration tests) | Factories + invariant tests | VERIFIED | conftest 117 lines with all 3 factories; 3 integration tests with required test function names |
| 8 unit-test files under `tests/organizations/` per Plan 12-02 | Comprehensive RBAC + dependency + endpoint coverage | VERIFIED | 11 files (8 from 12-02 + 2 from 12-03 + conftest); 71 tests collect cleanly |

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| jobs/router.py | auth/org_dependencies.py | `Depends(require_role(...))` | WIRED | line 24 import; 5 uses of `Depends(require_role(...))` at lines 110, 360, 456, 510, 564, 673 |
| billing router/client | organizations.stripe_customer_id | direct SELECT/UPDATE | WIRED | `stripe_client.py:48-65` reads/writes through orgs; billing router resolves via `_resolve_stripe_customer(org_id)` |
| backend/main.py | organizations.router | feature-flag-gated `include_router` | WIRED | line 129 `if settings.organizations_enabled` |
| frontend api.ts | backend X-Org-Id contract | header injection on shouldSendOrgHeader() | WIRED | localStorage read + header attach + 403 stale-org sweep |
| InvitationsTab copy-link | backend invitation token | `invite.token` field on response | WIRED | bug-fixed in 12-06 commit 361817a; null-fallback to error toast |
| protect_last_owner trigger | organization_memberships | BEFORE UPDATE OR DELETE FOR EACH ROW | WIRED | migration 12-01 lines 120-123; tested via integration test |
| RLS jobs_org_members | jobs.organization_id | is_member_of(organization_id) SECURITY DEFINER plpgsql | WIRED | migration 12-01 lines 255-258 |
| signup -> personal org bootstrap | organizations + memberships | INSERT in single transaction | WIRED | auth/router.py:149-178 |
| transfer_ownership endpoint | promote-then-demote atomic | service.transfer_ownership single tx | WIRED | router.py:324-344 delegates; service.py contains logic |
| drop-column migration | runbook step 9 gate | 4-condition gating comment in migration header + runbook callout | WIRED | migration header documents all 4 prerequisites; runbook step 9 enforces; rollback table covers premature application |

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|---------------------|--------|
| OrganizationSwitcher.tsx | orgs[] | useOrgContext -> fetchMyOrgs -> GET /organizations/mine -> DB JOIN on memberships+orgs | YES (router.py:59 actual SELECT JOIN) | FLOWING |
| JobHistoryPage Launched-by | row.created_by_email | backend jobs/router.py:420 actual SELECT incl. joined users.email | YES (jobs/router.py:413 SELECT joins users on j.created_by_user_id = u.id) | FLOWING |
| Billing portal CTA (owner) | hasPaymentMethod | _resolve_stripe_customer -> check_payment_method against actual Stripe API | YES | FLOWING |
| MembersTab members table | members[] | fetchMembers -> GET /organizations/{id}/members -> DB SELECT JOIN | YES (router.py:215-247) | FLOWING |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend phase-12 modules parse | `python -c "ast.parse for organizations/*, auth/org_dependencies.py, scripts/*"` | All 7 files parse | PASS |
| Phase-12 test files parse | `python -c "ast.parse for 15 test files"` | All 15 parse | PASS |
| Pytest collection (organizations + integration) | `pytest tests/organizations/ tests/integration/test_org_migration.py test_last_owner_trigger.py test_rls_jobs_org.py --collect-only -q` | 71 tests collected in 0.25s | PASS |
| Migration grep enforcement (no LANGUAGE sql) | grep on 12-01 migration | zero matches | PASS |
| LANGUAGE plpgsql occurrences in 12-01 | grep | 4 occurrences (protect_last_owner, is_member_of, has_role_in, create_organization) | PASS |
| Playwright test count | grep `^\s*test\(` on organizations.spec.ts | 12 tests | PASS |
| Integration test execution (full pytest run) | Requires SUPABASE_INTEGRATION_DB_URL + local Supabase; tests skip gracefully when env unset | SKIP — env-gated; cannot run without local Supabase | SKIP (needs human / local stack) |
| Playwright E2E execution | Requires running backend (8000) + frontend (5173) + Supabase + seed accounts | SKIP — env-gated; cannot run without full stack + seeded accounts | SKIP (needs human / staging) |

## Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|---------------|-------------|--------|----------|
| ORG-01 | 12-01, 12-02, 12-05, 12-06 | User can create an organization and invite team members by email | SATISFIED | POST /organizations + POST /organizations/{id}/invitations + Resend send_invitation_email + frontend invite form + E2E test 2 |
| ORG-02 | 12-01, 12-02, 12-03, 12-06 | Roles: owner / scientist / viewer with documented permission matrix | SATISFIED | ENUM with 3 values (migration 12-01:18); require_role enforcement (org_dependencies.py); E2E test 7+8 owner/non-owner billing gate |
| ORG-03 | 12-01, 12-03, 12-05, 12-06 | All jobs within an organization are visible to all org members | SATISFIED | RLS policy jobs_org_members (12-01:255) via is_member_of; jobs router scopes by organization_id (jobs/router.py:420); Launched-by column (12-05); E2E test 6 |
| ORG-04 | 12-01, 12-03, 12-04, 12-06 | Organization-level billing (one Stripe customer per org) | SATISFIED (with deployment gap) | Column moved (12-01); stripe_client reads org (12-03); metadata stamp (12-04); drop migration ready but BLOCKED by Gap 1 (deletion_cron + admin still read users.stripe_customer_id) |
| ORG-05 | 12-01, 12-02, 12-05, 12-06 | Owner can remove members and transfer ownership | SATISFIED | protect_last_owner trigger (12-01:120); DELETE /members + POST /members/transfer (router.py:283-344); MembersTab UI (12-05); E2E test 10 |
| ORG-06 | 12-02, 12-05, 12-06 | User can belong to multiple orgs and switch between them | SATISFIED | GET /organizations/mine + X-Org-Id resolver; localStorage[kendrew.activeOrgId]; OrganizationSwitcher; E2E test 1+1b switcher round-trip |
| ORG-07 | 12-01, 12-04, 12-06 | Existing single-tenant users migrated without data loss | SATISFIED (with deployment gap) | Personal-org backfill (12-01:216-243); Stripe metadata stamp (12-04); drop-migration gating (12-06); same Gap 1 blocks the final cleanup |
| ORG-08 | 12-01, 12-02, 12-06 | Last owner cannot leave an organization | SATISFIED | DB trigger BEFORE UPDATE OR DELETE (12-01:120-123); 400 translation in router.py:272-277; E2E test 9 |

REQUIREMENTS.md (lines 50-59 + 139-146) correctly lists all 8 ORG requirements as marked `[x]` with per-plan traceability appended. No orphaned requirements detected.

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| backend/worker/deletion_cron.py | 42-58 | Reads `SELECT id, email, stripe_customer_id FROM public.users` — column dropped by 12-06 migration | BLOCKER (post-drop) | Hard-delete cron will raise asyncpg.UndefinedColumnError on next run after drop |
| backend/admin/router.py | 108, 117, 129, 137, 151 | References `u.stripe_customer_id` in user-list SQL + GROUP BY + payment_status derivation | BLOCKER (post-drop) | `/admin/users` returns 500 after drop |
| backend/main.py | 146-175 | /health endpoint does not expose organizations_enabled despite runbook verify command | WARNING | Runbook step 1+5 verify commands silently always return null; operator may misread status |
| .planning/phases/12-teams-and-organizations/deferred-items.md | full file | Documents 2 items deferred to 12-06 that 12-06 did NOT close; file is now stale | WARNING | Future readers may assume the deferred items were closed because the phase is marked complete |
| backend/organizations/router.py | 174-207 | DELETE org refuses if `stripe_customer_id IS NOT NULL`; comment says v1 — Plan 12-03 will add the real "subscription active" check; not addressed in 12-03 | INFO | Owner with a stamped (but inactive) Stripe customer cannot delete org; manual NULL-out workaround |

## Regression Check

- `pytest tests/organizations/ tests/integration/test_org_migration.py test_last_owner_trigger.py test_rls_jobs_org.py --collect-only -q` → **71 tests collected in 0.25s, zero collection errors**.
- Running the full test suite requires SUPABASE_INTEGRATION_DB_URL (skipped gracefully when unset) and a real Supabase local instance. Did not attempt because local Supabase not provisioned in this verification environment.
- One Pydantic v1-style deprecation warning in `config.py:7` (class-based `Config` → ConfigDict). Pre-existing, not introduced by Phase 12.
- No Phase 11 / Phase 10 / Phase 9 regressions detected at file/AST level.

## Deferred-Items Audit

`.planning/phases/12-teams-and-organizations/deferred-items.md` lists two items deferred from Plan 12-03 to be closed by Plan 12-06:

1. `backend/worker/deletion_cron.py:42-58` — still reads `users.stripe_customer_id`. **NOT closed by 12-06.** Codebase confirmed lines 42-58 still contain the SELECT.
2. `backend/admin/router.py:108-151` — still JOINs `u.stripe_customer_id` + derives payment_status. **NOT closed by 12-06.** Codebase confirmed.

Plan 12-06 SUMMARY makes no mention of either file. Plan 12-06 PLAN frontmatter does not list either file in `files_modified`. The drop-column migration **will** succeed on a fresh DB, but the application code in `deletion_cron` and `admin/router` will then fail at runtime.

**Net:** Two real gaps. Both flagged as failed (gaps.0 in frontmatter).

## Runbook Review

`docs/runbook-phase-12-rollout.md` (247 lines) is operator-actionable:

- Pre-flight checklist (8 items) covers env, monitoring, backups.
- 9 ordered steps mirror RESEARCH §12.1 with concrete bash commands per step.
- Step 4 (verify Stripe metadata) explicitly gates Step 5.
- Step 8 (24-hour watch) explicitly gates Step 9 (drop column).
- Rollback table covers 7 failure modes with specific procedures.
- Decisive gate callout at the bottom emphasises the irreversibility of Step 9.

**One operational concern:**
- Step 1 verify `curl /health | jq '.organizations_enabled'` references a field that `backend/main.py:146-175` does not return. The probe will always produce `null`, so the gate cannot fail the way the runbook expects. **Operator should be redirected to a probe that exists** (e.g. `curl -o /dev/null -w "%{http_code}\n" -H "Cookie: ..." https://app.bindwave.com/organizations/mine` — 404 = flag off, 200/401 = flag on).

Step 5's verify command has the same problem (also reads `.organizations_enabled` from /health).

## Human Verification Required

These items cannot be verified programmatically without running services that are not provisioned in this verification environment. They map to Phase 12 deployment readiness and should be exercised before the runbook is followed in production:

### 1. Integration test suite green against local Supabase

**Test:** Provision local Supabase, apply migrations, set `SUPABASE_INTEGRATION_DB_URL`, then run:
```
cd backend && pytest tests/integration/test_org_migration.py tests/integration/test_last_owner_trigger.py tests/integration/test_rls_jobs_org.py -x -v
```
**Expected:** 8 tests pass (3 migration + 3 last-owner + 2 RLS).
**Why human:** Requires local Supabase instance and DB URL; not available in headless verification.

### 2. Playwright E2E spec against running stack

**Test:** Start backend (`organizations_enabled=true`), frontend, Supabase locally. Seed `usera-e2e@example.com` + `userb-e2e@example.com` accounts. Run:
```
cd frontend && npx playwright test e2e/organizations.spec.ts
```
**Expected:** 12/12 tests pass (or step 1 detects flag-off / missing seeds and gracefully skips downstream tests).
**Why human:** Requires full local stack + seeded accounts; specifically tests interactive UI behaviour (org switcher, transfer dialog) that is not statically verifiable.

### 3. Stripe stamp script against test mode

**Test:** Set `STRIPE_TEST_SECRET_KEY`, then run:
```
cd backend && python scripts/stamp_stripe_org_metadata.py --test-mode --dry-run
python scripts/stamp_stripe_org_metadata.py --test-mode
python scripts/verify_stripe_org_metadata.py --test-mode
```
**Expected:** All three exit 0; verify reports `mismatch_count == 0`.
**Why human:** Requires Stripe API key and test-mode customers seeded; not a code-only verification.

### 4. Production rollout dry-run

**Test:** Walk the runbook end-to-end against the staging Railway+Vercel deployment (separate from prod).
**Expected:** All 9 steps complete; rollback paths tested at step 5 + step 8.
**Why human:** Touches real cloud infra; cannot be safely automated from a verification agent.

### 5. Confirm /health probe alignment (Gap 2 follow-up)

**Test:** Decide whether to add `organizations_enabled` to /health response OR rewrite runbook step 1 + step 5 to use a different probe.
**Why human:** Product decision (lightweight extra check on /health vs. operational doc edit).

## Gaps Summary

Phase 12 ships a complete, well-tested, code-side implementation of multi-tenancy. The migration foundation is sound (PL/pgSQL helpers, BEFORE-trigger semantics, RLS rewrites, backfill), the backend cutover is org-scoped end-to-end (jobs, billing, webhooks, user routes), the frontend has the right context+switcher+invitations UX, the Stripe metadata path is idempotent + verifiable, and the rollout runbook has the right structure with a decisive 24h gate.

The phase falls short on **deployment-readiness cleanup** in two ways:

1. **Two callers of the deprecated `users.stripe_customer_id` column** (`deletion_cron.py` + `admin/router.py`) were explicitly deferred from 12-03 to 12-06 (per `deferred-items.md`), but 12-06 left them untouched. The drop-column migration in `20260606000001` will succeed at the SQL level but break both callers at runtime. The runbook step-8 24h watch would surface this once the deletion cron next runs (daily) or an admin opens `/admin/users` — but the operator should not have to discover this in production.

2. **The runbook's `/health` probe** asserts a field that the endpoint does not expose. The verify command will always produce `null`, defeating both the step-1 "flag is off" check and the step-5 "flag is on" check.

Neither gap blocks the merge of Phase 12 code to master — the feature flag is off by default and the drop-column migration is not applied by any automation. But both must be resolved before the runbook is followed in production. They are real, file-locatable gaps, not opinions.

The 8 ORG-XX requirements are all SATISFIED at the code level. ORG-04 and ORG-07 depend on the drop-column step for full closure, and that step is now blocked by Gap 1.

## Status Verdict

**gaps_found**

Two failed truths, both deployment-readiness issues for a phase whose feature flag is off in production. Code-side multi-tenancy is complete and well-tested. Two specific, named files need a small follow-up before the runbook can run cleanly end-to-end, and the runbook verify commands need one adjustment.

---

*Verified: 2026-06-04T23:55:00Z*
*Verifier: Claude (gsd-verifier, Opus 4.7 1M context)*
*Branch: fix/rfantibody-altloc-handling*
*HEAD: 136ca329941926dc02104ef2fedc1d3b5bbce070*
