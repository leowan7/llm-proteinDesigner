---
phase: 6
slug: ui-improvements
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-07
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | vitest (frontend) / pytest 7.x (backend) |
| **Config file** | `frontend/vitest.config.ts` / `backend/pytest.ini` |
| **Quick run command** | `cd frontend && npx vitest run --reporter=verbose` |
| **Full suite command** | `cd frontend && npx vitest run && cd ../backend && python -m pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd frontend && npx vitest run --reporter=verbose`
- **After every plan wave:** Run `cd frontend && npx vitest run && cd ../backend && python -m pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|-------------------|--------|
| 06-01-01 | 01 | 1 | SC-1 (sessions persist) | smoke (import) | `python -c "from sessions.queries import create_session; print('OK')"` | ⬜ pending |
| 06-01-02 | 01 | 1 | SC-2 (agent migration) | smoke (import) | `python -c "from sessions.router import router; print('OK')"` | ⬜ pending |
| 06-02-01 | 02 | 1 | SC-3 (job list endpoint) | smoke (import) | `python -c "from jobs.router import router; print('OK')"` | ⬜ pending |
| 06-02-02 | 02 | 1 | SC-4 (user settings) | smoke (import) | `python -c "from user.router import router; print('OK')"` | ⬜ pending |
| 06-03-01 | 03 | 2 | SC-5 (app shell) | compile | `cd frontend && npx tsc --noEmit` | ⬜ pending |
| 06-03-02 | 03 | 2 | SC-6 (ChatPage refactor) | compile | `cd frontend && npx tsc --noEmit` | ⬜ pending |
| 06-04-01 | 04 | 2 | SC-7 (job history page) | compile | `cd frontend && npx tsc --noEmit` | ⬜ pending |
| 06-04-02 | 04 | 2 | SC-8 (GreetingCard) | compile | `cd frontend && npx tsc --noEmit` | ⬜ pending |
| 06-05-01 | 05 | 3 | SC-9 (settings page) | compile | `cd frontend && npx tsc --noEmit` | ⬜ pending |
| 06-05-02 | 05 | 3 | SC-10 (WCAG audit) | compile | `cd frontend && npx tsc --noEmit` | ⬜ pending |
| 06-05-03 | 05 | 3 | SC-11 (e2e verify) | manual | Human checkpoint | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Behavioral Test Deferral

**Rationale:** Phase 6 is a UI-heavy phase (sidebar, settings page, job history, accessibility). The 8 behavioral test files identified in research (test_sessions.py, test_session_resume.py, test_jobs_list.py, test_user_usage.py, GreetingCard.test.tsx, AppSidebar.test.tsx, JobHistoryPage.test.tsx, SettingsPage.test.tsx) are deferred to the dedicated testing phase (Phase 9) for the following reasons:

1. **Context budget:** Phase 6 has 5 plans across 3 waves with significant implementation scope. Adding a Wave 0 test scaffolding plan would push context usage beyond the 50% target for multiple plans.
2. **Phase 6 verification coverage:** Each task has compile-time (tsc --noEmit) or import-based smoke checks that catch structural errors. Plan 05 includes a human-verify checkpoint for end-to-end functional verification.
3. **Phase 9 scope:** The project roadmap includes a testing/hardening phase where comprehensive behavioral tests (component tests, integration tests, API contract tests) will be authored systematically across all phases.

**Deferred test files:**
- `backend/tests/test_sessions.py` — session CRUD endpoint integration tests
- `backend/tests/test_session_resume.py` — message reconstruction tests
- `backend/tests/test_jobs_list.py` — paginated jobs list endpoint
- `backend/tests/test_user_usage.py` — usage summary endpoint
- `frontend/src/components/chat/GreetingCard.test.tsx` — example prompts render and click
- `frontend/src/components/AppSidebar.test.tsx` — session grouping, active indicator
- `frontend/src/pages/JobHistoryPage.test.tsx` — table render, pagination, empty state
- `frontend/src/pages/SettingsPage.test.tsx` — tab switching, form fields

---

## Wave 0 Requirements

- [ ] `eslint-plugin-jsx-a11y` — install for accessibility linting (handled in Plan 03 Task 1)

*Existing vitest/pytest infrastructure covers compile and import smoke checks.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Sidebar responsive collapse | SC-2 | Visual layout at breakpoints | Resize browser to 768px, 1024px; verify sidebar collapses to overlay/sheet |
| Color contrast WCAG AA | SC-6 | Requires visual contrast checker | Run axe-core audit on all new pages; verify no contrast failures |
| Keyboard navigation flow | SC-6 | Tab order requires manual verification | Tab through sidebar → chat → job history; verify focus indicators visible |
| Stripe Customer Portal redirect | SC-4 | External service integration | Click "Manage payment method" → verify Stripe portal opens |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify commands (compile checks or import smoke tests)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Behavioral tests deferred to Phase 9 with documented rationale
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved (with behavioral test deferral to Phase 9)
