---
phase: 10-legal-and-compliance
plan: 03
subsystem: frontend
tags: [cookie-consent, banner, localStorage, gdpr, ccpa, react-router, tailwind]

dependency_graph:
  requires:
    - phase: 10-legal-and-compliance
      provides: [legal-content-v1, version-string-canonical]
  provides:
    - cookie-consent-banner
    - cookie-consent-event
    - cookie-consent-record-v1
  affects: [app-shell, footer, legal-cookies-page, 10-06-legal-routes-and-footer]

tech-stack:
  added: []
  patterns:
    - versioned-localStorage-key
    - custom-event-provider-wakeup
    - dismissible-fixed-bottom-banner

key-files:
  created:
    - frontend/src/lib/cookieConsent.ts
    - frontend/src/components/legal/CookieConsentBanner.tsx
    - frontend/src/components/legal/CookieConsentProvider.tsx
    - frontend/src/components/legal/CookieConsentBanner.test.tsx
  modified:
    - frontend/src/App.tsx
    - frontend/src/main.tsx

key-decisions:
  - "Store consent as versioned JSON (version: 'v1') so a future schema change can invalidate old records by bumping the version string; readConsent() returns null on mismatch and the banner re-appears."
  - "Provider mounted INSIDE BrowserRouter (not inside main.tsx) so the banner's 'Learn more' Link has router context; main.tsx only gets a locator comment pointing readers to App.tsx."
  - "Single 'Got it' acknowledgement rather than granular opt-in/opt-out: Kendrew sets no analytics or tracking cookies, so there is nothing to opt into beyond the strictly-necessary auth cookies."
  - "Used fireEvent from @testing-library/react instead of @testing-library/user-event to avoid adding an unused dependency — the test only exercises a single click."

patterns-established:
  - "localStorage versioning: key 'kendrew.cookie_consent.v1' carries version inside JSON as well so both key rename and schema bump are options."
  - "Provider re-open via window CustomEvent: 'kendrew:open-cookie-consent' allows any descendant (e.g. future footer link in Plan 10-06) to re-open the banner without prop drilling or context."
  - "Cookie-banner disclosure text must name every cookie actually set — a grep for 'access_token|refresh_token|csrftoken' in the banner source is part of the acceptance criteria."

requirements-completed: []

# Metrics
duration: 12min
completed: 2026-04-23
---

# Phase 10 Plan 3: Cookie Consent Banner Summary

**First-visit dismissible cookie banner disclosing the three strictly-necessary auth cookies, persisted in `kendrew.cookie_consent.v1` localStorage and re-openable from anywhere via a window CustomEvent.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-04-23T14:37:00Z
- **Completed:** 2026-04-23T14:49:00Z
- **Tasks:** 2 auto + 1 auto-approved checkpoint
- **Files modified:** 6 (4 created, 2 modified)

## Accomplishments

- `readConsent` / `writeConsent` / `requestOpenConsent` helper module with a version-gated JSON schema and defensive error handling (invalid JSON, unknown schema version, SSR-safe try/catch).
- `CookieConsentBanner` fixed-bottom banner named as `role="region"` (`aria-label="Cookie notice"`) listing `access_token`, `refresh_token`, `csrftoken` with a react-router `Link` to `/legal/cookies`.
- `CookieConsentProvider` with event-driven re-open: any component can dispatch `kendrew:open-cookie-consent` on `window` to re-show the banner for users who already accepted.
- Provider mounted inside `<BrowserRouter>` in `App.tsx` so the banner renders on every public, authenticated, and admin route.
- 10 vitest cases covering helper behavior (read/write/invalid-JSON/wrong-version), banner render gating, required string disclosures, the Link target, Got-it persist-and-hide, and event-driven re-render after prior acceptance. All passing.

## Task Commits

1. **Task 1 (TDD): cookieConsent helper + CookieConsentProvider + Banner + test** — `065c721` (feat)
2. **Task 2: Mount CookieConsentProvider in App.tsx, annotate main.tsx** — `966d955` (feat)
3. **Task 3: human-verify checkpoint** — auto-approved (plan executed as autonomous per spawn directive; programmatic equivalents of every verification step confirmed below)

_Note: Task 1 was TDD-authored but committed as a single feat commit because the RED test file, helper, provider, and banner form one coherent unit; the intermediate RED run was verified via `npx vitest run src/components/legal` (0 tests before source, 10 pass after) before committing._

## Files Created/Modified

- `frontend/src/lib/cookieConsent.ts` (created) — helper module: `COOKIE_CONSENT_KEY`, `COOKIE_CONSENT_EVENT`, `CookieConsentRecord`, `readConsent`, `writeConsent`, `requestOpenConsent`.
- `frontend/src/components/legal/CookieConsentBanner.tsx` (created) — presentational banner; accepts `onAccept` prop; references shadcn `Button` at `size="sm"`.
- `frontend/src/components/legal/CookieConsentProvider.tsx` (created) — state container; reads initial visibility from `readConsent()`; registers `window` listener for `COOKIE_CONSENT_EVENT`.
- `frontend/src/components/legal/CookieConsentBanner.test.tsx` (created) — 10 vitest cases, jsdom environment, MemoryRouter wrapper.
- `frontend/src/App.tsx` (modified) — added import of `CookieConsentProvider`, wrapped the full `<Routes>` tree inside it (inside `<BrowserRouter>`).
- `frontend/src/main.tsx` (modified) — added one-line locator comment pointing to `App.tsx` for the banner mount.

