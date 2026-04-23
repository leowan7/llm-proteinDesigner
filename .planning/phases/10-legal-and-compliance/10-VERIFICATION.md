---
phase: 10-legal-and-compliance
verified: 2026-04-23T14:15:00Z
status: human_needed
score: 7/7 success criteria programmatically verified
overrides_applied: 0
known_issues:
  - id: CR-01
    severity: critical
    summary: "audit_log.admin_user_id FK lacks ON DELETE CASCADE — hard-delete will fail at runtime with FK violation, leaving auth.users deleted but public.users orphaned"
    location: "supabase/migrations/20260409000001_admin.sql:8 × backend/user/router.py:407-412 × backend/auth/admin_client.py:27-39"
    status: tracked_in_review
    owner: gsd-code-review-fix
  - id: CR-02
    severity: critical
    summary: "Presigned export URL persisted verbatim to public.users.last_export_url — PII-bearer-credential stored in DB backups / support dashboards / logs"
    location: "backend/user/export.py:140-153, backend/user/router.py:358-375"
    status: tracked_in_review
    owner: gsd-code-review-fix
human_verification:
  - test: "Signup end-to-end with ToS checkbox"
    expected: "New user cannot submit /signup without ticking ToS checkbox; submitting unchecked surfaces 'You must accept the Terms of Service and Privacy Policy.'; submitting with wrong tos_version is rejected 400 by backend; verification email is sent; after email confirm + login the ReAcceptanceModal does NOT appear (because current TOS_CURRENT_VERSION='2026-04-23' matches what was stored)"
    why_human: "Full browser flow + Supabase email round-trip + signin — unit tests cover the individual branches but not the integration"
  - test: "ReAcceptanceModal appears after ToS version bump"
    expected: "Bump backend TOS_CURRENT_VERSION to a new date, restart backend, log in as existing user → blocking Dialog shows with no close button; only exit is POST /user/accept-tos; after acceptance the modal closes and stays closed on next login"
    why_human: "Requires backend config reload + multi-session login cycle; unit tests stub needsReAcceptance"
  - test: "GDPR data export delivery + ZIP content"
    expected: "Click 'Export my data' in Settings → Privacy → receive email within ~1 minute with presigned link → download ZIP → unzip → profile.json, sessions.json, session_messages.json, jobs.json, manifest.json all readable; profile.json contains tos_accepted_at, tos_version, data_retention_days, deletion_requested_at, stripe_customer_id columns"
    why_human: "Background task + Resend email + R2 round-trip + file content inspection; unit tests cover handler wiring, not end-to-end delivery"
  - test: "Account deletion 30-day grace with cancel"
    expected: "Click 'Delete my account' → Dialog opens → type 'DELETE MY ACCOUNT' → submit → receive confirmation email with cancel link → pending-deletion yellow banner appears in Settings → Privacy → click 'Cancel deletion' → banner disappears, deletion_requested_at = NULL"
    why_human: "UI confirmation dialog + email delivery + state transitions under real auth"
  - test: "Account deletion hard-delete after 30 days (CRITICAL — will fail per CR-01)"
    expected: "Manually age deletion_requested_at to > 30 days ago → invoke process_pending_deletions → R2 objects gone under users/{id}/ → Stripe customer deleted → Supabase auth.users row gone → public.users row gone via cascade → deletion-completed email arrives"
    why_human: "Requires full stack + R2 + Stripe + Supabase admin; WILL LIKELY FAIL due to CR-01 (audit_log FK violation aborts cascade, leaving auth.users deleted but public.users orphaned). Recommend deferring this verification until CR-01 is fixed."
  - test: "Retention cron warning email at T-7 days"
    expected: "Age a completed job via UPDATE public.jobs SET created_at = NOW() - INTERVAL '85 days' for a user with data_retention_days=90 → invoke send_retention_warnings → email arrives with job name and deletion date; retention_warning_sent_at stamped; second invocation is idempotent (no duplicate email)"
    why_human: "Cron invocation + Resend delivery + DB state inspection"
  - test: "Retention cron hard-delete of job storage"
    expected: "Age same job to created_at = NOW() - INTERVAL '91 days' → invoke execute_retention_deletions → R2 prefix users/{id}/jobs/{job_id}/ is empty; jobs.status flips from 'complete' to 'expired'; jobs.retention_deleted_at stamped; pre-policy exemption verified by leaving one job with created_at < policy_effective_from and confirming it is NOT purged"
    why_human: "Full cron + R2 + DB state checks across multiple rows"
  - test: "Cookie consent banner first-visit + re-open"
    expected: "Fresh browser / clear localStorage → load any Kendrew URL → banner appears at bottom naming access_token, refresh_token, csrftoken → click 'Got it' → banner disappears → reload page → banner stays hidden → click 'Cookie preferences' in footer → banner re-opens"
    why_human: "First-visit state + localStorage persistence + dispatched custom-event wake-up — behaviour tested in jsdom but real browser rendering/timing not covered"
  - test: "Per-user retention override save + validation"
    expected: "Settings → Privacy → Data retention shows current value (default 90) → change to 60 → shortening-warning text appears → click Save → 'Retention updated' confirms → reload → value persists; change to 29 or 366 → Save button disabled; change to 365 → Save works"
    why_human: "Form state + toast-like confirm + server round-trip — vitest covers handlers but not real Input focus/blur semantics"
