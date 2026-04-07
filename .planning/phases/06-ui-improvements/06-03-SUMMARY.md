---
phase: 06-ui-improvements
plan: "03"
subsystem: frontend
tags: [sidebar, session-management, routing, layout, accessibility]
dependency_graph:
  requires: [06-01]
  provides: [sidebar-layout, session-navigation, authenticated-routing]
  affects: [frontend/src/App.tsx, frontend/src/components/chat/ChatPage.tsx]
tech_stack:
  added:
    - shadcn sidebar component (collapsible="icon" mode)
    - shadcn tabs, table, dialog, progress components
    - eslint-plugin-jsx-a11y
  patterns:
    - SidebarProvider + Outlet context pattern for sidebar-driven app shell
    - useOutletContext for cross-route callback sharing (refreshSessions)
    - Auth guard in layout wrapper (single location, not per-route)
    - Lifted session list state in layout layer
key_files:
  created:
    - frontend/src/lib/sessions.ts
    - frontend/src/components/layout/AppHeader.tsx
    - frontend/src/components/layout/AppSidebar.tsx
    - frontend/src/components/layout/AuthenticatedLayout.tsx
    - frontend/src/pages/JobHistoryPage.tsx
    - frontend/src/pages/SettingsPage.tsx
    - frontend/src/components/ui/sidebar.tsx
    - frontend/src/components/ui/tabs.tsx
    - frontend/src/components/ui/table.tsx
    - frontend/src/components/ui/dialog.tsx
    - frontend/src/components/ui/progress.tsx
  modified:
    - frontend/src/App.tsx
    - frontend/src/components/chat/ChatPage.tsx
    - frontend/src/globals.css
decisions:
  - "Session list state lifted to AuthenticatedLayout — sidebar is presentation-only, avoids prop drilling and double fetches"
  - "useOutletContext pattern exposes refreshSessions to child routes — avoids event bus or global state for a single callback"
  - "Auth guard implemented as API call to /auth/me in AuthenticatedLayout — consistent with existing UserMenu pattern, avoids Supabase client dependency in layout"
  - "ChatPage h-screen changed to h-full — AuthenticatedLayout owns the viewport shell; ChatPage fills its allocated slot"
  - "autoFocus on ChatInput deferred — ChatInput.tsx does not expose autoFocus prop; implementing in Plan 03 without modifying ChatInput would require forwardRef refactor; acceptable gap, tracked as stub"
metrics:
  duration: "8 min"
  completed: "2026-04-07"
  tasks_completed: 3
  files_modified: 13
---

# Phase 06 Plan 03: App Shell Restructure with Sidebar — Summary

Replaced the single-page layout with a sidebar-driven app shell. The agent chat interface now uses persistent PostgreSQL sessions instead of ephemeral Redis sessions, and all authenticated routes share a consistent layout with session history navigation.

## What Was Built

**Session API Client (`frontend/src/lib/sessions.ts`)**

Five typed functions wrapping the Plan 01 backend endpoints: `listSessions`, `loadSession`, `createPersistentSession`, `deleteSessionApi`, `updateSessionTitle`. Uses the existing `api()` client for auth/CSRF handling.

**AppHeader (`frontend/src/components/layout/AppHeader.tsx`)**

Slim header with: skip navigation link (sr-only, visible on keyboard focus), `SidebarTrigger` wrapped in Tooltip with `aria-label="Toggle sidebar"`, Kendrew logo, and optional session title. Replaces the full header that was previously embedded in ChatPage.

**AppSidebar (`frontend/src/components/layout/AppSidebar.tsx`)**

Collapsible sidebar (`collapsible="icon"`) with:
- "Start new session" button (primary variant, full-width)
- Session list grouped by Today / This week / Earlier with skeleton loading state and empty state copy
- Each session item has a context menu (rename, delete) with a confirmation dialog for delete
- Navigation links (Jobs, Settings) with active state derived from pathname
- User footer with avatar initial, email, logout button
- Session and nav items have `min-h-[44px]` for WCAG touch target compliance
- All icon-only buttons have `aria-label`