## Decisions Made

1. **Versioned JSON over raw timestamp.** Stored `{version, accepted_at, cookies_version}` rather than a bare ISO string so future cookie-policy changes can invalidate old acceptances by bumping `cookies_version`. `readConsent` currently only enforces `version === "v1"`, but a compare against the current `COOKIES_VERSION` can be added as a one-line policy change when the first material cookie update ships.
2. **Provider inside BrowserRouter.** The banner's "Learn more" uses `<Link>` which requires router context. Mounting at `App.tsx` (inside the existing `<BrowserRouter>`) rather than wrapping App in `main.tsx` was the correct placement and matches the plan's Interface context.
3. **fireEvent instead of user-event.** The test suite only needs a single click, and `@testing-library/user-event` is not in `package.json`. Using `fireEvent` from `@testing-library/react` avoids adding a dependency the project does not otherwise use.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test file imported `@testing-library/user-event` which is not installed**
- **Found during:** Task 1 (RED test authoring)
- **Issue:** The plan's behavior spec did not dictate a click library, and my initial test draft used `userEvent.setup()` / `user.click()`. `@testing-library/user-event` is not in `frontend/package.json`; the test would have failed at import time rather than at the assertion.
- **Fix:** Replaced `userEvent` with `fireEvent` (already available in `@testing-library/react`). One call site, one-line diff.
- **Files modified:** `frontend/src/components/legal/CookieConsentBanner.test.tsx`
- **Verification:** `npx vitest run src/components/legal` — 10/10 pass.
- **Committed in:** `065c721` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Avoided adding a dev dependency for a single click assertion. No functional difference.

## Issues Encountered

- None — Task 1 passed RED → GREEN cleanly once the user-event import was removed; Task 2 required careful indentation in `App.tsx` when wrapping the existing `<div>` + `<Routes>` subtree, but the final tsc run passed without errors.

## Verification evidence

Programmatic equivalents for every step of the Task 3 human-verify checkpoint:

- **"Banner appears at the bottom with 'Got it' and the three cookie names"** — covered by the "renders the banner when no prior consent is stored" + "discloses the three strictly-necessary cookies by name" tests.
- **"Learn more loads /legal/cookies"** — covered by the "contains a Learn more link to /legal/cookies" test asserting `href === "/legal/cookies"`.
- **"Got it persists consent, reload does not show banner"** — covered by the "persists consent and hides the banner" test + the "does not render when a valid consent record is already present" test (which simulates a reload via a fresh render).
- **"localStorage record contains accepted_at and cookies_version: '2026-04-23'"** — covered by the "writeConsent stores a v1 record under the canonical key" test asserting both fields on the parsed blob.
- **"Dispatching kendrew:open-cookie-consent re-opens the banner"** — covered by the "re-renders the banner when the open-consent event is dispatched, even after prior acceptance" test.
- **"Clear localStorage key + refresh → banner reappears"** — covered by the two no-prior-consent render tests after `beforeEach(() => localStorage.clear())`.

All acceptance criteria from both tasks hold:
- `grep -q "kendrew.cookie_consent.v1" src/lib/cookieConsent.ts` — OK
- `grep -q "kendrew:open-cookie-consent" src/lib/cookieConsent.ts` — OK
- `grep -q "access_token|refresh_token|csrftoken|/legal/cookies"` in banner — 6 matches
- `grep -q "CookieConsentProvider" src/App.tsx` — OK
- `grep -q "Cookie consent" src/main.tsx` — OK
- `npx tsc --noEmit` — exit 0
- `npx vitest run src/components/legal` — 10 pass / 10 total, exit 0

## User Setup Required

None — pure frontend change, no env vars, no external service config.

## Next Phase Readiness

- **Plan 10-06 (legal routes + footer)** can now add a "Cookie preferences" link in the footer that calls `requestOpenConsent()` from `@/lib/cookieConsent` to re-open the banner. The link wiring is trivial; no additional plumbing is required on this side.
- **Plan 10-02 (ToS acceptance on signup)** is independent and does not touch the banner.
- No blockers or concerns for downstream plans.

## Self-Check: PASSED

- `frontend/src/lib/cookieConsent.ts` — FOUND
- `frontend/src/components/legal/CookieConsentBanner.tsx` — FOUND
- `frontend/src/components/legal/CookieConsentProvider.tsx` — FOUND
- `frontend/src/components/legal/CookieConsentBanner.test.tsx` — FOUND
- Commit `065c721` — FOUND
- Commit `966d955` — FOUND

---
*Phase: 10-legal-and-compliance*
*Completed: 2026-04-23*
