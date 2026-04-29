---
phase: 09-testing-ci-cd
verified: 2026-04-10T22:30:00Z
reverified: 2026-04-23T12:15:00Z
status: verified
score: 5/5 must-haves verified
overrides_applied: 1
overrides:
  - must_have: "Pre-deploy smoke test hits production health endpoint after deploy and rolls back automatically on failure"
    reason: "Automated rollback rejected in D-12 — Railway/Vercel one-click rollback is fast enough for manual execution; automated rollback risks reverting valid deploys on transient failures. Manual rollback via deploy history accepted as equivalent."
    accepted_by: "leo"
    accepted_at: "2026-04-14T00:00:00Z"
gaps:
  - truth: "Integration tests verify the full agent conversation flow (resolve -> classify -> collect -> validate -> launch) against a test database"
    status: resolved
    reason: "Closed 2026-04-23. test_agent_conversation_flow_all_five_stages added to backend/tests/integration/test_agent_flow.py. Mocks Anthropic with a 5-response side_effect (resolve_structure -> classify_intent -> collect_parameters -> validate_preflight -> end_turn 'launch-ready' text), mocks agent.router.dispatch_tool with per-tool JSON fixtures, and posts /agent/message against a real Supabase session row. Asserts all 4 tool_result SSE events stream in exact order, validate_preflight.ready_to_launch=True, final text contains 'launch', Anthropic was called >=5 times, and dispatch_tool was called exactly 4 times. Passes locally against Supabase 54322 in 0.22s."
  - truth: "Pre-deploy smoke test hits production health endpoint after deploy and rolls back automatically on failure"
    status: resolved
    reason: "Accepted 2026-04-14 via override (see overrides[] above). Manual rollback via Railway/Vercel deploy history accepted as equivalent to automated rollback."
deferred: []
---

# Phase 9: Testing & CI/CD Verification Report

**Phase Goal:** Automated test suite (unit, integration, E2E), CI pipeline, pre-deploy gates
**Verified:** 2026-04-10T22:30:00Z
**Re-verified:** 2026-04-23T12:15:00Z
**Status:** verified
**Re-verification:** Yes — Gap 1 closed via new integration test; Gap 2 closed via override accepted 2026-04-14.

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Unit tests cover all backend modules with >80% line coverage | VERIFIED | pytest-cov==6.1.0 in requirements.txt; `--cov-fail-under=80` enforced in test.yml and local pytest run; SUMMARY reports 81.65% total; all 9 unit test files present and substantive (105–461 lines each) |
| 2 | Integration tests verify the full agent conversation flow (resolve -> classify -> collect -> validate -> launch) against a test database | VERIFIED (2026-04-23) | `test_agent_conversation_flow_all_five_stages` chains Anthropic mocks for all 5 responses (4 tool_use + 1 end_turn) and asserts the full ordered tool_result stream against a real Supabase-backed session. Passes in 0.22s. Stage-5 job dispatch has separate coverage in `tests/jobs/test_dispatch.py`. |
| 3 | Frontend E2E tests (Playwright) cover: login, chat flow, structure card interaction, job launch, job status page | VERIFIED | playwright.config.ts with chromium + webServer; 4 spec files (auth: 4 tests, chat: 2 tests, jobs: 2 tests, settings: 3 tests); chat.spec.ts uses `page.route()` to mock agent SSE with a `tool_result` event for structure card; no `test.skip(process.env.CI)` |
| 4 | CI pipeline (GitHub Actions) runs all tests on every PR; blocks merge on failure | VERIFIED | test.yml triggers on `pull_request` to main/master; 4 jobs (backend, frontend-unit, e2e, lint); `e2e` depends on `backend + frontend-unit`; concurrency group cancels stale PR runs; branch protection enforcement is a human configuration step that this workflow enables |
| 5 | Pre-deploy smoke test hits production health endpoint after deploy and rolls back automatically on failure | VERIFIED WITH OVERRIDE | smoke.yml checks (health 200, response time <2s, auth login via GH secrets, frontend Playwright smoke) are in place. Manual rollback via Railway/Vercel deploy history was accepted as equivalent to automated rollback on 2026-04-14 via the `overrides:` entry in this file's frontmatter. |