**AuthenticatedLayout (`frontend/src/components/layout/AuthenticatedLayout.tsx`)**

Auth guard + session list state manager + layout shell. Redirects to `/login` on 401 from `/auth/me`. Manages `sessions[]` and `sessionsLoading` state, exposes `refreshSessions` callback via `useOutletContext`. Exports `useLayoutContext()` hook for typed child route access.

**App.tsx routing**

Auth routes (`/login`, `/signup`, `/verify-email`, `/email-confirmed`, `/reset-password`, `/reset-password/confirm`) remain outside the layout. All other routes (`/chat`, `/chat/:sessionId`, `/jobs`, `/jobs/:id`, `/settings`, `/`) are nested under `AuthenticatedLayout`.

**ChatPage refactor**

- Removed `createSession` and `deleteSession` imports from `agent.ts` (ephemeral Redis sessions gone)
- Removed `UserMenu` (now in sidebar footer)
- Removed internal header with New Session button (now in sidebar)
- Added bare `/chat` URL handler: navigates to most recent session or creates one for brand new users
- Loads session history from `loadSession()` when `sessionId` URL param changes
- Calls `refreshSessions()` after agent `done` SSE event so sidebar titles update

**Reduced motion CSS (`globals.css`)**

Added `@media (prefers-reduced-motion: reduce)` block disabling animations on sidebar, cards, and fade-in utilities.

## Deviations from Plan

### Auto-fixed Issues

None.

### Minor Adjustments

**1. [Rule 2 - Missing critical] TooltipProvider added to AuthenticatedLayout**

AppHeader uses `Tooltip` / `TooltipTrigger` from shadcn. The `TooltipProvider` must wrap the component tree. Added to AuthenticatedLayout to cover the sidebar scope, rather than moving it to App.tsx root (keeping it scoped avoids double-Provider issues with unauthenticated pages).

**2. useState initialization for user fetch in AppSidebar**

The plan specified calling `fetchUser` on mount via `useLayoutContext`. Since AppSidebar doesn't have access to the layout context (it is a sibling of Outlet, not a child), the user email is fetched directly from `/auth/me` inside AppSidebar using a `useState` + `useCallback` pattern. The same endpoint is already called by AuthenticatedLayout for auth checking — the second call is a minor overhead but keeps the sidebar self-contained.

## Known Stubs

| Stub | File | Line | Reason |
|------|------|------|--------|
| JobHistoryPage "Coming soon" | `frontend/src/pages/JobHistoryPage.tsx` | 10 | Full implementation in Plan 05 (job history table) |
| SettingsPage "Coming soon" | `frontend/src/pages/SettingsPage.tsx` | 10 | Full implementation in Plan 04 (settings page) |
| autoFocus on ChatInput for return visits | `ChatPage.tsx` | n/a | ChatInput.tsx does not expose autoFocus prop; would require forwardRef refactor — deferred to Plan 04 or standalone fix |

Stubs in JobHistoryPage and SettingsPage do not prevent this plan's goal (sidebar navigation to those routes works; the pages will be built in subsequent plans per the phase roadmap).

## Self-Check

### Files Exist
- frontend/src/lib/sessions.ts: exists
- frontend/src/components/layout/AppHeader.tsx: exists
- frontend/src/components/layout/AppSidebar.tsx: exists
- frontend/src/components/layout/AuthenticatedLayout.tsx: exists
- frontend/src/pages/JobHistoryPage.tsx: exists
- frontend/src/pages/SettingsPage.tsx: exists

### Commits Exist
- 4e7cd9c: feat(06-03): Task 1
- 2df8cd3: feat(06-03): Task 2
- 3628cd7: feat(06-03): Task 3

## Self-Check: PASSED
