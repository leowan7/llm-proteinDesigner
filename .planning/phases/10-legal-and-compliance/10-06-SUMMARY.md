---
phase: 10-legal-and-compliance
plan: 06
subsystem: frontend
tags: [routes, footer, navigation, legal-pages, deep-link, react-router, vitest]

dependency_graph:
  requires:
    - phase: 10-legal-and-compliance
      provides: [legal-content-v1, cookie-consent-banner]
  provides:
    - legal-routes
    - app-footer
    - settings-tab-deep-link
    - privacy-tab-scaffold
  affects: [App-router, AuthenticatedLayout, AuthLayout, SettingsPage, plan-10-04-settings-page]

tech-stack:
  added: []
  patterns:
    - persistent-footer-on-both-layouts
    - url-search-param-deep-link-with-whitelist-fallback
    - aria-selected-tabs-assertion

key-files:
  created:
    - frontend/src/components/layout/AppFooter.tsx
    - frontend/src/components/layout/AppFooter.test.tsx
  modified:
    - frontend/src/App.tsx
    - frontend/src/components/layout/AuthenticatedLayout.tsx
    - frontend/src/components/auth/AuthLayout.tsx
    - frontend/src/pages/SettingsPage.tsx
    - frontend/src/pages/SettingsPage.test.tsx

key-decisions:
  - "Scaffolded a Privacy TabsTrigger + placeholder TabsContent in SettingsPage ahead of Plan 10-04 so ?tab=privacy deep-links from the cancel-deletion email (10-04 Task 3) resolve to a visible tab today rather than silently no-op'ing."
  - "Switched the deep-link test assertion from data-state='active'/'inactive' to aria-selected='true'/'false' because the Tabs primitive is base-ui, not Radix."
  - "AuthLayout restructured into a column flex so the card stays centered while the footer sits at the bottom of the viewport on public auth screens."
  - "Inner overflow in AuthenticatedLayout switched from overflow-hidden to overflow-auto so content scrolls while <AppFooter/> remains pinned at the bottom of <main>."

patterns-established:
  - "Public legal routes live above the AuthenticatedLayout Route block inside <Routes>, so they are reachable without authentication."
  - "Deep-link whitelist pattern: VALID_SETTINGS_TABS const + (list as readonly string[]).includes(param ?? '') fallback — shared template for any future URL-driven tab UI."
  - "Vitest assertion on base-ui Tabs uses aria-selected; do not assert data-state (Radix-only)."

requirements-completed: []

# Metrics
duration: 7min
completed: 2026-04-23
---

# Phase 10 Plan 6: Legal Routes + Persistent Footer Summary

**Four public `/legal/*` routes, an always-visible `AppFooter` on both the authenticated and public auth layouts with a working Cookie-preferences re-open trigger, and `/settings?tab=<name>` deep-link support that hardens the cancel-deletion email CTA from Plan 10-04.**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-04-23T11:38:57Z (first commit after 10-02 SUMMARY)
- **Completed:** 2026-04-23T11:45:02Z
- **Tasks:** 3 auto + 1 auto-approved checkpoint
- **Files modified:** 7 (2 created, 5 modified)

## Accomplishments

- `AppFooter` component: 4 `<Link>`s to Terms, Privacy, Subprocessors, Cookies; "Cookie preferences" button wired to `requestOpenConsent()`; dynamic `© {currentYear} Ranomics Inc.`; `<footer role="contentinfo">` for a11y.
- Public Routes `path="/legal/{terms,privacy,subprocessors,cookies}"` mounted inside `<Routes>` in `App.tsx`, above the `AuthenticatedLayout` block (so logged-out visitors can reach them) and below `CookieConsentProvider` (so the banner still renders on legal pages).
- `AppFooter` rendered inside `AuthenticatedLayout.tsx` (pinned at the bottom of `<main>`, scroll region switched to `overflow-auto`) and inside `AuthLayout.tsx` (column flex on the auth shell so it sits below the centered auth card).
- `SettingsPage` reads `?tab=<name>` via `useSearchParams`; whitelist = `[account, billing, privacy, usage, notifications]`; invalid/missing values fall back to `account`. `Tabs defaultValue={initialTab}` preserves user-driven tab changes after the initial load.
- Privacy `TabsTrigger` + placeholder `TabsContent` scaffolded so `?tab=privacy` activates a visible tab today (Plan 10-04 will replace the placeholder with Export + Delete controls).
- 7 new Vitest cases across two files (4 AppFooter + 3 deep-link) all passing; 19 pre-existing tests (SettingsPage smoke + CookieConsent) still green.