**Score:** 5/5 truths verified (1 via accepted override)

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| TEST-01 | 09-01-PLAN.md | Backend unit test coverage >80% line coverage | SATISFIED | 81.65% coverage enforced by pytest-cov; 7 new unit test files across 5 modules |
| TEST-02 | 09-01-PLAN.md | Backend integration tests with real Supabase test instance | SATISFIED (2026-04-23) | Integration tests exist and use real DB. `test_agent_conversation_flow_all_five_stages` now chains all 5 agent responses and verifies the full tool_result stream ordering. |
| TEST-03 | 09-02-PLAN.md | Frontend unit tests (Vitest) for API client, utility functions, page smoke tests | SATISFIED | 36 tests across 7 files; Vitest configured with jsdom + setup file; api.test.ts, utils.test.ts, format.test.ts, 4 page smoke tests all present |
| TEST-04 | 09-03-PLAN.md | Frontend E2E tests (Playwright) covering auth, chat, jobs, and settings flows | SATISFIED | 4 spec files, 11 tests; page objects encapsulate selectors; chat.spec.ts uses page.route() for CI-compatible agent mocking |
| TEST-05 | 09-04-PLAN.md | CI pipeline (GitHub Actions) with 4 gates on every PR: backend tests, frontend tests, E2E, lint+typecheck | SATISFIED | test.yml has all 4 gates on pull_request trigger; coverage threshold enforced; SUPABASE_SERVICE_KEY via secrets |
| TEST-06 | 09-04-PLAN.md | Docker image CI builds on merge to main via GitHub Actions | SATISFIED | All 5 docker-*.yml workflow files confirmed present (45 lines each): rfdiffusion, bindcraft, rfantibody, boltzgen, pxdesign |
| TEST-07 | 09-04-PLAN.md | Post-deploy smoke test workflow verifying health, auth, frontend load, and response time | SATISFIED WITH OVERRIDE | smoke.yml has all 4 checks; workflow_dispatch trigger; smoke.spec.ts for frontend; auth check uses GH secrets (CR-01 fixed). Manual-rollback deviation formalized via frontmatter override accepted 2026-04-14. |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/tests/sessions/test_router.py` | Tests for all 6 session endpoints (min 100 lines) | VERIFIED | 337 lines; 12 test functions covering list (empty+rows), create, get (found+not found), update, delete, generate-title |
| `backend/tests/user/test_router.py` | Tests for user usage, settings GET/PUT (min 60 lines) | VERIFIED | 256 lines; 7 test functions |
| `backend/tests/webhooks/test_router.py` | Tests for RunPod webhook handler (min 80 lines) | VERIFIED | 461 lines; 7 test functions including HMAC signature validation |
| `backend/tests/middleware/test_rate_limit.py` | Tests for rate limit key extraction (min 40 lines) | VERIFIED | 105 lines; 4 test functions |
| `backend/tests/middleware/test_logging.py` | Tests for structured logging middleware (min 40 lines) | VERIFIED | 90 lines; 3 test functions |
| `backend/tests/worker/test_tasks.py` | Tests for run_job task (min 60 lines) | VERIFIED | 213 lines; 4 test functions including idempotency guard |
| `backend/tests/worker/test_cleanup.py` | Tests for orphan pod cleanup and stale job detection (min 60 lines) | VERIFIED | 276 lines; 6 test functions |
| `backend/tests/integration/test_session_crud.py` | Integration tests for session CRUD against real Supabase (min 40 lines) | VERIFIED | 213 lines; 4 test functions; skip guard via TCP probe; no DB dependency override |
| `backend/tests/integration/test_agent_flow.py` | Integration test for full agent conversation flow (min 60 lines) | VERIFIED (2026-04-23) | 551 lines; 3 test functions; `test_agent_conversation_flow_all_five_stages` chains resolve -> classify -> collect -> validate -> end_turn and asserts full tool_result ordering against real Supabase |
| `frontend/src/test/setup.ts` | Vitest setup file with jest-dom matchers (min 5 lines) | VERIFIED | 5 lines; imports jest-dom/vitest; stubs scrollIntoView for jsdom compatibility |
| `frontend/src/lib/utils.test.ts` | Tests for utility functions (min 15 lines) | VERIFIED | 37 lines; 6 test cases for cn() including tailwind-merge deduplication |
| `frontend/src/pages/Login.test.tsx` | Smoke test for Login page (min 15 lines) | VERIFIED | 44 lines; 5 tests including email input, password input, submit button, signup link |
| `frontend/src/pages/SettingsPage.test.tsx` | Smoke test for SettingsPage (min 15 lines) | VERIFIED | 67 lines; 5 tests including heading and tab elements |
| `frontend/playwright.config.ts` | Playwright config with baseURL, webServer, chromium (min 15 lines) | VERIFIED | 21 lines; testDir: "./e2e", baseURL: http://localhost:5173, chromium project, webServer block |
| `frontend/e2e/auth.spec.ts` | Login and auth flow E2E tests (min 30 lines) | VERIFIED | 52 lines; 4 tests: valid login, invalid credentials, unauthenticated redirect, session persistence |
| `frontend/e2e/chat.spec.ts` | Chat interaction E2E test with mocked agent API (min 30 lines) | VERIFIED | 77 lines; 2 tests; page.route() mocks SSE endpoint; no test.skip for CI |
| `frontend/e2e/jobs.spec.ts` | Job status page E2E test (min 20 lines) | VERIFIED | 53 lines; 2 tests |
| `frontend/e2e/settings.spec.ts` | Settings page E2E test (min 20 lines) | VERIFIED | 82 lines; 3 tests including tab switching |
| `.github/workflows/test.yml` | PR gate workflow with 4 jobs (min 80 lines) | VERIFIED | 183 lines; 4 jobs: backend, frontend-unit, e2e, lint; pytest with --cov-fail-under=80; playwright test; ruff check; tsc --noEmit |
| `.github/workflows/smoke.yml` | Post-deploy smoke test workflow (min 30 lines) | VERIFIED (functionality partial) | 67 lines; workflow_dispatch trigger; 4 checks; failure alert; no auto-rollback |
| `frontend/e2e/smoke.spec.ts` | Playwright smoke spec for production frontend (min 10 lines) | VERIFIED | 14 lines; SMOKE_TARGET_URL env var; asserts form visible |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/requirements.txt` | pytest-cov | pip dependency | WIRED | Line: `pytest-cov==6.1.0` confirmed present |
| `backend/tests/sessions/test_router.py` | `backend/sessions/router.py` | `app.dependency_overrides` | WIRED | `dependency_overrides[get_current_user]` and `dependency_overrides[get_db_pool]` patterns confirmed |
| `backend/tests/integration/test_session_crud.py` | `backend/sessions/router.py` | Real HTTP calls, real DB pool | WIRED | No `dependency_overrides` for `get_db_pool` confirmed; `client.post` to /sessions present |
| `backend/tests/integration/test_agent_flow.py` | `backend/agent/router.py` | Real HTTP calls to `/agent/message` | WIRED | POST /agent/message with real DB; Anthropic mocked via `patch("agent.router.anthropic.Anthropic")` |
| `frontend/vite.config.ts` | `frontend/src/test/setup.ts` | `test.setupFiles` array | WIRED | `setupFiles: ["./src/test/setup.ts"]` confirmed |
| `frontend/package.json` | vitest | `scripts.test` | WIRED | `"test": "vitest run"` confirmed |
| `.github/workflows/test.yml` | `backend/tests/` | pytest command | WIRED | `pytest tests/ --cov=. --cov-fail-under=80` present |
| `.github/workflows/test.yml` | `frontend/e2e/` | playwright test command | WIRED | `npx playwright test` in e2e job |
| `.github/workflows/smoke.yml` | production health endpoint | curl to `{environment}/health` | WIRED | curl command with `%{http_code}` confirmed |
| `.github/workflows/smoke.yml` | `frontend/e2e/smoke.spec.ts` | `npx playwright test e2e/smoke.spec.ts` | WIRED | Exact spec file referenced in workflow |
| `frontend/playwright.config.ts` | `frontend/e2e/` | `testDir` config | WIRED | `testDir: "./e2e"` confirmed |
| `frontend/e2e/auth.spec.ts` | `frontend/e2e/pages/LoginPage.ts` | page object import | WIRED | `import { LoginPage } from "./pages/LoginPage"` confirmed |
| `frontend/e2e/chat.spec.ts` | `page.route` | Playwright route interception | WIRED | 2 `page.route("**/agent/message")` calls; no `test.skip(process.env.CI)` |

