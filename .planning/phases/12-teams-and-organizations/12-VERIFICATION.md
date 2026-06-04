---
status: gaps_found
phase: 12-teams-and-organizations
verified: 2026-06-04T12:55:00Z
must_haves_score: 32/33
requirements: [ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, ORG-08]
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 31/33
  previous_head: 136ca329941926dc02104ef2fedc1d3b5bbce070
  gap_fix_commits:
    - sha: ee1ff775ae77c17373afd32cd299d2e3aa91a531
      title: "fix(12-gaps): convert deletion_cron + admin/router stripe_customer_id reads to org JOIN"
      closes_gap: "Gap 1 (truth 32) — deletion_cron + admin/router users.stripe_customer_id reads"
    - sha: 1b7daa0d6661624ef3e3c1efc3dfba0a6ff159f0
      title: "fix(12-gaps): expose organizations_enabled flag in /health payload"
      closes_gap: "Gap 2 (truth 33) — runbook /health probe"
  gaps_closed:
    - "Drop-column migration safe to apply without breaking call sites (deletion_cron + admin/router)"
    - "Runbook /health verify command works"
  gaps_remaining:
    - "Drop-column migration safe to apply without breaking GDPR data-export path"
  regressions: []
gaps:
  - truth: "Drop-column migration (12-06) does not break the GDPR data-export path (backend/user/export.py)"
    status: failed
    reason: "Tighter audit after the Gap 1 fix surfaced a third caller of `SELECT ... stripe_customer_id FROM public.users` that the original VERIFICATION (commit 136ca32) missed: `backend/user/export.py:107-114` reads stripe_customer_id as part of the GDPR profile dump. This path runs whenever a user POSTs /user/data-export and is dispatched via FastAPI BackgroundTask from `request_data_export` (user/router.py:389). The drop migration in `20260606000001` will succeed at the SQL level but the next data-export request will raise asyncpg.exceptions.UndefinedColumnError inside the background task — the request returns 202 before the failure surfaces, so the WR-08 sentinel-stamp branch (export.py:73-82) will mark the export as 'failed' to the user without operator-visible context."
    artifacts:
      - path: "backend/user/export.py"
        issue: "Line 111: `SELECT id, email, display_name, ... notification_preferences, stripe_customer_id FROM public.users WHERE id = $1`. Same shape as the two queries closed by `ee1ff77`, missed by the original gap audit. Wave 1 still populates the column, so the query works today; runbook step 9 (DROP COLUMN) makes it fail."
    missing:
      - "Rewrite backend/user/export.py:107-114 to JOIN through `organization_memberships` → `organizations WHERE is_personal=true` (same pattern as the deletion_cron + admin/router fix in `ee1ff77`)"
      - "Add a regression test under tests/user/ that proves the export query no longer references users.stripe_customer_id (or use an integration test against a schema where the column has been dropped)"
      - "Optional: add a CI grep guard `! rg -n 'stripe_customer_id\\s+FROM\\s+public\\.users' backend/` so a future regression cannot be introduced before the runbook step 9 lands"
deferred: []
---

# Phase 12: Teams & Organizations — Verification Report

**Phase Goal:** Multi-user accounts, team billing, shared job history, role-based access (admin/scientist/viewer). Biopharma teams can use the platform under a shared organization with centralized billing.
**Verified:** 2026-06-04 (re-verification after gap-closure commits `ee1ff77` + `1b7daa0`)
**Status:** gaps_found
**Re-verification:** Yes — second pass after Gap 1 + Gap 2 fix attempts

## Goal Achievement

The phase delivers the **architectural and code foundation** for multi-tenancy end-to-end:

- A scientist CAN belong to multiple orgs (`GET /organizations/mine` returns list, `frontend/src/lib/api.ts` + `OrganizationContext.tsx` switch via `localStorage[kendrew.activeOrgId]`).
- A scientist CAN switch between them (`OrganizationSwitcher.tsx` dropdown; reload-on-switch).
- A scientist CAN invite a teammate (`POST /organizations/{id}/invitations` + Resend email via `notifications.send_invitation_email`).
- A teammate CAN accept (`POST /invitations/accept` + `frontend/src/pages/AcceptInvitation.tsx`).
- Jobs CAN be launched scoped to the active org (`backend/jobs/router.py` line 110 `org_id: str = Depends(require_role("owner", "scientist"))`).
- Stripe billing reads from the org (`backend/billing/stripe_client.py` line 49 reads `organizations.stripe_customer_id`).
- RBAC is enforced (`backend/auth/org_dependencies.py` `require_role(*allowed)` + DB-level `protect_last_owner_trigger` and `jobs_org_members` / `jobs_write_active` / `jobs_update_active` RLS policies).

