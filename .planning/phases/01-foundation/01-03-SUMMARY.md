---
phase: 01-foundation
plan: 03
subsystem: ui
tags: [react, typescript, vite, shadcn, tailwindcss, vitest, react-router-dom, inter-font]

# Dependency graph
requires:
  - phase: 01-01
    provides: docker-compose and project scaffold (API server target)

provides:
  - Vite + React + TypeScript frontend project at frontend/
  - shadcn/ui 4.x components: button, input, label, card, form
  - Dark theme with indigo-500 primary override via CSS variables in globals.css
  - Inter font at weights 400/600 via @fontsource/inter
  - react-router-dom BrowserRouter with dark class wrapper
  - AuthLayout component for all 6 auth screens
  - API client (api.ts) with cookie credentials, CSRF handling, 401 refresh retry
  - vitest configured with jsdom environment, 2 passing smoke tests

affects:
  - 01-04 (auth screens use AuthLayout and api.ts)
  - any future frontend plans

# Tech tracking
tech-stack:
  added:
    - vite 6.x (build tool + dev server)
    - react 19.x + react-dom
    - typescript 5.x
    - tailwindcss 4.x (with @tailwindcss/vite plugin)
    - shadcn/ui 4.x (button, input, label, card, form components)
    - tw-animate-css (shadcn animation dependency)
    - react-router-dom 7.x
    - "@fontsource/inter" (400, 600 weights)
    - react-hook-form + @hookform/resolvers + zod
    - lucide-react (icon library)
    - vitest 4.x + @testing-library/react + jsdom
  patterns:
    - CSS variable dark theme override in globals.css (.dark class)
    - OKLCH color system with HSL indigo-500 override for primary/ring
    - @ path alias resolving to src/ (tsconfig + vite.config.ts)
    - credentials: include on all API fetches for HTTP-only cookie auth
    - Silent 401 refresh retry pattern in api.ts

key-files:
  created:
    - frontend/src/globals.css
    - frontend/src/lib/api.ts
    - frontend/src/lib/api.test.ts
    - frontend/src/components/auth/AuthLayout.tsx
    - frontend/src/components/ui/form.tsx
    - frontend/vite.config.ts
    - frontend/tailwind.config.ts
    - frontend/components.json
  modified:
    - frontend/src/main.tsx
    - frontend/src/App.tsx
    - frontend/index.html
    - frontend/tsconfig.app.json
    - frontend/tsconfig.json

key-decisions:
  - "shadcn 4.x requires Tailwind v4: upgraded from planned Tailwind v3; font/color config moved to CSS @theme inline block; tailwind.config.ts kept as reference doc only"
  - "indigo-500 dark theme override uses HSL value in CSS variable (238.7 83.5% 66.7%) inside .dark block, co-existing with OKLCH values from shadcn defaults"
  - "form.tsx created manually since shadcn 4.x add form command produced no output; component is a standard react-hook-form wrapper following established shadcn patterns"
  - "globals.css is the canonical stylesheet; index.css imports it; main.tsx imports globals.css directly"

patterns-established:
  - "AuthLayout pattern: all auth screens wrap content in AuthLayout with title/subtitle/footer/children props"
  - "API client pattern: api<T>(path, options) throws ApiError on non-2xx, handles CSRF and refresh retry"
  - "Dark theme: html.dark class + .dark CSS block in globals.css; dark class applied in App.tsx wrapper div"

requirements-completed:
  - AUTH-04

# Metrics
duration: 11min
completed: 2026-03-18
---

# Phase 01 Plan 03: Frontend Scaffold Summary

**Vite + React + TypeScript frontend with shadcn/ui 4.x, dark indigo theme, AuthLayout, and typed API client with HTTP-only cookie auth and CSRF handling**

## Performance

- **Duration:** 11 min
- **Started:** 2026-03-18T21:21:09Z
- **Completed:** 2026-03-18T21:31:59Z
- **Tasks:** 2
- **Files modified:** 28 (all new)

## Accomplishments

- Vite dev server running with shadcn/ui components (button, input, label, card, form), dark zinc background, indigo-500 primary accent
- AuthLayout component implementing the UI-SPEC layout contract: 400px centered Card, 20px/600 heading, 16px/400 muted subtitle
- API client with cookie credentials, CSRF token injection on mutating requests, and silent 401 refresh retry to /auth/refresh
- vitest configured with jsdom, 2 smoke tests pass for ApiError class

## Task Commits

Each task was committed atomically:

1. **Task 1: Vite + React + TypeScript scaffold with shadcn/ui, dark theme, and vitest** - `c1d3053` (feat)
2. **Task 2: AuthLayout component, API client, and smoke test** - `3f26d65` (feat)

**Plan metadata:** _(this commit)_

## Files Created/Modified