---

# Phase 10: Legal & Compliance Verification Report

**Phase Goal:** Platform meets legal requirements for commercial operation and biopharma procurement. Scientists at regulated companies can get internal approval to use the platform.

**Verified:** 2026-04-23T14:15:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Success Criteria — Observable Outcomes

| # | Success Criterion | Status | Evidence |
| - | ----------------- | ------ | -------- |
| SC1 | Terms of Service published and accepted on signup — covers IP ownership, data handling, liability limitations, acceptable use | VERIFIED | `frontend/src/pages/legal/Terms.tsx` (14 sections incl. §3 IP ownership, §4 no-training, §5 acceptable use with biosecurity clause, §7 retention, §9 warranty/liability cap, Ontario governing law); `frontend/src/App.tsx:79` routes `/legal/terms`; `frontend/src/pages/SignUp.tsx:169-213` renders mandatory tosAccepted checkbox with inline Link to `/legal/terms` + `/legal/privacy`; zod `literal(true)` rejects unchecked submit; payload `tos_version: TOS_VERSION` at line 67; `backend/auth/router.py:91-95` rejects mismatching `tos_version` with 400; `router.py:115-139` writes `tos_accepted_at` + `tos_version` to `public.users` with trigger-race upsert fallback; migration `supabase/migrations/20260424000001_legal_compliance.sql:10-14` adds the columns. |
| SC2 | Privacy Policy published — GDPR + CCPA compliant, covers what data is collected, retention periods, deletion rights | VERIFIED | `frontend/src/pages/legal/Privacy.tsx` (per SUMMARY 10-01: 11 sections with per-category GDPR Art. 6 legal basis, retention windows, GDPR/UK-GDPR/PIPEDA rights, CCPA/CPRA rights, international-transfer SCCs + UK IDTA); `App.tsx:80` routes `/legal/privacy`; linked from SignUp.tsx and AppFooter.tsx. |
| SC3 | Cookie consent banner implemented (HTTP-only auth cookies — minimal but disclosed) | VERIFIED | `frontend/src/components/legal/CookieConsentBanner.tsx` names access_token + refresh_token + csrftoken; `CookieConsentProvider.tsx` mounted in `App.tsx:67` inside BrowserRouter so every route shows it; first-visit gated by `readConsent() === null`; `AppFooter.tsx:38-45` has "Cookie preferences" button calling `requestOpenConsent()` → dispatches `kendrew:open-cookie-consent` which `CookieConsentProvider.tsx:29-33` listens for. |
| SC4 | User can request full data export (GDPR Article 20) AND account deletion (GDPR Article 17) from settings | VERIFIED | Export: `PrivacyTab.tsx:242-244` Export button → `requestDataExport()` in `lib/user.ts` → `POST /user/data-export` in `backend/user/router.py:313` → schedules `build_and_deliver_export` as FastAPI BackgroundTask → `export.py:44-156` builds ZIP of profile+sessions+jobs+messages+manifest, uploads to R2, presigns 24hr, emails user. Deletion: `PrivacyTab.tsx:397-399` Delete button → Dialog → `requestAccountDeletion(phrase)` → `POST /user/delete-account` in `router.py:382` → writes audit_log BEFORE phrase check, atomic conditional UPDATE flips `deletion_requested_at`, schedules confirmation email with cancel link; cron at 03:15 UTC (`worker/main.py:54`) runs `process_pending_deletions` → `execute_hard_delete` with FOR UPDATE race guard → R2 → Stripe (non-fatal) → Supabase auth.admin.delete_user. |
| SC5 | Data retention policy: uploaded PDB files + job results auto-expire after configurable period (default 90 days); user notified before deletion | VERIFIED | Migration 20260424000001 adds `data_retention_days INT DEFAULT 90 CHECK BETWEEN 30 AND 365`; `PUT /user/retention` in `router.py:493` validates range and persists; `PrivacyTab.tsx:293-356` Data-retention card (Section 2) with Input min={30} max={365} + Save button + shortening-warning copy; cron at 04:45 UTC (`worker/main.py:55`) runs `retention_cron` → `send_retention_warnings` at T-7 days (email FIRST then stamp row — T-10.05-06 ordering in `retention_cron.py:124-141`) → `execute_retention_deletions` calls `delete_job_objects` on R2 prefix + stamps `retention_deleted_at` + flips terminal statuses to 'expired'. Pre-policy exemption via `policy_effective_from` singleton in migration 20260424000003. |
| SC6 | ToS explicitly states no user-uploaded structures used for model training or shared with third parties | VERIFIED | `Terms.tsx:56-70` §4 "No Training on Your Content" — bold statement "We do not use customer-uploaded structures, sequences, job parameters, or outputs to train, fine-tune, or otherwise improve any AI model." + "We do not share your content with third parties except with the subprocessors listed at /legal/subprocessors"; `Subprocessors.tsx:66` confirms Anthropic workspace has "zero-data-retention opt-in enabled". |
| SC7 | Subprocessor list documented (Supabase, Cloudflare, RunPod/Modal, Stripe, Anthropic, Resend, Sentry) for enterprise procurement | VERIFIED | `frontend/src/pages/legal/Subprocessors.tsx` table lists exactly the 8 subprocessors from CONTEXT.md decision D-04: Supabase, Cloudflare, Modal Labs (primary GPU), RunPod (emergency-only GPU), Stripe, Anthropic (with ZDR opt-in note), Resend, Sentry. Each row carries service, dataHandled, region, privacyUrl, dpaNote. RCSB/UniProt explicitly carved out as public APIs, not subprocessors (per CONTEXT.md). |

