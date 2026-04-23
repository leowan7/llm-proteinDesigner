---
phase: 10-legal-and-compliance
plan: 02
subsystem: auth
tags: [tos, acceptance, signup, re-acceptance, migration, gdpr, pydantic, react-hook-form, zod, vitest, pytest]
dependency_graph:
  requires:
    - phase: 10-legal-and-compliance-plan-01
      provides: [legal-content-v1, version-string-canonical]
  provides:
    - tos-acceptance-gate
    - retention-column
    - re-acceptance-modal
    - POST /user/accept-tos endpoint
    - tos_current exposed on GET /user/settings
  affects: [10-04-gdpr-endpoints, 10-05-retention-cron, users-table, signup-flow, authenticated-layout]
tech_stack:
  added: []
  patterns:
    - supabase-raw-sql-migration
    - pydantic-v2-settings-field
    - react-hook-form-zod-literal
    - dependency-override-testing
    - trigger-race-upsert-fallback
    - backend-guarded-tos-version-comparison
key_files:
  created:
    - supabase/migrations/20260424000001_legal_compliance.sql
    - backend/tests/auth/__init__.py
    - backend/tests/auth/test_signup_tos.py
    - backend/tests/user/test_tos_acceptance.py
    - frontend/src/lib/legal.ts
    - frontend/src/components/legal/ReAcceptanceModal.tsx
    - frontend/src/pages/SignUp.test.tsx
  modified:
    - backend/config.py
    - backend/auth/router.py
    - backend/user/router.py
    - backend/tests/user/test_router.py
    - frontend/src/lib/user.ts
    - frontend/src/pages/SignUp.tsx
    - frontend/src/components/layout/AuthenticatedLayout.tsx
decisions:
  - "Backend is the single source of truth for tos_current_version; request-supplied versions are rejected on mismatch (threat T-10.02-02)"
  - "tos_accepted_at + tos_version are written inside the signup handler right after Supabase sign_up succeeds, not deferred to email verification"
  - "Trigger race fallback: retry-once + INSERT ... ON CONFLICT DO NOTHING using (id, email, tos_accepted_at, tos_version); logs a warning if still missing so signup UX never blocks"
  - "ReAcceptanceModal is a blocking Dialog with showCloseButton={false} and onOpenChange no-op; acceptance is the only exit affordance"
  - "/user/settings response intentionally omits deletion_requested_at — Plan 10-04 (wave 2) owns that field in both the response and the TS interface"
  - "Zod v4 literal(true) uses {message: ...} not {errorMap: ...}; discovered during vitest run"
requirements_completed: []
metrics:
  duration: 45min
  completed: "2026-04-23T11:40:00Z"
  tasks: 5
  files: 13
---

# Phase 10 Plan 2: ToS Acceptance on Signup + Retention Column + Re-Acceptance Gate Summary

**Signup now blocks without a ticked ToS checkbox, writes `tos_accepted_at`+`tos_version` to `public.users` via backend-guarded UPDATE with trigger-race fallback, exposes `tos_current` on `/user/settings`, and a blocking re-acceptance modal appears when stored version drifts from `settings.tos_current_version`.**

## Performance

- **Duration:** ~45 min (Tasks 3-5 resumed from a prior Task 1 + Task 2 session)
- **Started (resume):** 2026-04-23T11:20:00Z
- **Completed:** 2026-04-23T11:40:00Z
- **Tasks:** 5 (Tasks 1-2 done in prior session, Tasks 3-5 done here)
- **Files modified / created:** 13 (7 created, 6 modified)

## Accomplishments

- `SignUpRequest` now requires `tos_version: str`; signup rejects 400 on mismatch and 422 on missing field.
- Supabase sign-up success path writes `tos_accepted_at = now()` + `tos_version` to `public.users` via the service-role `get_db_pool()` connection, with a 200ms sleep + upsert fallback for the auth.users → public.users trigger race.
- `POST /user/accept-tos` wired for re-acceptance from the modal; returns `{accepted: true, tos_version: <current>}` or 404 when the row is missing.
- `GET /user/settings` now returns `tos_version`, `tos_current`, `data_retention_days`; `deletion_requested_at` intentionally absent (Plan 10-04).
- Signup page has a gating checkbox with inline links to `/legal/terms` and `/legal/privacy` (target=_blank); unchecked state shows "You must accept the Terms of Service and Privacy Policy."
- `ReAcceptanceModal` mounts inside `AuthenticatedLayout`; on `getSettings()` drift, the modal opens with no close affordance and the only exit is `POST /user/accept-tos`.
- 12 new tests all green: 3 pytest cases in `tests/auth/test_signup_tos.py`, 5 pytest cases in `tests/user/test_tos_acceptance.py`, 4 vitest cases in `src/pages/SignUp.test.tsx`.