**Gap-closure pass on the prior `gaps_found` verdict (commits `ee1ff77` + `1b7daa0`):**

1. ✓ **Gap 1 closed** — `deletion_cron.py:46-54` + `admin/router.py:107-153` both now `LEFT JOIN public.organization_memberships ... LEFT JOIN public.organizations ON ... AND o.is_personal = true` and read `o.stripe_customer_id`. The asyncpg row alias preserves the top-level `stripe_customer_id` key so the dependent code in both files (and their test mocks) keeps working without change.
2. ✓ **Gap 2 closed** — `backend/main.py:178` adds `organizations_enabled: settings.organizations_enabled` to the `/health` payload. The healthy verdict at line 173 is computed from the inner `checks` dict BEFORE the flag is merged in, so the bool flag does not poison the `all(v == "ok")` check. Runbook step 1 + step 5 probes now resolve to a real boolean field.

**New finding from the re-verification audit (NOT a regression — a missed item from the original audit):**

3. ✗ **`backend/user/export.py:107-114` still reads `stripe_customer_id FROM public.users`** as part of the GDPR profile dump. Structurally identical to the two queries closed by `ee1ff77` and will break in the same way after runbook step 9 (DROP COLUMN). This path was not flagged in the original VERIFICATION.md (commit 136ca32) or in `.planning/phases/12-teams-and-organizations/deferred-items.md`. It runs via FastAPI BackgroundTask from `POST /user/data-export` (user/router.py:389), so the request returns 202 immediately and the failure surfaces only as a `last_export_expires_at` sentinel-stamp (export.py:73-82) on the user row. From the operator's perspective: a silent post-drop break of the GDPR Article 20 path.

Neither the closed nor the new gap blocks the **code-complete** judgment of Phase 12 in the repo, and none block the merge of Phase 12 code to master — the feature flag is off by default and the drop-column migration is gated by runbook step 9. But the new finding must be resolved before the runbook is followed in production.

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
| 32 | Drop-column migration safe to apply without breaking call sites (deletion_cron + admin/router) | VERIFIED (via `ee1ff77`) | deletion_cron.py:46-54 + admin/router.py:107-153 now both JOIN through organization_memberships → organizations WHERE is_personal=true; `o.stripe_customer_id` alias preserves top-level dict key; Leo-confirmed 4/4 deletion_cron + 21/21 admin/router tests pass. **Note:** the truth statement was originally scoped only to these two callers; see new gap (truth 33b below) for a third caller surfaced in this audit. |
| 33 | Runbook /health verify command works | VERIFIED (via `1b7daa0`) | `backend/main.py:178` `payload = {**checks, "organizations_enabled": settings.organizations_enabled}`; `healthy` verdict computed at line 173 BEFORE the flag is merged, so it does not poison the all(v == "ok") check; runbook step 1+5 probes now resolve to a real bool. |
| 33b | Drop-column migration does not break the GDPR data-export path (user/export.py) | FAILED | NEW gap surfaced in this re-verification audit. `backend/user/export.py:107-114` still reads `SELECT ... stripe_customer_id FROM public.users WHERE id = $1`. Same shape as the two queries closed by `ee1ff77`. Triggered by POST /user/data-export → BackgroundTask. Drop migration breaks it. See gaps[0]. |