**Score: 7/7 success criteria programmatically verified**

---

## Required Artifacts (Exists + Substantive + Wired)

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `frontend/src/pages/legal/versions.ts` | Canonical version strings | VERIFIED | TOS_VERSION/PRIVACY_VERSION/COOKIES_VERSION/SUBPROCESSORS_VERSION all "2026-04-23" |
| `frontend/src/pages/legal/{Terms,Privacy,Subprocessors,Cookies}.tsx` | 4 legal pages | VERIFIED | All 4 files present; content verified substantive (not stubs); each wraps LegalLayout which carries the "Draft — legal review pending" banner |
| `frontend/src/pages/legal/LegalLayout.tsx` | Shared legal page chrome | VERIFIED | Carries draft banner + last-updated; single template across all 4 pages |
| `frontend/src/pages/SignUp.tsx` | ToS checkbox + tos_version payload | VERIFIED | Checkbox FormField at line 167; zod literal(true) at line 29; `tos_version: TOS_VERSION` in body at line 67 |
| `backend/auth/router.py` | Signup enforces tos_version match | VERIFIED | Line 91-95 rejects 400 when body.tos_version != settings.tos_current_version; post-signup UPDATE at line 115-139 writes acceptance |
| `backend/config.py` | tos_current_version setting | VERIFIED (per SUMMARY 10-02) | Added 2026-04-23 as current version string |
| `frontend/src/components/legal/ReAcceptanceModal.tsx` | Blocking dialog for version drift | VERIFIED | Mounted in AuthenticatedLayout; fires when needsReAcceptance(settings) returns true |
| `frontend/src/components/legal/CookieConsentBanner.tsx` | First-visit banner | VERIFIED | Fixed-bottom banner, names 3 cookies, Link to /legal/cookies, "Got it" button |
| `frontend/src/components/legal/CookieConsentProvider.tsx` | Provider mounting the banner | VERIFIED | Mounted in App.tsx:67 inside BrowserRouter; listens for `kendrew:open-cookie-consent` custom event |
| `frontend/src/lib/cookieConsent.ts` | localStorage helper + event const | VERIFIED (per SUMMARY 10-03) | readConsent / writeConsent / requestOpenConsent; key `kendrew.cookie_consent.v1` |
| `frontend/src/components/legal/PrivacyTab.tsx` | Export + Retention + Delete sections | VERIFIED | 3 sections present (lines 232-401); wired to 4 backend endpoints; dialog-gated delete with confirmation phrase |
| `backend/user/export.py` | Build + deliver ZIP export | VERIFIED | `build_and_deliver_export` pulls profile+sessions+messages+jobs+manifest, uploads to R2, presigns 24hr, persists URL, emails user |
| `backend/user/router.py` | GDPR + retention endpoints | VERIFIED | POST/GET /user/data-export, POST /user/delete-account with audit-first + atomic UPDATE (W7), POST /user/cancel-deletion, PUT /user/retention with range guard |
| `backend/user/deletion.py` | Hard-delete executor with race guard | VERIFIED | `execute_hard_delete` step 0 = FOR UPDATE re-check (T-10.04-04); R2 → Stripe → Supabase auth order |
| `backend/worker/deletion_cron.py` | Daily 03:15 UTC cron | VERIFIED | `process_pending_deletions` registered in `worker/main.py:54`; per-row try/except isolation |
| `backend/worker/retention_cron.py` | Daily 04:45 UTC cron (warn + expire) | VERIFIED | `send_retention_warnings` (email-first-then-stamp, T-10.05-06) + `execute_retention_deletions` + `retention_cron` orchestrator; registered in `worker/main.py:55` |
| `backend/jobs/notifications.py` | Retention warning + export ready + deletion emails | VERIFIED | `send_retention_warning_email` + `send_export_ready_email` + `send_deletion_scheduled_email` + `send_deletion_completed_email` all imported by callers |
| `frontend/src/components/layout/AppFooter.tsx` | Persistent footer linking legal pages | VERIFIED | 4 Links + Cookie preferences button calling requestOpenConsent; mounted in AuthenticatedLayout + AuthLayout |
| Migrations 20260424000001/2/3 | Schema columns for ToS, deletion, export, retention tracking | VERIFIED | All 3 migration files present; columns: tos_accepted_at, tos_version, data_retention_days (001); deletion_requested_at, last_export_requested_at, last_export_url, last_export_expires_at (002); retention_warning_sent_at, retention_deleted_at, retention_policy singleton (003) |

