---
phase: 09-testing-ci-cd
plan: 02
subsystem: testing
tags: [vitest, jsdom, testing-library, react, typescript, unit-tests, smoke-tests]

requires:
  - phase: 06-ui-improvements
    provides: SettingsPage, ChatPage, JobPage components that are smoke-tested here

provides:
  - Vitest configured with jsdom and jest-dom setup file
  - npm test/test:watch/test:coverage scripts
  - Unit tests for api(), cn(), and relativeDate() functions
  - Smoke render tests for Login, SettingsPage, JobPage, ChatPage
  - format.ts utility module (relativeDate) ported from main branch

affects: [09-03, 09-04, ci-pipeline]

tech-stack:
  added: []
  patterns:
    - "vi.mock() for module-level mocking of API clients and context providers"
    - "MemoryRouter + Routes wrapping for pages that use useParams/useNavigate"
    - "Element.prototype.scrollIntoView stub in setup.ts for jsdom compatibility"
    - "vi.useFakeTimers() + vi.setSystemTime() for deterministic time-based tests"

key-files:
  created:
    - frontend/src/test/setup.ts
    - frontend/src/lib/format.ts
    - frontend/src/lib/utils.test.ts
    - frontend/src/lib/format.test.ts
    - frontend/src/pages/Login.test.tsx
    - frontend/src/pages/SettingsPage.test.tsx
    - frontend/src/pages/JobPage.test.tsx
    - frontend/src/components/chat/ChatPage.test.tsx
  modified:
    - frontend/vite.config.ts
    - frontend/package.json
    - frontend/src/lib/api.test.ts
    - frontend/src/test/setup.ts

key-decisions:
  - "Element.prototype.scrollIntoView stubbed globally in setup.ts — jsdom does not implement DOM scroll APIs; MessageList uses scrollIntoView in a useEffect"
  - "ChatPage mocked at module boundary (useLayoutContext, sessions, agent, jobs) rather than rendering AuthenticatedLayout — smoke test verifies component tree renders, not integration wiring"
  - "format.ts created in worktree to match main branch — the file was present in main repo commits but missing from the worktree base; created to satisfy plan's format.test.ts requirement"

patterns-established:
  - "Smoke test pattern: render in MemoryRouter, assert container truthy + key structural elements present"
  - "API mock pattern: vi.mock('@/lib/user', () => ({ getSettings: vi.fn().mockResolvedValue(...) })) — all functions mocked to prevent real fetch in jsdom"
  - "Fake timer pattern for time-relative tests: vi.useFakeTimers() + vi.setSystemTime(fixedDate) in each test, restored via afterEach vi.useRealTimers()"

requirements-completed: [TEST-03]

duration: 18min
completed: 2026-04-10
---

# Phase 9 Plan 02: Frontend Vitest Infrastructure and Smoke Tests Summary

**Vitest configured with jsdom and jest-dom; 36 tests across 7 files covering api(), cn(), relativeDate(), and render smoke tests for Login, SettingsPage, JobPage, and ChatPage.**

## Performance

- **Duration:** 18 min
- **Completed:** 2026-04-10
- **Tasks:** 2/2
- **Files modified:** 12

## Accomplishments

### Task 1: Configure Vitest infrastructure and write utility/API tests

- Updated `vite.config.ts`: `setupFiles: ["./src/test/setup.ts"]`
- Added `test`, `test:watch`, `test:coverage` scripts to `package.json`
- Created `src/test/setup.ts` with `@testing-library/jest-dom/vitest` import and `scrollIntoView` stub
- Created `src/lib/format.ts` with `relativeDate()` utility (deviation — file missing from worktree base)
- Extended `api.test.ts`: 8 tests covering `ApiError` class and `api()` function (fetch mocking, 401 retry loop, error propagation, Content-Type header logic)
- Created `utils.test.ts`: 6 tests for `cn()` — tailwind-merge deduplication, conditional classes, object syntax
- Created `format.test.ts`: 5 tests for `relativeDate()` with fake timers — just now, minutes, hours, days, locale fallback

### Task 2: Create page component smoke tests

- `Login.test.tsx`: 5 tests — renders, email input, password input, submit button, signup link
- `SettingsPage.test.tsx`: 5 tests — renders, Settings heading, Account/Billing/Usage tabs; mocked `@/lib/user` module
- `JobPage.test.tsx`: 2 tests — renders, shows loading state; mocked `@/lib/jobs` module
- `ChatPage.test.tsx`: 3 tests — renders, textarea present, context panel text; mocked `@/lib/sessions`, `@/lib/agent`, `@/lib/jobs`, `@/components/layout/AuthenticatedLayout`

**Final result: 36/36 tests pass, 7 test files.**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing file] Created format.ts in worktree**
- **Found during:** Task 1
- **Issue:** `frontend/src/lib/format.ts` referenced in the plan did not exist in the worktree base commit; it was present in the main branch but not in the worktree's `f1a99f2` base
- **Fix:** Created `format.ts` with `relativeDate()` matching the main branch implementation
- **Files modified:** `frontend/src/lib/format.ts`
- **Commit:** f04b5fc

**2. [Rule 1 - Bug] Stubbed scrollIntoView in setup.ts**
- **Found during:** Task 2 (ChatPage smoke tests)
- **Issue:** `MessageList.tsx` calls `element.scrollIntoView()` in a `useEffect` — jsdom does not implement this DOM API, causing `TypeError: bottomRef.current?.scrollIntoView is not a function`
- **Fix:** Added `Element.prototype.scrollIntoView = () => {}` to `src/test/setup.ts` global setup
- **Files modified:** `frontend/src/test/setup.ts`
- **Commit:** cb58bed

## Known Stubs

None — all tests assert real component behavior; no stubs that affect plan goal.

## Threat Flags

None — test files introduce no network endpoints, auth paths, or schema changes. Threat model note (T-09-03) honored: no real API URLs, tokens, or credentials in test files; fetch is mocked via `vi.stubGlobal("fetch", mockFetch)`.

## Self-Check

Files created:
- frontend/src/test/setup.ts — FOUND
- frontend/src/lib/format.ts — FOUND
- frontend/src/lib/utils.test.ts — FOUND
- frontend/src/lib/format.test.ts — FOUND
- frontend/src/pages/Login.test.tsx — FOUND
- frontend/src/pages/SettingsPage.test.tsx — FOUND
- frontend/src/pages/JobPage.test.tsx — FOUND
- frontend/src/components/chat/ChatPage.test.tsx — FOUND

Commits:
- f04b5fc — FOUND (Task 1)
- cb58bed — FOUND (Task 2)

## Self-Check: PASSED