**Score:** 32/33 truths verified (97%). The two previously-failed truths (32 + 33) are now VERIFIED via the gap-fix commits; one new truth (33b) was added and FAILED in this re-verification pass, surfacing a third caller of the deprecated column that the original audit missed.

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
| `backend/worker/deletion_cron.py` | Phase 12 cutover: read stripe_customer_id via personal-org JOIN, not users column | VERIFIED (via `ee1ff77`) | Lines 41-54 contain the cutover comment + 3-table LEFT JOIN; line 64 uses `dict(row).get("stripe_customer_id")` for NULL safety; the row key still arrives as `stripe_customer_id` due to the alias |
| `backend/admin/router.py` | Phase 12 cutover: derive payment_status via personal-org JOIN, not users column | VERIFIED (via `ee1ff77`) | Lines 100-153 cover both keyset and non-keyset SELECT paths with identical 3-table LEFT JOIN; line 162 `payment_status: "active" if r["stripe_customer_id"] else "none"` still works because the alias preserves the top-level key |
| `backend/main.py` | Phase 12: /health exposes organizations_enabled informational field | VERIFIED (via `1b7daa0`) | Lines 175-178 add the flag to the payload after the healthy verdict is computed |
| `backend/user/export.py` | Phase 12 cutover: GDPR export must read stripe_customer_id via personal-org JOIN | FAILED | Line 111 still reads stripe_customer_id directly from public.users. NOT closed by `ee1ff77` (commit scope was deletion_cron + admin/router only). See gaps[0]. |
| `frontend/src/lib/organizations.ts` | 13 typed API wrappers + types incl. token field | VERIFIED | 189 lines; InvitationRow.token: string\|null at line 45; inviteMember returns {id, token} at line 147 |
| `frontend/src/lib/api.ts` | X-Org-Id header injection with opt-out list | VERIFIED | line 16 storage key; lines 29-34 opt-out; lines 56-72 shouldSendOrgHeader; lines 176-181 header injection |
| `frontend/src/components/org/OrganizationContext.tsx` | useOrgContext provider + localStorage + reload-on-switch | VERIFIED | 205 lines |
| `frontend/src/components/org/OrganizationSwitcher.tsx` | shadcn dropdown switcher | VERIFIED | 115 lines |
| `frontend/src/components/org/MembersTab.tsx` | Members table + transfer + remove + invite UI | VERIFIED | 444 lines |
| `frontend/src/components/org/InvitationsTab.tsx` | Pending invitations table + copy-link via token | VERIFIED | 184 lines |
| `frontend/src/components/org/OrgSettingsTab.tsx` | Rename + delete (typed-confirmation) | VERIFIED | 199 lines |
| `frontend/e2e/organizations.spec.ts` | 12-test serialized Playwright spec | VERIFIED | 436 lines; test.describe.serial at line 139; 12 test() declarations |
| `docs/runbook-phase-12-rollout.md` | 9-step rollout + rollback table + decisive gate | VERIFIED | 247 lines; 9 steps + rollback table + decisive gate callout; `/health \| jq .organizations_enabled` probes at lines 34 + 116 now resolve to a real field |
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
| deletion_cron.py | organizations.stripe_customer_id (personal org) | LEFT JOIN via organization_memberships | WIRED (via `ee1ff77`) | lines 46-54 chain `users u → organization_memberships om → organizations o` filtered by `o.is_personal = true` |
| admin/router.py list_users | organizations.stripe_customer_id (personal org) | LEFT JOIN via organization_memberships | WIRED (via `ee1ff77`) | both keyset + non-keyset paths chain through the same 3-table LEFT JOIN |
| /health endpoint | settings.organizations_enabled | top-level payload field | WIRED (via `1b7daa0`) | main.py:178 `payload = {**checks, "organizations_enabled": settings.organizations_enabled}`; healthy verdict computed on inner checks dict only |
| user/export.py | organizations.stripe_customer_id (personal org) | LEFT JOIN via organization_memberships | NOT_WIRED | export.py:107-114 still reads `stripe_customer_id` directly from public.users; identical fix pattern available but not applied in `ee1ff77` |

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|---------------------|--------|
| OrganizationSwitcher.tsx | orgs[] | useOrgContext -> fetchMyOrgs -> GET /organizations/mine -> DB JOIN on memberships+orgs | YES (router.py:59 actual SELECT JOIN) | FLOWING |
| JobHistoryPage Launched-by | row.created_by_email | backend jobs/router.py:420 actual SELECT incl. joined users.email | YES (jobs/router.py:413 SELECT joins users on j.created_by_user_id = u.id) | FLOWING |
| Billing portal CTA (owner) | hasPaymentMethod | _resolve_stripe_customer -> check_payment_method against actual Stripe API | YES | FLOWING |
| MembersTab members table | members[] | fetchMembers -> GET /organizations/{id}/members -> DB SELECT JOIN | YES (router.py:215-247) | FLOWING |
| /health response | organizations_enabled | settings.organizations_enabled (config.py:149, default False) | YES (Pydantic settings read at startup) | FLOWING |
| Admin users.payment_status | rows[i].stripe_customer_id (aliased) | LEFT JOIN orgs WHERE is_personal=true | YES (post `ee1ff77`) | FLOWING |
| deletion_cron stripe cleanup | stripe_customer_id passed to execute_hard_delete | LEFT JOIN orgs WHERE is_personal=true | YES (post `ee1ff77`) | FLOWING |
| GDPR export user profile | profile.stripe_customer_id | direct SELECT FROM public.users | YES today; will be DISCONNECTED post-drop | STATIC (pre-drop) / DISCONNECTED (post-drop) |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend phase-12 modules parse | `python -c "ast.parse for organizations/*, auth/org_dependencies.py, scripts/*"` | All 7 files parse | PASS |
| Phase-12 test files parse | `python -c "ast.parse for 15 test files"` | All 15 parse | PASS |
| Pytest collection (organizations + integration) | `pytest tests/organizations/ tests/integration/test_org_migration.py test_last_owner_trigger.py test_rls_jobs_org.py --collect-only -q` | 71 tests collected in 0.25s | PASS (prior verification) |
| Migration grep enforcement (no LANGUAGE sql) | grep on 12-01 migration | zero matches | PASS |
| LANGUAGE plpgsql occurrences in 12-01 | grep | 4 occurrences | PASS |
| Playwright test count | grep `^\s*test\(` on organizations.spec.ts | 12 tests | PASS |
| Leftover `u.stripe_customer_id` / `users.stripe_customer_id` in live SQL | Grep `backend/**/*.py` excluding tests + comments + scripts/stamp_stripe_org_metadata.py docstring | `worker/deletion_cron.py` + `admin/router.py` matches are now in COMMENTS only; live SQL goes through `o.stripe_customer_id`. BUT `user/export.py:111` still selects `stripe_customer_id` directly from `public.users` — FAILED. | PARTIAL (2 callers closed, 1 missed) |
| Runbook /health probe payload | Read main.py:178 + runbook lines 34, 116 | `organizations_enabled` is a top-level bool field in /health payload; runbook probes resolve | PASS |
| deletion_cron tests | Leo-attested 4/4 pass after `ee1ff77` | 4/4 | PASS (Leo confirmed inline) |
| admin/router tests | Leo-attested 21/21 pass after `ee1ff77` | 21/21 | PASS (Leo confirmed inline) |
| Integration test execution (full pytest run) | Requires SUPABASE_INTEGRATION_DB_URL + local Supabase; tests skip gracefully when env unset | SKIP — env-gated | SKIP (needs human / local stack) |
| Playwright E2E execution | Requires running backend (8000) + frontend (5173) + Supabase + seed accounts | SKIP — env-gated | SKIP (needs human / staging) |

## Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|---------------|-------------|--------|----------|
| ORG-01 | 12-01, 12-02, 12-05, 12-06 | User can create an organization and invite team members by email | SATISFIED | POST /organizations + POST /organizations/{id}/invitations + Resend send_invitation_email + frontend invite form + E2E test 2 |
| ORG-02 | 12-01, 12-02, 12-03, 12-06 | Roles: owner / scientist / viewer with documented permission matrix | SATISFIED | ENUM with 3 values (migration 12-01:18); require_role enforcement (org_dependencies.py); E2E test 7+8 owner/non-owner billing gate |
| ORG-03 | 12-01, 12-03, 12-05, 12-06 | All jobs within an organization are visible to all org members | SATISFIED | RLS policy jobs_org_members (12-01:255) via is_member_of; jobs router scopes by organization_id (jobs/router.py:420); Launched-by column (12-05); E2E test 6 |
| ORG-04 | 12-01, 12-03, 12-04, 12-06 | Organization-level billing (one Stripe customer per org) | SATISFIED (with deployment gap) | Column moved (12-01); stripe_client reads org (12-03); metadata stamp (12-04); two of three drop-blocker callers closed by `ee1ff77`; user/export.py drop-blocker remains. ORG-04 is met at the code level; the drop migration's pre-conditions are not fully met until export.py is fixed. |
| ORG-05 | 12-01, 12-02, 12-05, 12-06 | Owner can remove members and transfer ownership | SATISFIED | protect_last_owner trigger (12-01:120); DELETE /members + POST /members/transfer (router.py:283-344); MembersTab UI (12-05); E2E test 10 |
| ORG-06 | 12-02, 12-05, 12-06 | User can belong to multiple orgs and switch between them | SATISFIED | GET /organizations/mine + X-Org-Id resolver; localStorage[kendrew.activeOrgId]; OrganizationSwitcher; E2E test 1+1b switcher round-trip |
| ORG-07 | 12-01, 12-04, 12-06 | Existing single-tenant users migrated without data loss | SATISFIED (with deployment gap) | Personal-org backfill (12-01:216-243); Stripe metadata stamp (12-04); drop-migration gating (12-06); same user/export.py blocker as ORG-04 above. |
| ORG-08 | 12-01, 12-02, 12-06 | Last owner cannot leave an organization | SATISFIED | DB trigger BEFORE UPDATE OR DELETE (12-01:120-123); 400 translation in router.py:272-277; E2E test 9 |