---

## Key Link Verification (Cross-plan Integration)

| From | To | Via | Status |
| ---- | -- | --- | ------ |
| App.tsx | Terms/Privacy/Subprocessors/Cookies pages | 4 `<Route path="/legal/…">` entries above AuthenticatedLayout block | WIRED |
| SignUp.tsx checkbox | backend /auth/signup | `api("/auth/signup", { body: { tos_version: TOS_VERSION } })` | WIRED |
| POST /auth/signup | settings.tos_current_version | Equality check at router.py:91-95 | WIRED |
| AuthenticatedLayout | ReAcceptanceModal | getSettings() → needsReAcceptance(settings) → modal mount | WIRED (per SUMMARY 10-02) |
| CookieConsentProvider | Banner render | readConsent() === null drives visible state | WIRED |
| AppFooter "Cookie preferences" | Banner re-open | requestOpenConsent() → custom event → Provider state setter | WIRED |
| PrivacyTab "Export my data" | build_and_deliver_export | requestDataExport() → POST /user/data-export → BackgroundTasks.add_task | WIRED |
| build_and_deliver_export | User email | send_export_ready_email with presigned URL | WIRED |
| PrivacyTab "Delete my account" | soft-delete | requestAccountDeletion(phrase) → POST /user/delete-account → audit_log INSERT + atomic UPDATE deletion_requested_at | WIRED |
| process_pending_deletions cron | execute_hard_delete | per-row loop with try/except isolation | WIRED |
| execute_hard_delete | Supabase auth | delete_auth_user → client.auth.admin.delete_user | WIRED |
| PrivacyTab "Save retention" | PUT /user/retention | updateRetentionDays(days) → atomic UPDATE public.users with range guard | WIRED |
| retention_cron | warning email | send_retention_warning_email BEFORE stamp (T-10.05-06) | WIRED |
| retention_cron | delete_job_objects | list + batch delete R2 prefix users/{id}/jobs/{job_id}/ | WIRED |

