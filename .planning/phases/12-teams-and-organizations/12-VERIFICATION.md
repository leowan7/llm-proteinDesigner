---
status: passed
phase: 12-teams-and-organizations
verified: 2026-06-04T14:30:00Z
must_haves_score: 33/33
requirements: [ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, ORG-08]
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 32/33
  previous_head: 299c49a
  gap_fix_commits:
    - sha: ee1ff775ae77c17373afd32cd299d2e3aa91a531
      title: "fix(12-gaps): convert deletion_cron + admin/router stripe_customer_id reads to org JOIN"
      closes_gap: "Gap 1 (truth 32) — deletion_cron + admin/router users.stripe_customer_id reads"
    - sha: 1b7daa0d6661624ef3e3c1efc3dfba0a6ff159f0
      title: "fix(12-gaps): expose organizations_enabled flag in /health payload"
      closes_gap: "Gap 2 (truth 33) — runbook /health probe"
    - sha: cf082e75a77fc81ee663265a9594c0da9c43d69d
      title: "fix(12-gaps): convert user/export GDPR profile dump to org JOIN"
      closes_gap: "Gap 3 (truth 33b) — backend/user/export.py GDPR data-export path"
  gaps_closed:
    - "Drop-column migration safe to apply without breaking call sites (deletion_cron + admin/router)"
    - "Runbook /health verify command works"
    - "Drop-column migration safe to apply without breaking GDPR data-export path (user/export.py)"
  gaps_remaining: []
  regressions: []
gaps: []
deferred: []
---

# Phase 12: Teams & Organizations — Verification Report

**Phase Goal:** Multi-user accounts, team billing, shared job history, role-based access (admin/scientist/viewer). Biopharma teams can use the platform under a shared organization with centralized billing.
**Verified:** 2026-06-04 (final pass after all three gap-closure commits `ee1ff77` + `1b7daa0` + `cf082e7`)
**Status:** passed
**Re-verification:** Yes — third pass; all audited gaps resolved

## Goal Achievement

The phase delivers the **architectural and code foundation** for multi-tenancy end-to-end:

- A scientist CAN belong to multiple orgs (`GET /organizations/mine` returns list, `frontend/src/lib/api.ts` + `OrganizationContext.tsx` switch via `localStorage[kendrew.activeOrgId]`).
- A scientist CAN switch between them (`OrganizationSwitcher.tsx` dropdown; reload-on-switch).
- A scientist CAN invite a teammate (`POST /organizations/{id}/invitations` + Resend email via `notifications.send_invitation_email`).
- A teammate CAN accept (`POST /invitations/accept` + `frontend/src/pages/AcceptInvitation.tsx`).
- Jobs CAN be launched scoped to the active org (`backend/jobs/router.py` line 110 `org_id: str = Depends(require_role("owner", "scientist"))`).
- Stripe billing reads from the org (`backend/billing/stripe_client.py` line 49 reads `organizations.stripe_customer_id`).
- RBAC is enforced (`backend/auth/org_dependencies.py` `require_role(*allowed)` + DB-level `protect_last_owner_trigger` and `jobs_org_members` / `jobs_write_active` / `jobs_update_active` RLS policies).

**Gap-closure pass on the prior `gaps_found` verdicts (commits `ee1ff77` + `1b7daa0` + `cf082e7`):**

1. ✓ **Gap 1 closed** (`ee1ff77`) — `deletion_cron.py:46-54` + `admin/router.py:107-153` both now `LEFT JOIN public.organization_memberships ... LEFT JOIN public.organizations ON ... AND o.is_personal = true` and read `o.stripe_customer_id`. The asyncpg row alias preserves the top-level `stripe_customer_id` key so the dependent code in both files (and their test mocks) keeps working without change.
2. ✓ **Gap 2 closed** (`1b7daa0`) — `backend/main.py:178` adds `organizations_enabled: settings.organizations_enabled` to the `/health` payload. The healthy verdict at line 173 is computed from the inner `checks` dict BEFORE the flag is merged in, so the bool flag does not poison the `all(v == "ok")` check. Runbook step 1 + step 5 probes now resolve to a real boolean field.
3. ✓ **Gap 3 closed** (`cf082e7`) — `backend/user/export.py:_build_and_deliver_export_inner` SELECT at lines 111-122 now `LEFT JOIN public.organization_memberships om ON om.user_id = u.id LEFT JOIN public.organizations o ON o.id = om.organization_id AND o.is_personal = true` and selects `o.stripe_customer_id`. Same alias pattern as Gap 1 — exported `profile.json` keeps the same `stripe_customer_id` key, GDPR Article 20 shape preserved. 9/9 `tests/user/test_export.py` pass (Leo-confirmed inline in commit message).