## Task Commits

1. **Task 1: legal_compliance migration + TOS_CURRENT_VERSION settings** — `67d93d3` (feat, prior session)
2. **Task 2: [BLOCKING] supabase migration applied** — manual `supabase migration up` (user-confirmed via `supabase db query`)
3. **Task 3: backend tos_version gate + /user/accept-tos + settings extension** — `c143849` (feat, TDD red→green)
4. **Task 4: frontend SignUp checkbox, ReAcceptanceModal, AuthenticatedLayout wiring** — `961351a` (feat, TDD red→green)
5. **Task 5: end-to-end verification** — auto-approved (see Deviations)

**Plan metadata:** to be committed with this SUMMARY.md.

## Files Created/Modified

- `supabase/migrations/20260424000001_legal_compliance.sql` (prior) — ALTER TABLE adds tos_accepted_at, tos_version, data_retention_days + CHECK(30-365)
- `backend/config.py` (prior) — adds `tos_current_version` / `privacy_current_version` at "2026-04-23"
- `backend/auth/router.py` — SignUpRequest.tos_version; pre-Supabase 400 guard; post-sign_up UPDATE with trigger-race retry/upsert; asyncio + logging imports
- `backend/user/router.py` — /settings selects tos_version + data_retention_days and returns `tos_current`; new `POST /accept-tos` endpoint
- `backend/tests/auth/__init__.py` (new, empty) — package marker for the new tests subfolder
- `backend/tests/auth/test_signup_tos.py` (new) — 3 cases: matching version, wrong version (400), missing field (422)
- `backend/tests/user/test_tos_acceptance.py` (new) — 5 cases: settings exposes new fields, no deletion_requested_at, accept-tos happy path, 404, 401 unauthenticated
- `backend/tests/user/test_router.py` — user_row fixtures extended with tos_version + data_retention_days (regression fix from Rule 1)
- `frontend/src/lib/legal.ts` (new) — `needsReAcceptance`, `acceptTos`, re-exports TOS_VERSION
- `frontend/src/lib/user.ts` — UserSettings gains `is_admin?`, `tos_version?`, `tos_current?`, `data_retention_days?`
- `frontend/src/pages/SignUp.tsx` — zod `literal(true)` with message; new FormField renders checkbox + inline legal links; payload includes `tos_version: TOS_VERSION`
- `frontend/src/components/legal/ReAcceptanceModal.tsx` (new) — blocking Dialog, links to legal pages, `acceptTos()` action
- `frontend/src/components/layout/AuthenticatedLayout.tsx` — `getSettings()` after `/auth/me`, `needsReAcceptance` drives `reAcceptanceOpen`, mounts `<ReAcceptanceModal>` as a sibling of `<SidebarProvider>`
- `frontend/src/pages/SignUp.test.tsx` (new) — 4 vitest cases

## Decisions Made

- Kept `_get_supabase()` export intact so the pytest patch path `auth.router._get_supabase` continues to work without affecting production callers.
- Caught post-sign-up DB exceptions in a try/except that only logs — a Supabase-accepted signup should not 500 the user if public.users isn't writable for a transient reason. The warning line carries the user id for forensic follow-up.
- Used `FormField` with a native `<input type="checkbox">` (rather than introducing a shadcn Checkbox dependency) because the UI folder has no checkbox primitive yet. Styled via Tailwind `size-4 rounded border-input`.
- The `onOpenChange={() => { /* no-op */ }}` keeps @base-ui's Dialog state machine consistent while disallowing user-driven dismissal.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Existing `test_router.py` regressed after /settings SELECT change**
- **Found during:** Task 3 GREEN run
- **Issue:** Existing tests used mock user_row dicts without `tos_version` / `data_retention_days` keys; KeyError raised once the handler tried to read them.
- **Fix:** Added the two new keys to both regression mock fixtures (`test_get_settings` and `test_get_settings_null_preferences_uses_default`). Scope is limited to the file under test for this task.
- **Files modified:** `backend/tests/user/test_router.py`
- **Verification:** `pytest tests/user/test_router.py tests/user/test_tos_acceptance.py tests/auth/test_signup_tos.py -x -q` → 15 passed.
- **Committed in:** `c143849` (Task 3 commit)