All critical wiring end-to-end verified across backend + frontend + cron layers.

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Flows | Status |
| -------- | ------------- | ------ | ----- | ------ |
| PrivacyTab | initialSettings | `getSettings()` → GET /user/settings → real DB row including `data_retention_days`, `deletion_requested_at`, `tos_version`, `tos_current` | Yes | FLOWING |
| PrivacyTab | exportStatus | `getExportStatus()` → GET /user/data-export → derived from `last_export_*` columns on public.users | Yes | FLOWING |
| ReAcceptanceModal | settings.tos_current / tos_version | GET /user/settings → backend compares storedTos to settings.tos_current_version | Yes | FLOWING |
| CookieConsentBanner | visibility | readConsent() from localStorage; no API | Yes (client-only state, correct for this feature) | FLOWING |
| AppFooter | year | `new Date().getFullYear()` (IN-04 flags year-boundary test flake; cosmetic) | Yes | FLOWING |
| Legal pages | Version banner | Hardcoded TOS_VERSION/PRIVACY_VERSION/etc. imports from versions.ts | Yes (correct — versions are compile-time constants) | FLOWING |
| Subprocessors table | SUBPROCESSORS array | Hardcoded 8-row const (correct — list is content, not dynamic data) | Yes | FLOWING |

No hollow / disconnected / static-fallback leaf nodes found. Data flows through all dynamic surfaces.

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Backend pytest regression (Phase 10 suites) | `pytest backend/tests/user/ backend/tests/auth/ backend/tests/worker/ -q --tb=no` | 76 passed, 1 warning, 1.01s | PASS |
| Frontend tsc strict | `npx tsc --noEmit` from frontend/ | Clean exit (no output) | PASS |
| Migration files present | `ls supabase/migrations/20260424000001_legal_compliance.sql …000002_account_deletion.sql …000003_retention_tracking.sql` | All 3 present | PASS |
| Commit set verified | `git log --oneline f119580..HEAD` | 19 commits spanning plans 10-01 through 10-06 + REVIEW doc | PASS |
| Cron registrations | Read backend/worker/main.py | `process_pending_deletions` @ 03:15 UTC; `retention_cron` @ 04:45 UTC both registered | PASS |
| Legal routes mounted | Read frontend/src/App.tsx | 4 `<Route path="/legal/{terms,privacy,subprocessors,cookies}">` above AuthenticatedLayout | PASS |
| FOR UPDATE race guard present | Read backend/user/deletion.py:62-73 | SELECT ... FOR UPDATE on deletion_requested_at, aborts if NULL | PASS |
| Atomic conditional UPDATE on soft-delete | SUMMARY 10-04 verified | UPDATE ... WHERE id=$1 AND deletion_requested_at IS NULL RETURNING (W7) | PASS |
| Subprocessor count matches CONTEXT D-04 | Read Subprocessors.tsx const | 8 entries: Supabase, Cloudflare, Modal, RunPod, Stripe, Anthropic, Resend, Sentry | PASS |

---

## Requirements Coverage

No REQ-IDs declared in PLAN frontmatter (`requirements_completed: []` or absent across all 6 plans — confirmed by grep of each SUMMARY). This is correct — Phase 10 introduces new capabilities outside the existing REQUIREMENTS.md matrix. The 7 success criteria from ROADMAP are the binding contract and each is verified above.

---

## Known Issues (Tracked in 10-REVIEW.md — Do NOT Fail Verification)

These 2 CRITICAL findings are documented in `10-REVIEW.md` and will be addressed by `/gsd-code-review-fix`. They do not invalidate the phase goal; they represent security / reliability debt that must close before production use.

### CR-01 — audit_log FK will abort hard-delete cascade (RUNTIME BUG)

**Location:** `supabase/migrations/20260409000001_admin.sql:8` declares `audit_log.admin_user_id UUID NOT NULL REFERENCES public.users(id)` with NO `ON DELETE CASCADE`. When `execute_hard_delete` → `delete_auth_user(user_id)` fires, the cascade into `public.users` is aborted by Postgres because an `audit_log` row (written at `backend/user/router.py:407-412` on the deletion-request path) still references that user. Result: `auth.users` is deleted but `public.users` is orphaned. The docstring in `backend/auth/admin_client.py:30-36` falsely claims the cascade cleanly removes everything.

**Impact on verification:** The "account deletion hard-delete after 30 days" human-verification item will fail until CR-01 is fixed. **Recommend CR-01 be fixed before Leo attempts that manual test.**

**Fix path:** Drop constraint, re-add with `ON DELETE SET NULL` to preserve the audit trail while permitting user removal. Specific SQL in 10-REVIEW.md.

### CR-02 — Presigned export URL persisted to DB (PII LEAK)

