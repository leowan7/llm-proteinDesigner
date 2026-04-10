---
phase: 09-testing-ci-cd
plan: "03"
subsystem: frontend-e2e
tags: [playwright, e2e, testing, ci]
dependency_graph:
  requires: []
  provides: [frontend/playwright.config.ts, frontend/e2e/]
  affects: [frontend/package.json]
tech_stack:
  added: ["@playwright/test ^1.59.1", "Chromium browser (Playwright)"]
  patterns: ["Page Object Model", "page.route() SSE mocking", "test.beforeEach login"]
key_files:
  created:
    - frontend/playwright.config.ts
    - frontend/e2e/pages/LoginPage.ts
    - frontend/e2e/pages/ChatPage.ts
    - frontend/e2e/pages/JobPage.ts
    - frontend/e2e/pages/SettingsPage.ts
    - frontend/e2e/auth.spec.ts
    - frontend/e2e/chat.spec.ts
    - frontend/e2e/jobs.spec.ts
    - frontend/e2e/settings.spec.ts
  modified:
    - frontend/package.json
decisions:
  - "page.route() intercepts /agent/message SSE endpoint — chat tests always run in CI without Anthropic API key"
  - "LoginPage.goto() expects /chat redirect (not /) matching Login.tsx navigate('/chat') behavior"
  - "Settings tabs use role=tab / aria-selected selectors — shadcn Tabs outputs standard ARIA attributes"
  - "Jobs test gracefully handles empty state — no seed jobs guaranteed in CI"
metrics:
  duration: "12 min"
  completed: "2026-04-10"
  tasks_completed: 2
  files_changed: 9
---

# Phase 9 Plan 3: Playwright E2E Testing Infrastructure Summary

Playwright E2E infrastructure with Page Object Model and 4 spec files covering all core user flows. Chat tests always run in CI using `page.route()` to mock the agent SSE endpoint.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create Playwright config, page objects, and npm script | ce7d73b | playwright.config.ts, 4 page objects, package.json |
| 2 | Create E2E spec files for all 4 core flows | d4a3bba | auth.spec.ts, chat.spec.ts, jobs.spec.ts, settings.spec.ts |

## What Was Built

### Playwright Configuration (`frontend/playwright.config.ts`)
- Single chromium project — limits resource usage in CI (T-09-05)
- `baseURL: http://localhost:5173` — matches Vite dev server default
- `webServer` block spins up Vite dev server automatically, reuses existing server outside CI
- `retries: 2` in CI, `0` locally
- `testDir: ./e2e` — all spec files in `frontend/e2e/`

### Page Objects (`frontend/e2e/pages/`)
Four page object classes encapsulate DOM selectors to isolate spec files from UI changes:

- **LoginPage** — `goto()`, `login(email, password)`, `getErrorMessage()`. Uses `input[name="email"]` and `input[name="password"]` matching react-hook-form's HTML name attribute on shadcn Input.
- **ChatPage** — `goto()`, `sendMessage(text)`, `waitForAgentResponse()`, `getLastMessage()`. Targets the `textarea` and `button[aria-label="Send message"]` from ChatInput.tsx.
- **JobPage** — `goto(jobId)`, `getStatus()`, `waitForStatus(status)`. Targets `[role="status"]` badge from JobStatusCard.
- **SettingsPage** — `goto()`, `getActiveTab()`, `clickTab(name)`. Uses `[role="tab"]` and `aria-selected="true"` from shadcn Tabs (standard ARIA).

### E2E Spec Files

**auth.spec.ts** — 4 tests:
1. Valid credentials redirect to `/chat`
2. Invalid credentials show error message (`.text-destructive`)
3. Unauthenticated user redirected to `/login`
4. Session persists across page reload

**chat.spec.ts** — 2 tests with CI-compatible agent mocking:
- `page.route("**/agent/message")` intercepts the SSE endpoint and returns a deterministic 5-event body
- Tests verify text rendering ("I found the structure...") and second text block appear in the DOM
- `test.slow()` applied for generous SSE processing timeout
- No `test.skip(process.env.CI)` — always runs in CI per D-08

**jobs.spec.ts** — 2 tests:
1. Job history page loads (table or empty state — both valid)
2. Job detail page loads from history click, or shows error on nonexistent ID

**settings.spec.ts** — 3 tests:
1. Settings page renders 4 tabs (Account, Billing, Usage, Notifications)
2. Tab switching updates `aria-selected="true"`
3. Billing tab shows payment method content, "Manage payment method" button, or graceful error

### npm Scripts Added
- `"test:e2e": "npx playwright test"` — run all E2E tests
- `"test:e2e:ui": "npx playwright test --ui"` — interactive Playwright UI mode

## Verification

```
npx playwright test --list
```
Output: 11 tests across 4 files (auth: 4, chat: 2, jobs: 2, settings: 3).

## Deviations from Plan

### Auto-adjusted selectors

**1. [Rule 1 - Bug] Login redirect target is /chat, not /**
- **Found during:** Task 1 (reading Login.tsx)
- **Issue:** Plan assumed post-login redirect to `/`. Login.tsx calls `navigate("/chat")`.
- **Fix:** LoginPage.login() waits for `URL /\/(chat|$)/` to handle both `/chat` and potential `/` fallback.
- **Files modified:** frontend/e2e/pages/LoginPage.ts, frontend/e2e/auth.spec.ts

**2. [Rule 2 - Missing] Error selector adjusted to match actual DOM**
- **Found during:** Task 1 (reading Login.tsx)
- **Issue:** Plan specified `[role="alert"]` but Login.tsx renders errors as `<p class="text-destructive">` (no role attribute).
- **Fix:** auth.spec.ts targets `.text-destructive` instead of `[role="alert"]`.
- **Files modified:** frontend/e2e/auth.spec.ts

## Known Stubs

None — all E2E test selectors target real DOM elements from existing components. Page objects use structural selectors that match the actual rendered output.

## Threat Flags

None — test files introduce no new network endpoints, auth paths, or schema changes. Test credentials follow T-09-04 mitigation: only `test@example.com` seed account is referenced.

## Self-Check: PASSED

Files exist:
- frontend/playwright.config.ts: FOUND
- frontend/e2e/pages/LoginPage.ts: FOUND
- frontend/e2e/pages/ChatPage.ts: FOUND
- frontend/e2e/pages/JobPage.ts: FOUND
- frontend/e2e/pages/SettingsPage.ts: FOUND
- frontend/e2e/auth.spec.ts: FOUND
- frontend/e2e/chat.spec.ts: FOUND
- frontend/e2e/jobs.spec.ts: FOUND
- frontend/e2e/settings.spec.ts: FOUND

Commits exist:
- ce7d73b: FOUND
- d4a3bba: FOUND
