---
phase: 1
slug: foundation
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-18
---

# Phase 1 -- Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (backend) + vitest (frontend) |
| **Config file** | `backend/pytest.ini` or `backend/pyproject.toml` (installed via requirements.txt) |
| **Quick run command** | `cd backend && pytest tests/ -x -q` |
| **Full suite command** | `cd backend && pytest tests/ && cd ../frontend && npx vitest run` |
| **Estimated runtime** | ~30 seconds |

---

## Wave 0 Rationale

Wave 0 test stubs are NOT provided as a separate plan. Instead:

- **Backend:** Plan 01-02 Task 2 is a TDD task (`tdd="true"`) that creates `conftest.py`, `test_auth.py`, and the pytest configuration before implementing endpoints. The test-first requirement is satisfied by this task's TDD contract: tests are written and run (RED) before production code is modified (GREEN). `pytest` and `httpx` are installed via `backend/requirements.txt` in Plan 01-01.

- **Frontend:** Plan 01-03 Task 1 installs `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, and `jsdom` as dev dependencies and configures vitest in `vite.config.ts`. Plan 01-03 Task 2 creates `frontend/src/lib/api.test.ts` as a smoke test that validates the `ApiError` class. This ensures vitest is configured and passing before Plan 01-04 builds the auth screens.

This approach avoids a separate Wave 0 plan while preserving the test-first guarantee for both stacks.

---

## Sampling Rate

- **After every task commit:** Run `cd backend && pytest tests/ -x -q`
- **After every plan wave:** Run `cd backend && pytest tests/ && cd ../frontend && npx vitest run`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-02-01 | 02 | 2 | AUTH-01..04 | unit | `cd backend && python -c "from main import app; print('OK')"` | N/A | pending |
| 1-02-02 | 02 | 2 | AUTH-01..04 | integration | `cd backend && pytest tests/test_auth.py -x -q` | Created by task | pending |
| 1-03-01 | 03 | 2 | AUTH-04 | type-check | `cd frontend && npx tsc --noEmit` | N/A | pending |
| 1-03-02 | 03 | 2 | AUTH-04 | unit | `cd frontend && npx vitest run` | Created by task | pending |
| 1-04-01 | 04 | 3 | AUTH-01..04 | type-check | `cd frontend && npx tsc --noEmit` | N/A | pending |
| 1-04-02 | 04 | 3 | AUTH-01..04 | manual | Human verify (checkpoint) | N/A | pending |

*Status: pending / green / red / flaky*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Email verification link delivered and clickable | AUTH-01 | Requires real email or Supabase local dashboard inspection | Start dev env, register user, open Supabase Studio at localhost:54323, check Inbucket at localhost:54324 for email, click link, verify user record shows `email_confirmed_at` set |
| Password reset email received | AUTH-03 | Requires real email or Inbucket check | Trigger reset from UI, open Inbucket at localhost:54324, click link, set new password, verify login works |
| Session persists across browser refresh | AUTH-02 | Browser state not testable in unit tests | Log in via frontend, close and reopen tab, confirm user remains authenticated without re-auth prompt |
| MinIO bucket created on dev-up | AUTH-04 | Docker/shell orchestration outside pytest | Run `./dev-up.sh`, open MinIO console at localhost:9001, confirm `protein-designer` bucket (or configured name) exists with per-user prefix structure |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covered by Plan 01-02 TDD task (backend) and Plan 01-03 vitest setup (frontend)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending execution