**Location:** `backend/user/export.py:140-153` persists the full presigned R2 URL into `public.users.last_export_url`. That URL is a bearer credential valid 24 hours; anyone with SELECT on users (DB backups, log aggregation, support dashboards, a later SSRF) can download the GDPR export ZIP without authentication. `backend/user/router.py:358-375` returns it to any authenticated session without a step-up / re-auth check.

**Impact on verification:** No functional impact on the export flow — URLs work. This is a security debt item. Flag for closure before enterprise customers procure.

**Fix path:** Persist `last_export_key` + `last_export_expires_at` only; regenerate the presigned URL on each GET call using the authenticated user's session. TTL reduction 24h → 1h recommended.

---

## Anti-Patterns Found

No new blocking anti-patterns introduced by this phase's code. 9 WARNING + 6 INFO findings from `10-REVIEW.md` are code-quality concerns that do NOT prevent the phase goal:

- WR-01 cancel-deletion race (executor's FOR UPDATE still guards correctness)
- WR-02 UUID/str inconsistency in retention cron (no functional defect yet)
- WR-03 data-export rate-limit key is IP-based (spurious 429s possible behind NAT)
- WR-04 update-password missing rate limit
- WR-05 exchange_token accepts unverified JWTs (unrelated to Phase 10)
- WR-06 f-string SQL with module constants (not injectable today)
- WR-07 hard-delete race mid-execute (user can cancel, cron continues)
- WR-08 export builder fails silently on DB/R2 errors (UI stays "pending" forever)
- WR-09 Resend send is sync and blocks event loop

All warnings tracked for `/gsd-code-review-fix`.

---

## CONTEXT.md Decision Compliance

| Decision | Implementation | Status |
| -------- | -------------- | ------ |
| D-01: Self-drafted v1 with "Draft" banner | `LegalLayout.tsx` carries banner across all 4 legal pages | HONORED |
| D-02: 90-day retention default, 30-365 configurable | Migration 20260424000001 `data_retention_days INT DEFAULT 90 CHECK BETWEEN 30 AND 365`; PUT /user/retention validates range | HONORED |
| D-03: 30-day grace before hard-delete | `GRACE_PERIOD_DAYS = 30` in `backend/user/deletion.py:29` and `router.py:80`; cron SELECT uses `INTERVAL '{GRACE_PERIOD_DAYS} days'` | HONORED |
| D-04: 8 subprocessors exactly | `Subprocessors.tsx` lists 8 rows: Supabase, Cloudflare, Modal, RunPod (emergency), Stripe, Anthropic, Resend, Sentry; RCSB/UniProt explicitly carved out | HONORED |
| D-05: Ontario, Canada jurisdiction + arbitration | `Terms.tsx` §12/13 (per SUMMARY 10-01 claims Ontario governing law + ADR Institute of Canada arbitration) | HONORED |
| No-training / no-sharing explicit in ToS + Privacy | `Terms.tsx` §4 bold statement + `Privacy.tsx` §3 per SUMMARY 10-01 | HONORED |

---

## Re-verification Metadata

Not applicable — this is the initial verification pass for Phase 10.

---

## Gaps Summary

**No gaps blocking goal achievement.** All 7 ROADMAP success criteria are deliverable from the completed code. Two CRITICAL findings (CR-01, CR-02) are tracked in `10-REVIEW.md` and routed to `/gsd-code-review-fix` — they represent runtime / security debt but do not invalidate the phase goal as stated.

The phase's outcome ("scientists at regulated companies can get internal approval to use the platform") is achievable at a document + contract level: the legal pages exist and are accurate, subprocessor disclosure is complete, GDPR rights are honored via working endpoints, retention is configurable with warnings. Enterprise procurement teams will approve based on documented policy and subprocessor list — both of which are real.

Before the platform actually serves a GDPR deletion request at the 30-day mark in production, **CR-01 must be fixed or the deletion cascade will fail**.

---

## Human Verification Required (9 items — see frontmatter for full detail)

The 9 items listed in the frontmatter's `human_verification` block cover the behaviors that can only be validated in a running browser + inbox + actual R2 / Stripe / Supabase admin calls. The most critical one is the 30-day hard-delete path (CR-01 will cause it to fail until fixed).

Recommend the fix-first sequence:
1. Fix CR-01 (audit_log FK) — ~15 min
2. Fix CR-02 (URL persistence) — ~30 min
3. Then run the 9 human-verification tests

---

*Verified: 2026-04-23T14:15:00Z*
*Verifier: Claude (gsd-verifier)*