Independent grep audit against `backend/**/*.py` for `u\.stripe_customer_id|users\.stripe_customer_id|users SET stripe_customer_id|users\(.*stripe_customer_id|FROM public\.users.*stripe_customer_id` returns only:
- `billing/stripe_client.py:10,37` — explanatory comments
- `scripts/stamp_stripe_org_metadata.py:5` — comment referencing the soon-to-drop column
- `tests/integration/test_org_migration.py:68,75,80` — intentional pre-migration invariant test (proves backfill correctness; obsoletes itself when the drop migration runs)
- `tests/webhooks/test_runpod_org_billing.py:6` — comment contrasting old vs new path

**Zero remaining live-code reads of `public.users.stripe_customer_id`.** The runbook step-9 drop migration is now safe from a code-path perspective; the column drop will not break any production read path.

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
| 32 | Drop-column migration safe to apply without breaking call sites (deletion_cron + admin/router) | VERIFIED (via `ee1ff77`) | deletion_cron.py:46-54 + admin/router.py:107-153 now both JOIN through organization_memberships → organizations WHERE is_personal=true; `o.stripe_customer_id` alias preserves top-level dict key; Leo-confirmed 4/4 deletion_cron + 21/21 admin/router tests pass |
| 33 | Runbook /health verify command works | VERIFIED (via `1b7daa0`) | `backend/main.py:178` `payload = {**checks, "organizations_enabled": settings.organizations_enabled}`; healthy verdict computed at line 173 BEFORE the flag is merged, so it does not poison the all(v == "ok") check; runbook step 1+5 probes now resolve to a real bool |
| 33b | Drop-column migration does not break the GDPR data-export path (user/export.py) | VERIFIED (via `cf082e7`) | `backend/user/export.py:111-122` now LEFT JOINs through `organization_memberships` → `organizations WHERE is_personal=true` and selects `o.stripe_customer_id`. Alias preserves the exported `profile.json` key (GDPR shape unchanged). 9/9 `tests/user/test_export.py` pass (Leo-confirmed inline in commit cf082e7 body). |