REQUIREMENTS.md (lines 50-59 + 139-146) correctly lists all 8 ORG requirements as marked `[x]` with per-plan traceability appended. No orphaned requirements detected. Note: ORG-04 + ORG-07 remain SATISFIED at the code level — the user/export.py gap is a deployment-step blocker for the drop migration, not a requirements blocker.

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| backend/user/export.py | 107-114 | `SELECT ... stripe_customer_id FROM public.users WHERE id = $1` — column dropped by 12-06 migration | BLOCKER (post-drop) | GDPR data-export BackgroundTask raises UndefinedColumnError on next request after drop; user sees `last_export_expires_at` sentinel-stamp (failed) but operator gets no surface signal |
| backend/worker/deletion_cron.py | 41-54 | (resolved) `SELECT u.id, u.email, o.stripe_customer_id FROM public.users u LEFT JOIN ...` | RESOLVED | Cutover comment + 3-table JOIN closed by `ee1ff77`; tests pass |
| backend/admin/router.py | 100-153 | (resolved) Same JOIN pattern on both keyset and non-keyset paths | RESOLVED | Closed by `ee1ff77`; `payment_status` derivation preserved via aliased column |
| backend/main.py | 175-178 | (resolved) /health payload now includes `organizations_enabled: settings.organizations_enabled` | RESOLVED | Closed by `1b7daa0`; runbook step 1 + step 5 probes resolve |
| .planning/phases/12-teams-and-organizations/deferred-items.md | full file | Documents 2 items as still-open that are now CLOSED by `ee1ff77`; file is now stale | WARNING | Future readers may believe the items are still open. Recommend adding a 2026-06-04 "CLOSED in commit ee1ff77" stamp + new section flagging the user/export.py blocker surfaced after the fix. |
| backend/organizations/router.py | 174-207 | DELETE org refuses if `stripe_customer_id IS NOT NULL`; comment says v1 — Plan 12-03 will add the real "subscription active" check; not addressed in 12-03 | INFO | Owner with a stamped (but inactive) Stripe customer cannot delete org; manual NULL-out workaround |

## Regression Check

- `git show ee1ff77 -- backend/` and `git show 1b7daa0 -- backend/main.py` confirm both commits are scoped to the file lines documented in their commit messages. No unrelated changes snuck in.
- `git show ee1ff77` diff stat: 2 files changed, 27 insertions(+), 8 deletions(-) — matches the JOIN-rewrite delta for the two SELECTs.
- `git show 1b7daa0` diff stat: 1 file changed, 5 insertions(+), 1 deletion(-) — matches the 4-line health-payload addition + 1-line comment.
- The dependent code in `deletion_cron.py:64` (`dict(row).get("stripe_customer_id")`) and `admin/router.py:162` (`r["stripe_customer_id"]`) is unchanged — the asyncpg row alias `o.stripe_customer_id` keeps the same top-level key in the returned dict, so no caller-side changes are needed.
- The /health `healthy = all(v == "ok" for v in checks.values())` calculation at main.py:173 still reads the inner `checks` dict only; the bool flag is merged into `payload` AFTER the verdict is set, so a `True`/`False` value of `organizations_enabled` cannot poison the 200/503 status code.
- Leo confirmed 4/4 deletion_cron + 21/21 admin/router tests pass inline in the work prompt.
- `pytest --collect-only -q` was not re-run in this verification environment (no local DB); prior collection of 71 tests still holds.
- No Phase 11 / Phase 10 / Phase 9 regressions detected at file/AST level.