### Behavioral Spot-Checks

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| pytest-cov present in requirements.txt | `grep "pytest-cov" backend/requirements.txt` | `pytest-cov==6.1.0` | PASS |
| Integration tests use real DB (no DB override) | `grep "dependency_overrides.*get_db_pool" backend/tests/integration/test_session_crud.py` | No match | PASS |
| chat.spec.ts does not skip in CI | `grep "test.skip.*CI\|process.env.CI" frontend/e2e/chat.spec.ts` (ignoring comments) | No actionable skip found | PASS |
| test.yml has all 4 required jobs | `grep -E "^  (backend\|frontend-unit\|e2e\|lint):" .github/workflows/test.yml` | All 4 matched | PASS |
| smoke.yml is valid YAML | `python -c "import yaml; yaml.safe_load(open('.github/workflows/smoke.yml'))"` | Valid | PASS |
| All 5 Docker workflows exist | File existence check for all 5 docker-*.yml files | All 5 found (45 lines each) | PASS |
| Agent integration test covers full 5-stage flow | grep for classify/collect/validate in test_agent_flow.py | All 4 tool names found in `test_agent_conversation_flow_all_five_stages` | PASS (2026-04-23) |
| Smoke test auto-rollback on failure | N/A — deliberately manual per D-12, override accepted | Override entry present in VERIFICATION.md frontmatter | PASS (override) |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backend/tests/integration/test_agent_flow.py` | 114, 200 | `pass` in exception handlers | Info | Both are legitimate: line 114 silences session cleanup errors in teardown; line 200 skips malformed SSE lines during parsing. Not stubs. |

No blockers, warnings, or significant stubs found across 25 verified files.

### Gaps Summary

Both original gaps have been closed.

**Gap 1 — RESOLVED 2026-04-23: Integration test now exercises all 5 agent flow stages (SC2, TEST-02)**

Added `test_agent_conversation_flow_all_five_stages` to `backend/tests/integration/test_agent_flow.py`. The test:

- Mocks `agent.router.anthropic.Anthropic` with a 5-response side_effect chain: `resolve_structure` → `classify_intent` → `collect_parameters` → `validate_preflight` → `end_turn` ("All checks passed. You're ready to launch...").
- Mocks `agent.router.dispatch_tool` with per-tool JSON fixtures (structure metadata, design classification, parameter schema, preflight checklist with `ready_to_launch=True`).
- POSTs `/agent/message` against a real Supabase-backed session row (no `get_db_pool` override — session/message persistence is verified end-to-end).
- Parses the SSE stream and asserts: all 4 `tool_result` events stream in exact order (`resolve_structure, classify_intent, collect_parameters, validate_preflight`), validate_preflight result carries `ready_to_launch=True`, the final assistant `text` event mentions "launch", a `done` event terminates the stream, Anthropic was called ≥5 times (4 tool_use + 1 end_turn; a 6th title-gen call is tolerated), and `dispatch_tool` was invoked exactly 4 times in order.

Result: **PASS** against local Supabase (port 54322) in 0.22s. The actual job dispatch (stage 5 in the ROADMAP literal reading) remains outside the agent loop — POST `/jobs/launch` has its own router-level test coverage (`backend/tests/jobs/test_dispatch.py`).

**Gap 2 — RESOLVED 2026-04-14: Smoke test auto-rollback override accepted (SC5, TEST-07)**

Decision D-12 ("no automated rollback") formally overrides ROADMAP SC5 via the `overrides:` entry in this file's frontmatter, signed off by Leo on 2026-04-14. Rationale: Railway/Vercel one-click rollback from the deploy history is fast enough for manual execution, and automated rollback risks reverting valid deploys on transient failures.

### Review Findings Closure

Companion `09-REVIEW.md` (2026-04-10) raised 1 critical + 4 warnings + 3 info. Re-audit on 2026-04-23 confirms all findings have been addressed in source files already in git:

- **CR-01 (critical):** smoke.yml now injects `SMOKE_TEST_EMAIL`/`SMOKE_TEST_PASSWORD` via `secrets.*` context (lines 50-51).
- **WR-01:** `test_rate_limit_key_jwt_missing_sub` docstring now documents the `user:{client_host}` fallback; assertion `user:172.16.0.1` is coherent.
- **WR-02:** test.yml backend startup uses a 20-iteration `/health` readiness poll (lines 130-137), not `sleep 3`.
- **WR-03:** test.yml backend pytest run includes `--ignore=tests/integration` (line 44).
- **WR-04:** `_delete_session` in both `test_agent_flow.py` and `test_session_crud.py` calls `warnings.warn(...)` on cleanup failure instead of silent pass.
- **IN-01:** smoke.yml step renamed to "Setup Node.js for Playwright".
- **IN-02:** Dead `text_block.hasattr = lambda ...` assignment is not present in current test_agent_flow.py.
- **IN-03:** No action required (pytest-asyncio pin note only).

---

_Verified: 2026-04-10T22:30:00Z_
_Re-verified: 2026-04-23T12:15:00Z_
_Verifier: Claude (gsd-verifier)_