**Score:** 33/33 truths verified (100%). All three originally-flagged truths (32, 33, and the audit-surfaced 33b) are now VERIFIED via the gap-fix commits.

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
| `backend/user/export.py` | Phase 12 cutover: GDPR export must read stripe_customer_id via personal-org JOIN | VERIFIED (via `cf082e7`) | Lines 111-122 contain the cutover comment + 3-table LEFT JOIN through `organization_memberships` → `organizations WHERE is_personal=true`; selects `o.stripe_customer_id`; exported `profile.json` field name preserved; 9/9 user/test_export.py pass |
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
| user/export.py | organizations.stripe_customer_id (personal org) | LEFT JOIN via organization_memberships | WIRED (via `cf082e7`) | export.py:111-122 chains `users u → organization_memberships om → organizations o WHERE o.is_personal = true`; alias preserves `profile.stripe_customer_id` key in exported JSON |

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
| GDPR export user profile | profile.stripe_customer_id | LEFT JOIN orgs WHERE is_personal=true (alias preserves key name) | YES (post `cf082e7`) | FLOWING |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend phase-12 modules parse | `python -c "ast.parse for organizations/*, auth/org_dependencies.py, scripts/*"` | All 7 files parse | PASS |
| Phase-12 test files parse | `python -c "ast.parse for 15 test files"` | All 15 parse | PASS |
| Pytest collection (organizations + integration) | `pytest tests/organizations/ tests/integration/test_org_migration.py test_last_owner_trigger.py test_rls_jobs_org.py --collect-only -q` | 71 tests collected in 0.25s | PASS (prior verification) |
| Migration grep enforcement (no LANGUAGE sql) | grep on 12-01 migration | zero matches | PASS |
| LANGUAGE plpgsql occurrences in 12-01 | grep | 4 occurrences | PASS |
| Playwright test count | grep `^\s*test\(` on organizations.spec.ts | 12 tests | PASS |
| Leftover live-code `users.stripe_customer_id` reads | Grep `backend/**/*.py` (5-alternation pattern) for `u\|users\.stripe_customer_id` / `users SET stripe_customer_id` / `users\(.*stripe_customer_id` / `FROM public\.users.*stripe_customer_id` | Only comments + intentional pre-migration invariant test remain (see "Goal Achievement" for full breakdown). Zero live-code reads. | PASS |
| Runbook /health probe payload | Read main.py:178 + runbook lines 34, 116 | `organizations_enabled` is a top-level bool field in /health payload; runbook probes resolve | PASS |
| deletion_cron tests | Leo-attested 4/4 pass after `ee1ff77` | 4/4 | PASS (Leo confirmed inline) |
| admin/router tests | Leo-attested 21/21 pass after `ee1ff77` | 21/21 | PASS (Leo confirmed inline) |
| user/export tests | Leo-attested 9/9 pass after `cf082e7` | 9/9 | PASS (Leo confirmed inline in commit body) |
| Integration test execution (full pytest run) | Requires SUPABASE_INTEGRATION_DB_URL + local Supabase; tests skip gracefully when env unset | SKIP — env-gated | SKIP (needs human / local stack) |
| Playwright E2E execution | Requires running backend (8000) + frontend (5173) + Supabase + seed accounts | SKIP — env-gated | SKIP (needs human / staging) |

## Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|---------------|-------------|--------|----------|
| ORG-01 | 12-01, 12-02, 12-05, 12-06 | User can create an organization and invite team members by email | SATISFIED | POST /organizations + POST /organizations/{id}/invitations + Resend send_invitation_email + frontend invite form + E2E test 2 |
| ORG-02 | 12-01, 12-02, 12-03, 12-06 | Roles: owner / scientist / viewer with documented permission matrix | SATISFIED | ENUM with 3 values (migration 12-01:18); require_role enforcement (org_dependencies.py); E2E test 7+8 owner/non-owner billing gate |
| ORG-03 | 12-01, 12-03, 12-05, 12-06 | All jobs within an organization are visible to all org members | SATISFIED | RLS policy jobs_org_members (12-01:255) via is_member_of; jobs router scopes by organization_id (jobs/router.py:420); Launched-by column (12-05); E2E test 6 |
| ORG-04 | 12-01, 12-03, 12-04, 12-06 | Organization-level billing (one Stripe customer per org) | SATISFIED | Column moved (12-01); stripe_client reads org (12-03); metadata stamp (12-04); all three drop-blocker callers (deletion_cron, admin/router, user/export) closed via `ee1ff77` + `cf082e7`. Drop migration's pre-conditions fully met at the code level. |
| ORG-05 | 12-01, 12-02, 12-05, 12-06 | Owner can remove members and transfer ownership | SATISFIED | protect_last_owner trigger (12-01:120); DELETE /members + POST /members/transfer (router.py:283-344); MembersTab UI (12-05); E2E test 10 |
| ORG-06 | 12-02, 12-05, 12-06 | User can belong to multiple orgs and switch between them | SATISFIED | GET /organizations/mine + X-Org-Id resolver; localStorage[kendrew.activeOrgId]; OrganizationSwitcher; E2E test 1+1b switcher round-trip |
| ORG-07 | 12-01, 12-04, 12-06 | Existing single-tenant users migrated without data loss | SATISFIED | Personal-org backfill (12-01:216-243); Stripe metadata stamp (12-04); drop-migration gating (12-06); all drop-blocker call sites cutover (incl. user/export via `cf082e7`). |
| ORG-08 | 12-01, 12-02, 12-06 | Last owner cannot leave an organization | SATISFIED | DB trigger BEFORE UPDATE OR DELETE (12-01:120-123); 400 translation in router.py:272-277; E2E test 9 |