## Task Commits

1. **Task 1 (TDD): `AppFooter` component + test** — `b217db5` (`feat`)
2. **Task 2: Mount legal routes + footer in both layouts + URL deep-link** — `4255bd9` (`feat`)
3. **Task 3: Vitest coverage for `/settings?tab=privacy` deep-link** — `92775f7` (`test`)

_Note: Task 1 followed TDD (RED: test file referencing missing `./AppFooter` → import error with 0 tests; GREEN: 4/4 pass). RED and GREEN were committed together as one `feat` commit since the component + test form a single coherent unit and the intermediate RED run was verified before staging._

## Files Created/Modified

- `frontend/src/components/layout/AppFooter.tsx` **(created)** — persistent site footer.
- `frontend/src/components/layout/AppFooter.test.tsx` **(created)** — 4 Vitest cases (link hrefs, Cookie-preferences click, current-year copyright, `role="contentinfo"`).
- `frontend/src/App.tsx` **(modified)** — imports Terms/Privacy/Subprocessors/Cookies from `pages/legal/*`; adds 4 public `<Route>` entries above the `AuthenticatedLayout` block. `CookieConsentProvider` wrap from Plan 10-03 preserved.
- `frontend/src/components/layout/AuthenticatedLayout.tsx` **(modified)** — imports `AppFooter`; renders it inside `<main>` below the `<Outlet>`; switched inner scroll region from `overflow-hidden` to `overflow-auto`.
- `frontend/src/components/auth/AuthLayout.tsx` **(modified)** — imports `AppFooter`; wraps centered card in a column flex container; renders `<AppFooter />` at the bottom.
- `frontend/src/pages/SettingsPage.tsx` **(modified)** — adds `useSearchParams` import; adds `VALID_SETTINGS_TABS` const; reads `?tab=` param with whitelist fallback; `<Tabs defaultValue={initialTab}>`; adds `Privacy` TabsTrigger + placeholder TabsContent as a Plan 10-04 seam.
- `frontend/src/pages/SettingsPage.test.tsx` **(modified)** — new `describe("SettingsPage deep-link ?tab=")` block with 3 cases asserting `aria-selected` on the correct tab for `?tab=privacy`, no param, and `?tab=bogus`.

## Decisions Made