- `frontend/src/globals.css` - Dark theme CSS variables with indigo-500 override for --primary and --ring
- `frontend/src/index.css` - Entry point importing globals.css
- `frontend/src/main.tsx` - React entry: Inter font imports, globals.css import
- `frontend/src/App.tsx` - BrowserRouter with dark class wrapper div
- `frontend/index.html` - html class="dark" for dark theme activation
- `frontend/vite.config.ts` - @tailwindcss/vite plugin, @ alias, vitest jsdom config
- `frontend/tailwind.config.ts` - Reference doc with Inter fontFamily (Tailwind v4 reads from CSS)
- `frontend/components.json` - shadcn 4.x config
- `frontend/tsconfig.app.json` - Path alias @ -> ./src/*
- `frontend/tsconfig.json` - Added compilerOptions with baseUrl/paths for shadcn detection
- `frontend/src/components/ui/button.tsx` - shadcn Button component
- `frontend/src/components/ui/input.tsx` - shadcn Input component
- `frontend/src/components/ui/label.tsx` - shadcn Label component
- `frontend/src/components/ui/card.tsx` - shadcn Card, CardHeader, CardContent, CardFooter
- `frontend/src/components/ui/form.tsx` - Manual shadcn Form wrapper (react-hook-form integration)
- `frontend/src/lib/utils.ts` - cn() utility (shadcn default)
- `frontend/src/lib/api.ts` - API client: credentials: include, x-csrftoken, 401 refresh retry
- `frontend/src/lib/api.test.ts` - Vitest smoke test: ApiError construction (2 tests)
- `frontend/src/components/auth/AuthLayout.tsx` - Shared auth page layout with centered Card

## Decisions Made

- Upgraded to Tailwind v4 (shadcn 4.x requires it; plan specified v3). Font and theme configuration moved from tailwind.config.ts to CSS @theme inline block per Tailwind v4 conventions.
- indigo-500 override uses HSL format (`238.7 83.5% 66.7%`) in the `.dark` CSS block, co-existing with OKLCH values from shadcn defaults. This preserves the exact artifact value the plan specifies.
- form.tsx was created manually since `npx shadcn@latest add form` produced no output in shadcn 4.x. The component matches the canonical shadcn form pattern.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Tailwind v4 required by shadcn 4.x instead of planned v3**
- **Found during:** Task 1 (shadcn init)
- **Issue:** `npx shadcn@latest init` (v4.0.8) failed with Tailwind v3 installed. shadcn 4.x dropped Tailwind v3 support; requires v4 with `@tailwindcss/vite` plugin.
- **Fix:** Uninstalled tailwindcss@3/postcss/autoprefixer, installed tailwindcss@4 + @tailwindcss/vite. Updated vite.config.ts to use the Vite plugin instead of PostCSS. Font configuration moved to CSS `@theme inline` block in globals.css.
- **Files modified:** frontend/vite.config.ts, frontend/src/globals.css (removed tailwind.config.js, postcss.config.js)
- **Verification:** `npx tsc --noEmit` passes; `npx vitest run` passes 2 tests
- **Committed in:** c1d3053 (Task 1 commit)

**2. [Rule 3 - Blocking] shadcn init requires tsconfig.json compilerOptions with paths**
- **Found during:** Task 1 (shadcn init — first attempt)
- **Issue:** shadcn validated import alias from root tsconfig.json, which only had `references` and no `compilerOptions`. Alias defined in tsconfig.app.json was not found.
- **Fix:** Added `compilerOptions: { baseUrl: ".", paths: { "@/*": ["./src/*"] } }` to root tsconfig.json alongside existing references.
- **Files modified:** frontend/tsconfig.json
- **Verification:** shadcn init passed alias validation on re-run
- **Committed in:** c1d3053 (Task 1 commit)

**3. [Rule 3 - Blocking] form component not added by shadcn add form command**
- **Found during:** Task 1 (shadcn add button input label card form)
- **Issue:** `npx shadcn@latest add form` ran without error but created no files. shadcn 4.x may have renamed or restructured the form component.
- **Fix:** Created frontend/src/components/ui/form.tsx manually following the canonical shadcn form pattern (FormProvider, FormField, FormItem, FormLabel, FormControl, FormDescription, FormMessage).
- **Files modified:** frontend/src/components/ui/form.tsx (new file)
- **Verification:** TypeScript compiles without errors; form.tsx exports all required components
- **Committed in:** c1d3053 (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (all Rule 3 blocking issues caused by shadcn 4.x vs planned shadcn 2.x/3.x behavior)
**Impact on plan:** All fixes necessary to complete Task 1. No scope change. Final output matches all plan acceptance criteria.

## Issues Encountered

None beyond the deviations documented above.

## User Setup Required

None - no external service configuration required. Run `cd frontend && npm run dev` to start the dev server.

## Next Phase Readiness

- Frontend scaffold complete. All 6 auth screen components can be built using AuthLayout + shadcn components.
- api.ts provides the typed fetch wrapper Plan 04 will use for auth endpoint calls.
- shadcn form.tsx is ready for react-hook-form integration in signup/login forms.
- No blockers for Plan 04.

---
*Phase: 01-foundation*
*Completed: 2026-03-18*