REQUIREMENTS.md (lines 50-59 + 139-146) correctly lists all 8 ORG requirements as marked `[x]` with per-plan traceability appended. No orphaned requirements detected. ORG-04 + ORG-07 are now fully SATISFIED at the code level — all three drop-blocker call sites are cutover.

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| backend/worker/deletion_cron.py | 41-54 | (resolved) `SELECT u.id, u.email, o.stripe_customer_id FROM public.users u LEFT JOIN ...` | RESOLVED | Cutover comment + 3-table JOIN closed by `ee1ff77`; tests pass |
| backend/admin/router.py | 100-153 | (resolved) Same JOIN pattern on both keyset and non-keyset paths | RESOLVED | Closed by `ee1ff77`; `payment_status` derivation preserved via aliased column |
| backend/main.py | 175-178 | (resolved) /health payload now includes `organizations_enabled: settings.organizations_enabled` | RESOLVED | Closed by `1b7daa0`; runbook step 1 + step 5 probes resolve |
| backend/user/export.py | 107-122 | (resolved) `SELECT ... o.stripe_customer_id FROM public.users u LEFT JOIN public.organization_memberships om ... LEFT JOIN public.organizations o ... AND o.is_personal = true WHERE u.id = $1` | RESOLVED | Closed by `cf082e7`; exported `profile.json` key name preserved via alias; 9/9 tests pass |
| .planning/phases/12-teams-and-organizations/deferred-items.md | full file | Documents items as still-open that are now CLOSED across `ee1ff77` + `cf082e7`; file is stale | WARNING | Future readers may believe items are still open. Recommend adding a 2026-06-04 "CLOSED in commits ee1ff77 + cf082e7" stamp. |
| backend/organizations/router.py | 174-207 | DELETE org refuses if `stripe_customer_id IS NOT NULL`; comment says v1 — Plan 12-03 will add the real "subscription active" check; not addressed in 12-03 | INFO | Owner with a stamped (but inactive) Stripe customer cannot delete org; manual NULL-out workaround |

## Regression Check

- `git show ee1ff77 -- backend/`, `git show 1b7daa0 -- backend/main.py`, and `git show cf082e7 -- backend/user/export.py` confirm all three commits are scoped to the file lines documented in their commit messages. No unrelated changes snuck in.
- `git show cf082e7 --stat` shows the commit is scoped to backend/user/export.py only; no collateral changes.
- The dependent code in `deletion_cron.py:64` (`dict(row).get("stripe_customer_id")`), `admin/router.py:162` (`r["stripe_customer_id"]`), and the export.py downstream `profile.json` dump are unchanged — the asyncpg row alias `o.stripe_customer_id` keeps the same top-level key in every dict the callers see, so no caller-side changes are needed.
- The /health `healthy = all(v == "ok" for v in checks.values())` calculation at main.py:173 still reads the inner `checks` dict only; the bool flag is merged into `payload` AFTER the verdict is set, so a `True`/`False` value of `organizations_enabled` cannot poison the 200/503 status code.
- Leo confirmed test passes inline for all three commits: 4/4 deletion_cron + 21/21 admin/router + 9/9 user/test_export.
- `pytest --collect-only -q` was not re-run in this verification environment (no local DB); prior collection of 71 tests still holds and the export.py change does not alter test collection.
- No Phase 11 / Phase 10 / Phase 9 regressions detected at file/AST level.

## Final Grep Audit (independent confirmation)

Pattern: `u\.stripe_customer_id|users\.stripe_customer_id|users SET stripe_customer_id|users\(.*stripe_customer_id|FROM public\.users.*stripe_customer_id`
Scope: `backend/**/*.py`
Live-code matches: **0**
Total matches: 7, broken down as:

| File:Line | Category | Why not actionable |
|-----------|----------|--------------------|
| `billing/stripe_client.py:10,37` | Comment | Doc only |
| `scripts/stamp_stripe_org_metadata.py:5` | Comment | Module docstring referencing the deprecated column for context |
| `tests/integration/test_org_migration.py:68,75,80` | Intentional pre-migration test | Proves backfill correctness; obsoletes itself when the drop migration ships |
| `tests/webhooks/test_runpod_org_billing.py:6` | Comment | Contrasts old vs new path |