## Deferred-Items Audit

`.planning/phases/12-teams-and-organizations/deferred-items.md` lists two items deferred from Plan 12-03 that this re-verification confirms are now CLOSED:

1. `backend/worker/deletion_cron.py:42-58` — **CLOSED** by `ee1ff77`. Lines 41-54 now contain the cutover comment + 3-table LEFT JOIN.
2. `backend/admin/router.py:108-151` — **CLOSED** by `ee1ff77`. Both keyset and non-keyset paths now use the JOIN.

**However, the audit also surfaced a third caller that was NOT in deferred-items.md:**

3. `backend/user/export.py:107-114` — **NEW gap.** Same `SELECT ... stripe_customer_id FROM public.users` shape; same post-drop break. The original Plan 12-03 audit missed this caller; `ee1ff77` did not close it; deferred-items.md was never updated to include it.

**File status:** deferred-items.md is now factually stale on item 1 + item 2 (they are closed) and silent on item 3 (the new gap). It needs a 2026-06-04 update or replacement.

## Runbook Review

`docs/runbook-phase-12-rollout.md` (247 lines) is operator-actionable:

- Pre-flight checklist (8 items) covers env, monitoring, backups.
- 9 ordered steps mirror RESEARCH §12.1 with concrete bash commands per step.
- Step 4 (verify Stripe metadata) explicitly gates Step 5.
- Step 8 (24-hour watch) explicitly gates Step 9 (drop column).
- Rollback table covers 7 failure modes with specific procedures.
- Decisive gate callout at the bottom emphasises the irreversibility of Step 9.

**Status:**
- ✓ Step 1 verify (`curl /health | jq '.organizations_enabled'`) — now resolves to a real bool field via `1b7daa0`.
- ✓ Step 5 verify — same probe, same fix.
- ✗ Step 9 (DROP COLUMN) — still blocked by `user/export.py:111`; an operator running step 9 would discover this on the next GDPR data-export request (typically rare, so the failure surfaces hours-to-days after the drop). The runbook's 4-condition gate comment in the drop migration header should be expanded to require a grep-clean check across the backend, or the export.py rewrite must precede the next runbook execution.

## Human Verification Required

These items cannot be verified programmatically without running services that are not provisioned in this verification environment:

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

### 5. Confirm /health probe payload in staging

**Test:** Curl `https://staging.bindwave.com/health | jq '.organizations_enabled'` against a deployment with `organizations_enabled=false` and again with `=true`.
**Expected:** Returns `false` then `true`. Never `null` (would mean the fix did not deploy).
**Why human:** Requires a staging deployment; the unit-level fix is verified but the field's reachability over HTTPS is not.

## Gap-Closure Summary (2026-06-04 follow-up)

This re-verification was triggered by two gap-fix commits on branch `fix/rfantibody-altloc-handling`:

### Gap 1 — leftover `users.stripe_customer_id` reads

**Original finding** (commit 136ca32 VERIFICATION):
- `backend/worker/deletion_cron.py:42-58` reads the deprecated column directly.
- `backend/admin/router.py:108-151` references `u.stripe_customer_id` in user-list SQL + GROUP BY + payment_status derivation.

**Fix commit:** `ee1ff77 fix(12-gaps): convert deletion_cron + admin/router stripe_customer_id reads to org JOIN`

**Confirmation:**
- `worker/deletion_cron.py:46-54` chains `users u → organization_memberships om → organizations o WHERE o.is_personal = true` and selects `o.stripe_customer_id`. The asyncpg row dict still contains a top-level `stripe_customer_id` key (alias rule), so `dict(row).get("stripe_customer_id")` at line 64 works unchanged.
- `admin/router.py:100-153` applies the same 3-table LEFT JOIN to both the keyset (`before_dt is not None`) and non-keyset paths. GROUP BY at lines 125 + 148 includes `o.stripe_customer_id`. Line 162 `payment_status: "active" if r["stripe_customer_id"] else "none"` is preserved.
- Leo-confirmed inline: 4/4 deletion_cron tests + 21/21 admin/router tests pass.
- Grep audit for `u\.stripe_customer_id|users\.stripe_customer_id` in `backend/` shows zero matches in live SQL — only comments (stripe_client.py:10,37; deletion_cron.py:41-44 cutover comment), test mocks, and the stamp_stripe_org_metadata.py module docstring remain.