1. **Privacy tab scaffold lives in 10-06, content in 10-04.** Plan 10-06 and 10-04 both land in wave 2; 10-04 hadn't landed when 10-06 started. The plan's `validTabs` array explicitly includes `privacy`, and Task 3's test asserts `getByRole("tab", { name: /privacy/i })` is active. Adding only the URL-param reader without the tab itself would have left the test asserting against a missing element. Adding an empty Privacy TabsContent placeholder is the minimal seam that lets the deep-link resolve today and keeps 10-04's scope untouched (10-04 replaces the placeholder body only, not the trigger wiring).
2. **`aria-selected` over `data-state`.** The Kendrew Tabs primitive is base-ui (`@base-ui/react/tabs`), which exposes `data-active` (boolean-presence) and `aria-selected="true"/"false"`. It does NOT emit Radix's `data-state="active"/"inactive"`. `aria-selected` is the portable accessibility attribute so the test reads as a user-intent assertion rather than a library-specific one.
3. **`defaultValue={initialTab}` not `value={initialTab}`.** Using `defaultValue` lets the URL seed the initial tab without overriding subsequent user clicks — matching the plan's directive that in-session tab changes stay intact.
4. **AuthLayout column restructure.** Previously `AuthLayout` was a single flex container centering the card with `min-h-screen`. To pin a footer to the bottom while keeping the card vertically centered, the outer became `flex min-h-screen flex-col` with `flex-1` on the card wrapper and `<AppFooter />` as the last child.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Task 3 test required a Privacy tab that did not exist**
- **Found during:** Task 2 (preparatory read of `SettingsPage.tsx`)
- **Issue:** Plan 10-06 Task 3's vitest spec asserts `screen.getByRole("tab", { name: /privacy/i })` is selected when the URL is `/settings?tab=privacy`. Before this plan, `SettingsPage` only had `account / billing / usage / notifications` tabs. Plan 10-04 is responsible for the Privacy tab's UI, but 10-04 had not landed yet (wave 2 parallel), so Task 3's test would have failed on a missing element. The plan's own `validTabs` const lists `privacy`, so the URL contract is 10-06's responsibility even if the content isn't.
- **Fix:** Added a `Privacy` `TabsTrigger` + empty `TabsContent` placeholder inside `SettingsPage.tsx`. The placeholder body reads "Privacy controls are being rolled out…" and is explicitly annotated in a comment as the Plan 10-04 seam. Deep-link Task 3 assertions now pass against the real DOM.
- **Files modified:** `frontend/src/pages/SettingsPage.tsx`
- **Verification:** `npx vitest run src/pages/SettingsPage.test.tsx` — 8/8 pass (5 prior smoke + 3 new deep-link).
- **Committed in:** `4255bd9` (Task 2 commit)