No remaining live-code reads of `public.users.stripe_customer_id` in the backend. The runbook step-9 drop migration is now safe.

## Runbook Review

`docs/runbook-phase-12-rollout.md` (247 lines) is operator-actionable:

- Pre-flight checklist (8 items) covers env, monitoring, backups.
- 9 ordered steps mirror RESEARCH §12.1 with concrete bash commands per step.
- Step 4 (verify Stripe metadata) explicitly gates Step 5.
- Step 8 (24-hour watch) explicitly gates Step 9 (drop column).
- Rollback table covers 7 failure modes with specific procedures.
- Decisive gate callout at the bottom emphasises the irreversibility of Step 9.

**Status:**
- ✓ Step 1 verify (`curl /health | jq '.organizations_enabled'`) — resolves to a real bool field via `1b7daa0`.
- ✓ Step 5 verify — same probe, same fix.
- ✓ Step 9 (DROP COLUMN) — all three live-code blockers (deletion_cron, admin/router, user/export) cutover via `ee1ff77` + `cf082e7`. Runbook execution is now safe from a code-path perspective. Recommendation: add a grep-clean CI guard (`! rg -nU 'stripe_customer_id[\s\S]*?FROM public\.users' backend/`) before the runbook is followed in production, to catch any future regression.

## Human Verification Required

These items cannot be verified programmatically without running services that are not provisioned in this verification environment. They are NOT blockers for the `passed` verdict — they are operator-side validations that must happen before the runbook execution against production.

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

### 6. GDPR data-export round-trip after schema cutover

**Test:** Against a database where the drop-column migration has been applied, POST `/user/data-export` for a user with a stamped Stripe customer id on their personal org. Wait for the email, download the export zip, open `profile.json`.
**Expected:** `profile.json` contains a `stripe_customer_id` field with the value from `organizations.stripe_customer_id` (their personal org). No 500s, no `last_export_expires_at` sentinel-stamp.
**Why human:** Requires applied drop migration + Stripe customer fixture + Resend delivery loop; static verification of the SQL change is complete (cf082e7) but end-to-end GDPR shape preservation must be confirmed against a fully-cutover environment before going to prod.

## Gap-Closure Summary (2026-06-04 follow-up — FINAL)

This re-verification was triggered by three gap-fix commits on branch `fix/rfantibody-altloc-handling`:

### Gap 1 — leftover `users.stripe_customer_id` reads (deletion_cron + admin/router)

**Fix commit:** `ee1ff77 fix(12-gaps): convert deletion_cron + admin/router stripe_customer_id reads to org JOIN`

**Confirmation:**
- `worker/deletion_cron.py:46-54` chains `users u → organization_memberships om → organizations o WHERE o.is_personal = true` and selects `o.stripe_customer_id`. The asyncpg row dict still contains a top-level `stripe_customer_id` key (alias rule), so `dict(row).get("stripe_customer_id")` at line 64 works unchanged.
- `admin/router.py:100-153` applies the same 3-table LEFT JOIN to both the keyset (`before_dt is not None`) and non-keyset paths. GROUP BY at lines 125 + 148 includes `o.stripe_customer_id`. Line 162 `payment_status: "active" if r["stripe_customer_id"] else "none"` is preserved.
- Leo-confirmed inline: 4/4 deletion_cron tests + 21/21 admin/router tests pass.

**Status:** VERIFIED — Gap 1 closed.

### Gap 2 — `/health` missing `organizations_enabled`

**Fix commit:** `1b7daa0 fix(12-gaps): expose organizations_enabled flag in /health payload`

**Confirmation:**
- `main.py:175-178` constructs `payload = {**checks, "organizations_enabled": settings.organizations_enabled}` and returns `JSONResponse(content=payload, status_code=status_code)`.
- `settings.organizations_enabled` is sourced from `config.py:149` (Pydantic settings, default `False`).
- The `healthy = all(v == "ok" for v in checks.values())` calculation at line 173 reads the inner `checks` dict BEFORE the bool is merged into `payload`, so a `True`/`False` flag value cannot poison the 200/503 status decision.
- Runbook probes at `docs/runbook-phase-12-rollout.md:34` + `:116` now resolve to a real boolean.

