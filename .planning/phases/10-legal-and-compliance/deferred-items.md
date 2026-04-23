Playwright e2e files (e2e/*.spec.ts) fail under 'vitest run' because vitest pattern-matches them but @playwright/test is not a vitest-compatible import. Pre-existing configuration issue, not introduced by Plan 10-02. All 50 unit tests pass including 4 new SignUp ToS tests.

## Plan 10-02 Task 5 — auto-approved (auto mode)

End-to-end browser verification steps 1-7 in the plan (navigate /signup, submit w/o checkbox,
inspect DB after signup, simulate tos_version drift, verify modal, accept, curl 400 for
wrong version) were not executed in this non-interactive run. Programmatic equivalents
all pass:
- Migration applied (user confirmed in Task 2): 3 columns + CHECK constraint verified via
  `supabase db query` with the documented SQL.
- Backend: 8 new pytest cases cover matching version, wrong version, missing field,
  accept-tos happy path, 404 edge, 401 edge, /settings shape including absence of
  deletion_requested_at.
- Frontend: 4 SignUp tests cover unchecked-by-default, submit block without tick,
  payload shape (email, password, tos_version), and legal links.
- tsc --noEmit clean; existing tests green.

Leo should run the manual steps once next time the backend + frontend are started
together; failures from that pass should be filed as a Phase 10 follow-up against
10-02.