**Status:** VERIFIED — Gap 1 closed.

### Gap 2 — `/health` missing `organizations_enabled`

**Original finding** (commit 136ca32 VERIFICATION):
- `backend/main.py:146-175` `/health` returned only `{api, db, redis}` status keys, but runbook steps 1 + 5 probe `curl /health | jq .organizations_enabled` which would always return `null`.

**Fix commit:** `1b7daa0 fix(12-gaps): expose organizations_enabled flag in /health payload`

**Confirmation:**
- `main.py:175-178` constructs `payload = {**checks, "organizations_enabled": settings.organizations_enabled}` and returns `JSONResponse(content=payload, status_code=status_code)`.
- `settings.organizations_enabled` is sourced from `config.py:149` (Pydantic settings, default `False`).
- The `healthy = all(v == "ok" for v in checks.values())` calculation at line 173 reads the inner `checks` dict BEFORE the bool is merged into `payload`, so a `True`/`False` flag value cannot poison the 200/503 status decision.
- Runbook probes at `docs/runbook-phase-12-rollout.md:34` + `:116` now resolve to a real boolean.

**Status:** VERIFIED — Gap 2 closed.

### Net delta from prior verification

| Metric | Prior (136ca32) | Now (post `1b7daa0`) |
|--------|-----------------|----------------------|
| Status | gaps_found | gaps_found |
| Score | 31/33 | 32/33 |
| Failed truths | 2 (deletion_cron+admin, health) | 1 (user/export.py) |
| Closed gaps in this pass | — | 2 |
| New gaps surfaced in this pass | — | 1 |

### New gap surfaced — `backend/user/export.py:107-114`

**This is NOT a regression introduced by `ee1ff77` or `1b7daa0`.** It is a third caller of the deprecated column that the original audit missed and that neither gap-fix commit was scoped to address.

**Code path:** `POST /user/data-export` (user/router.py:362-411) → `BackgroundTask(build_and_deliver_export)` → `_build_and_deliver_export_inner` → SELECT at export.py:107-114.

**Why it slipped:** The original gap-audit grep (per the deferred-items.md trail) found the deletion_cron + admin/router callers but not export.py because export.py reads stripe_customer_id without an `u.` alias — the pattern `u\.stripe_customer_id` does not catch it, only a broader `stripe_customer_id\s+FROM\s+public\.users` multiline grep does.

**Fix required:** Same pattern as `ee1ff77` — replace the bare `SELECT ... stripe_customer_id` with a `LEFT JOIN public.organization_memberships ... LEFT JOIN public.organizations ... WHERE is_personal=true` and read `o.stripe_customer_id`. Add a regression test under `tests/user/` that the query no longer references the deprecated column.

**Recommended CI guard:**
```
! rg -nU 'stripe_customer_id[\s\S]*?FROM public\.users' backend/
```
This would catch any future regression before runbook step 9 lands.

## Status Verdict

**gaps_found**

The two originally-flagged gaps (truths 32 + 33) are now VERIFIED via commits `ee1ff77` and `1b7daa0`. The fixes are minimal, scoped, and Leo-attested.

However, the re-verification audit surfaced a third caller of the deprecated `users.stripe_customer_id` column (`backend/user/export.py:107-114`) that the original verification missed. It is structurally identical to the original Gap 1 finding and will break the GDPR data-export BackgroundTask after runbook step 9 (DROP COLUMN) runs.

The score moves from 31/33 to 32/33. Neither the closed nor the new gap blocks the merge of Phase 12 to master — the feature flag is off by default and the drop migration is gated by runbook step 9. But the new gap must be resolved before the runbook is followed in production, and `deferred-items.md` should be refreshed.

---

*Re-verified: 2026-06-04T12:55:00Z*
*Verifier: Claude (gsd-verifier, Opus 4.7 1M context)*
*Branch: fix/rfantibody-altloc-handling*
*HEAD: 1b7daa0d6661624ef3e3c1efc3dfba0a6ff159f0*
*Previous HEAD (verified 2026-06-04T23:55:00Z): 136ca329941926dc02104ef2fedc1d3b5bbce070*