**Status:** VERIFIED — Gap 2 closed.

### Gap 3 — `backend/user/export.py` GDPR data-export path (surfaced in re-verification audit)

**Fix commit:** `cf082e7 fix(12-gaps): convert user/export GDPR profile dump to org JOIN`

**Confirmation:**
- `backend/user/export.py:111-122` (`_build_and_deliver_export_inner`) SELECT now reads:
  ```
  SELECT u.id, u.email, u.display_name, u.created_at,
         u.tos_version, u.tos_accepted_at,
         u.data_retention_days, u.deletion_requested_at,
         u.last_export_requested_at,
         u.notification_preferences, o.stripe_customer_id
  FROM public.users u
  LEFT JOIN public.organization_memberships om ON om.user_id = u.id
  LEFT JOIN public.organizations o
    ON o.id = om.organization_id AND o.is_personal = true
  WHERE u.id = $1
  ```
- The `o.stripe_customer_id` alias preserves the top-level `stripe_customer_id` key in the asyncpg row, so the downstream `profile.json` dump in the GDPR export keeps the exact same shape — no breaking change to the GDPR Article 20 contract.
- Cutover comment at export.py:107-110 documents the Phase 12 schema move and references the deletion_cron + admin/router precedent.
- Leo-confirmed inline (`cf082e7` body): 9/9 `tests/user/test_export.py` pass.
- The structural pattern matches Gap 1's fix exactly (same JOIN chain, same alias rule, same downstream-key preservation).

**Status:** VERIFIED — Gap 3 closed.

### Final grep audit

After all three commits, the only `users.stripe_customer_id`-pattern matches across `backend/**/*.py` are:
- Comments in `billing/stripe_client.py` (lines 10, 37) and `scripts/stamp_stripe_org_metadata.py` (line 5).
- The intentional pre-migration invariant test in `tests/integration/test_org_migration.py` (lines 68, 75, 80).
- A comment in `tests/webhooks/test_runpod_org_billing.py` (line 6).

Zero remaining live-code reads. The drop-column migration (runbook step 9) is now safe from a code-path perspective.

### Net delta from prior verifications

| Metric | Original (136ca32) | Post `1b7daa0` (299c49a) | Now (post `cf082e7`) |
|--------|--------------------|--------------------------|----------------------|
| Status | gaps_found | gaps_found | passed |
| Score | 31/33 | 32/33 | 33/33 |
| Failed truths | 2 (deletion_cron+admin, health) | 1 (user/export.py) | 0 |
| Closed gaps cumulative | — | 2 | 3 |
| New gaps surfaced cumulative | — | 1 | 0 |

**All audited gaps resolved. Phase 12 is code-complete and the runbook is unblocked for production execution (pending the five operator-side verifications listed above).**

## Status Verdict

**passed**

All three originally-flagged gaps (truths 32, 33, and the audit-surfaced 33b) are now VERIFIED via commits `ee1ff77`, `1b7daa0`, and `cf082e7`. The fixes are minimal, scoped, and Leo-attested. An independent grep audit confirms zero remaining live-code reads of the deprecated `public.users.stripe_customer_id` column.

Score is 33/33 (100%). The five human-verification items listed above are operator-side validations against a running stack and a staging deployment — none are blockers for the `passed` code-level verdict; they are pre-conditions for the runbook execution against production.

---

*Final-verified: 2026-06-04T14:30:00Z*
*Verifier: Claude (gsd-verifier, Opus 4.7 1M context)*
*Branch: fix/rfantibody-altloc-handling*
*HEAD: cf082e75a77fc81ee663265a9594c0da9c43d69d*
*Previous HEADs verified: 136ca32 (2026-06-04T23:55:00Z, original), 299c49a / 1b7daa0 (2026-06-04T12:55:00Z, re-verify after gaps 1+2)*