**2. [Rule 1 - Bug] Initial deep-link test used Radix-only `data-state` attribute**
- **Found during:** Task 3 (first test run)
- **Issue:** The plan's Task 3 spec suggests asserting `data-state="active"`, which is a Radix Tabs convention. The project uses base-ui Tabs, which emits `data-active` (boolean-presence) + `aria-selected`, NOT `data-state`. 3/3 deep-link tests failed with `Received: null`.
- **Fix:** Switched assertions to `toHaveAttribute("aria-selected", "true" | "false")`. Same user-facing guarantee (selected tab is the correct one), but portable across Tabs primitives.
- **Files modified:** `frontend/src/pages/SettingsPage.test.tsx`
- **Verification:** `npx vitest run src/pages/SettingsPage.test.tsx` — 8/8 pass.
- **Committed in:** `92775f7` (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking scaffold, 1 bug in the plan's test hint)
**Impact on plan:** No scope creep. The Privacy TabsTrigger scaffold is the minimum needed to make Task 3's test assertion valid; 10-04 will replace the placeholder body only. The `aria-selected` switch keeps the test's intent identical while correcting for the Tabs primitive in this codebase.

## Issues Encountered

- None beyond the two auto-fixed deviations above. `tsc --noEmit` passed cleanly after each commit; the full 22-test regression (AppFooter 4 + SettingsPage 8 + CookieConsent 10) was green at every stage.

## Known Stubs

- `frontend/src/pages/SettingsPage.tsx` — Privacy `TabsContent` body is a placeholder: `"Privacy controls are being rolled out. Data export and account deletion will appear here soon."`. This is an intentional seam for Plan 10-04, which replaces the placeholder with Export Data + Delete Account controls. The plan 10-06 goal (deep-link resolution + footer + public legal routes) is fully achieved; the stub affects only the body of a tab that 10-04 owns.

## Verification evidence

Programmatic equivalents for every step of the Task 4 human-verify checkpoint (auto-approved per autonomous spawn directive):

- **Step 2 — /legal/* URLs render without auth** — `tsc --noEmit` resolves the imports of `Terms`, `Privacy`, `Subprocessors`, `Cookies` from `pages/legal/*` into `App.tsx`, and `grep -q 'path="/legal/{name}"' src/App.tsx` passes for all four names. The four Route entries sit above the `AuthenticatedLayout` Route block, so no auth guard intercepts them.
- **Step 3 — footer visible at the bottom of /login** — AppFooter grep in `AuthLayout.tsx` passes; `AuthLayout.tsx` now uses `flex min-h-screen flex-col` with `<AppFooter />` as the last child below the centered card flex row.
- **Step 4 — footer Terms link loads /legal/terms** — `AppFooter.test.tsx` "renders the four /legal/* links with correct hrefs" asserts `href === "/legal/terms"` on the Terms Link.
- **Step 5 — Cookie preferences re-opens the banner** — `AppFooter.test.tsx` "clicking 'Cookie preferences' calls requestOpenConsent" asserts the mocked function is called exactly once. `requestOpenConsent` itself is already covered by Plan 10-03's banner tests (`re-renders the banner when the open-consent event is dispatched`).
- **Step 6 — footer visible in authenticated layout without hiding sidebar** — AppFooter grep in `AuthenticatedLayout.tsx` passes; footer is a sibling of `<div className="flex-1 overflow-auto">` inside the same `<main>`, so it flows at the bottom of the content column while the sidebar is a sibling of `<main>` inside the surrounding `SidebarProvider`.
- **Step 7 — /settings?tab=privacy activates Privacy tab on fresh load** — `SettingsPage.test.tsx` "activates the Privacy tab when rendered at /settings?tab=privacy" asserts `aria-selected="true"` on the Privacy TabsTrigger.
- **Step 8 — /settings activates Account tab** — covered by "activates the Account tab (default) when rendered at /settings with no query param".
- **Step 9 — /settings?tab=bogus falls back to Account** — covered by "falls back to the Account tab when ?tab=<invalid> is supplied"; also asserts `aria-selected="false"` on Privacy, confirming the fallback is actually filtering the param rather than silently accepting anything.

All acceptance criteria across the 3 auto tasks hold:

- `grep -q "/legal/terms|/legal/privacy|/legal/subprocessors|/legal/cookies|Cookie preferences|requestOpenConsent" src/components/layout/AppFooter.tsx` — 6/6 hits
- `grep -q 'path="/legal/*"' src/App.tsx` — 4/4 paths present
- `grep -q "AppFooter" src/components/layout/AuthenticatedLayout.tsx` — hit
- `grep -q "AppFooter" src/components/auth/AuthLayout.tsx` — hit
- `grep -q "useSearchParams" src/pages/SettingsPage.tsx` — hit
- `grep -q "defaultValue={initialTab}" src/pages/SettingsPage.tsx` — hit
- `grep -q 'tab=privacy' src/pages/SettingsPage.test.tsx` — hit
- `npx tsc --noEmit` — exit 0
- `npx vitest run src/components/layout/AppFooter.test.tsx src/pages/SettingsPage.test.tsx src/components/legal/CookieConsentBanner.test.tsx` — 22/22 pass

## User Setup Required

None — pure frontend change, no env vars, no migrations, no external service configuration.

## Next Phase Readiness

- **Plan 10-04 (GDPR export + deletion endpoints + Privacy tab UI)** can now replace the Privacy `TabsContent` placeholder body in `SettingsPage.tsx` with the Export Data + Delete Account controls. The TabsTrigger + TabsContent wrapper, URL-param deep-link, and whitelist validation are already in place. 10-04's cancel-deletion email link (`/settings?tab=privacy`) is live and covered by Vitest.
- **Plan 10-05 (retention cron + warning email)** is unaffected; footer/routes wiring has no backend coupling.
- **Counsel review** is the only remaining blocker on removing the "Draft — legal review pending" banner (tracked post-launch).

## Self-Check: PASSED

- `frontend/src/components/layout/AppFooter.tsx` — FOUND
- `frontend/src/components/layout/AppFooter.test.tsx` — FOUND
- Commit `b217db5` (AppFooter Task 1) — FOUND
- Commit `4255bd9` (routes + footer + deep-link Task 2) — FOUND
- Commit `92775f7` (deep-link tests Task 3) — FOUND

---
*Phase: 10-legal-and-compliance*
*Completed: 2026-04-23*