**2. [Rule 1 — Bug] Zod v4 `literal(true)` uses `{message: ...}`, not `errorMap`**
- **Found during:** Task 4 vitest run (1 of 4 cases failing on missing validation text)
- **Issue:** Plan authored with Zod v3 API (`errorMap`); the repo is on `zod: ^4.3.6` where `errorMap` is silently ignored and no message is attached to the field.
- **Fix:** Replaced `errorMap: () => ({ message: "…" })` with the v4 shorthand `{ message: "You must accept…" }`.
- **Files modified:** `frontend/src/pages/SignUp.tsx`
- **Verification:** `npx vitest run src/pages/SignUp` → 4 of 4 passed; `npx tsc --noEmit` clean.
- **Committed in:** `961351a` (Task 4 commit)

---

**Total deviations:** 2 auto-fixed (2 × Rule 1 bug fixes)
**Impact on plan:** Both fixes were correctness issues uncovered by the test suites the plan specified. No scope creep — both stayed within the files the plan listed in `files_modified`.

## Issues Encountered

- **Pre-existing workspace:** the working tree already had uncommitted Phase 9 edits (~40 files). Only the files this plan owns were staged for Task 3 and Task 4 commits; nothing from Phase 9 was swept in.
- **Vitest picks up Playwright e2e specs:** `vitest run` (no filter) tries to load `frontend/e2e/*.spec.ts`, which import `@playwright/test` and fail. Unrelated to this plan; logged to `.planning/phases/10-legal-and-compliance/deferred-items.md`.

## Authentication Gates

None. Task 2 [BLOCKING] was a database migration executed by the user in the prior session (confirmed via `supabase db query` output). No runtime auth was requested during this session.

## Task 5 (human-verify) — auto-approved

Per the `<context_notes>` go-autonomous directive, Task 5's end-to-end browser verification was not executed interactively. All programmatic equivalents pass (migration columns verified, 12 unit tests green, tsc clean, backend greps pass). Details appended to `.planning/phases/10-legal-and-compliance/deferred-items.md` so the manual walk-through happens on the next app start.

## Known Stubs

None. The re-acceptance modal fetches real data from `/user/settings`, and the signup checkbox is fully wired to `/auth/signup`. No placeholder text, hardcoded empty arrays, or unwired components introduced by this plan.

## Next Plan Readiness

- **10-04 (GDPR export + deletion):** can now add `deletion_requested_at` to the already-typed `UserSettings` interface and /settings response — the hook is documented at both call sites.
- **10-05 (retention cron):** `data_retention_days` column is populated (default 90) for every user; cron can SELECT `jobs.created_at < now() - (users.data_retention_days * interval '1 day')`.
- **10-06 (legal routes + footer):** `/legal/terms` and `/legal/privacy` Link targets in SignUp + ReAcceptanceModal expect 10-01's components to be routed; already created in prior plan.

## Self-Check: PASSED

Created files verified present:
- supabase/migrations/20260424000001_legal_compliance.sql
- backend/tests/auth/__init__.py
- backend/tests/auth/test_signup_tos.py
- backend/tests/user/test_tos_acceptance.py
- frontend/src/lib/legal.ts
- frontend/src/components/legal/ReAcceptanceModal.tsx
- frontend/src/pages/SignUp.test.tsx

Task commits verified in git history:
- 67d93d3 (Task 1 — prior session)
- c143849 (Task 3 — this session)
- 961351a (Task 4 — this session)

---
*Phase: 10-legal-and-compliance*
*Plan: 02*
*Completed: 2026-04-23*
