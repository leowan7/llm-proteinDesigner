---
phase: 07-admin-dashboard
plan: 03
subsystem: frontend
tags: [react, typescript, tailwind, shadcn, admin, auth-guard, pagination]

# Dependency graph
requires:
  - phase: 07-admin-dashboard
    plan: 01
    provides: /admin/* backend endpoints, is_admin field on /user/settings

provides:
  - AdminLayout with is_admin auth guard and sidebar nav
  - AdminStatCard reusable metric card component
  - admin.ts API client (7 typed fetch functions)
  - AdminUsersPage: paginated, filterable user table
  - AdminJobsPage: paginated, filterable jobs table with row expansion and cancel
  - Stub pages: AdminRevenuePage, AdminSystemPage, AdminAuditPage (Plan 04 replacements)
  - App.tsx routing: 6 /admin/* paths behind AdminLayout

affects: [07-04-admin-revenue-system-audit]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - AdminLayout auth guard fetches /user/settings, checks is_admin, silently redirects non-admins to /chat (D-04)
    - Keyset pagination: cursorStack push/pop pattern (same as JobHistoryPage) — no page numbers
    - 300ms debounce on email filter inputs using useRef timer
    - Row expansion in AdminJobsPage: click row → fetchAdminJobDetail → inline <tr colSpan> panel
    - Cancel flow: Button sets cancelDialogJobId state → Dialog renders → confirm calls cancelAdminJob → refetch

key-files:
  created:
    - frontend/src/lib/admin.ts
    - frontend/src/components/admin/AdminStatCard.tsx
    - frontend/src/components/layout/AdminLayout.tsx
    - frontend/src/pages/admin/AdminUsersPage.tsx
    - frontend/src/pages/admin/AdminJobsPage.tsx
    - frontend/src/pages/admin/AdminRevenuePage.tsx
    - frontend/src/pages/admin/AdminSystemPage.tsx
    - frontend/src/pages/admin/AdminAuditPage.tsx
  modified:
    - frontend/src/App.tsx

key-decisions:
  - "Summary card totals derived from current page data (not a dedicated summary endpoint) — documented as page-level approximations, sufficient for operational dashboard"
  - "Row expansion fetches /admin/jobs/{id} on first open; collapses on second click; falls back to list-row data if detail fetch fails"
  - "Cancel dialog uses cancelDialogJobId state (string | null) as both open trigger and job ID carrier — avoids separate isOpen boolean"
  - "AdminUsersPage is view-only per D-11 — no edit/delete actions in the table"

# Metrics
duration: 25min
completed: 2026-04-09
---

# Phase 7 Plan 03: Admin Frontend Foundation Summary

**AdminLayout with is_admin auth guard, admin API client (7 typed functions), AdminUsersPage and AdminJobsPage with keyset pagination, row expansion, and cancel dialog — full admin frontend foundation ready for Plan 04 revenue/system/audit pages.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-04-09
- **Tasks:** 2
- **Files modified:** 9 (8 created, 1 modified)

## Accomplishments

- `admin.ts` API client exports 7 typed functions covering all 7 /admin/* endpoints with URLSearchParams building and full TypeScript interface coverage (AdminUser, AdminJob, AdminRevenue, AdminSystemHealth, AuditEntry)
- `AdminLayout` enforces is_admin check via /user/settings on mount; non-admins silently redirect to /chat (D-04); auth errors redirect to /login; sidebar collapses to icon-only at 56px width; WCAG 44px min nav touch targets
- `AdminStatCard` renders Display-role 28px metric values with label/sub-label per UI-SPEC typography contract
- `AdminUsersPage` delivers email filter (debounced 300ms), sort dropdown (3 options), keyset pagination (50/page), 7-column table, loading skeleton, empty/error states
- `AdminJobsPage` delivers status/tool/email filters, row expansion with full params JSON in `<pre>`, error message, candidate count, session link; cancel button (destructive) opens Dialog with billing warning copy per UI-SPEC; refetches page on successful cancel
- Three stub pages (AdminRevenuePage, AdminSystemPage, AdminAuditPage) created for compilation — Plan 04 replaces them
- App.tsx updated with 6 /admin/* routes inside AdminLayout block, positioned before AuthenticatedLayout

## Task Commits

1. **Task 1: AdminLayout, AdminStatCard, admin API client, stub pages, App.tsx routing** — `b20224d`
2. **Task 2: AdminUsersPage and AdminJobsPage with full functionality** — `15362dc`

## Files Created/Modified

- `frontend/src/lib/admin.ts` — 7 typed fetch functions, 5 TypeScript interfaces
- `frontend/src/components/admin/AdminStatCard.tsx` — metric card with text-[28px] Display typography
- `frontend/src/components/layout/AdminLayout.tsx` — auth guard, collapsible sidebar, Outlet
- `frontend/src/pages/admin/AdminUsersPage.tsx` — users table with filter, sort, pagination
- `frontend/src/pages/admin/AdminJobsPage.tsx` — jobs table with expansion, cancel dialog
- `frontend/src/pages/admin/AdminRevenuePage.tsx` — stub (Plan 04)
- `frontend/src/pages/admin/AdminSystemPage.tsx` — stub (Plan 04)
- `frontend/src/pages/admin/AdminAuditPage.tsx` — stub (Plan 04)
- `frontend/src/App.tsx` — AdminLayout route block with 6 /admin/* paths

## Deviations from Plan

### Auto-applied approach adjustments

**1. Temporary compile stubs for AdminUsersPage and AdminJobsPage**
- **Found during:** Task 1
- **Issue:** App.tsx imports AdminUsersPage and AdminJobsPage which didn't exist yet, causing TypeScript compile failure during Task 1 verification
- **Fix:** Created minimal 3-line stubs during Task 1 (committed), then replaced with full implementations in Task 2 (committed)
- **Files modified:** frontend/src/pages/admin/AdminUsersPage.tsx, frontend/src/pages/admin/AdminJobsPage.tsx
- **Commit:** b20224d (stubs), 15362dc (full implementations)

## Known Stubs

| Stub | File | Reason | Resolved by |
|------|------|---------|-------------|
| AdminRevenuePage | `frontend/src/pages/admin/AdminRevenuePage.tsx` | Plan 04 implements full Recharts bar chart + by-tool table | Plan 07-04 |
| AdminSystemPage | `frontend/src/pages/admin/AdminSystemPage.tsx` | Plan 04 implements health status cards + GPU queue | Plan 07-04 |
| AdminAuditPage | `frontend/src/pages/admin/AdminAuditPage.tsx` | Plan 04 implements reverse-chronological audit log table | Plan 07-04 |

These stubs do not affect the plan's goal — AdminUsersPage and AdminJobsPage are the primary deliverables and are fully implemented. The three stubs allow App.tsx to compile and routing to work end-to-end; they are intentional placeholders per plan design.

## Threat Flags

No new threat surface beyond what is documented in the plan's threat model.

- T-07-08 (Information Disclosure): AdminLayout checks is_admin from /user/settings before rendering any admin content; non-admins are silently redirected to /chat without error message — implemented.
- T-07-09 (Spoofing): All admin API calls go through api() which includes credentials and CSRF token; backend enforces get_current_admin on every endpoint — implemented at API client layer.

## Self-Check: PASSED

Files verified present:
- frontend/src/lib/admin.ts — FOUND
- frontend/src/components/admin/AdminStatCard.tsx — FOUND
- frontend/src/components/layout/AdminLayout.tsx — FOUND
- frontend/src/pages/admin/AdminUsersPage.tsx — FOUND
- frontend/src/pages/admin/AdminJobsPage.tsx — FOUND
- frontend/src/pages/admin/AdminRevenuePage.tsx — FOUND
- frontend/src/pages/admin/AdminSystemPage.tsx — FOUND
- frontend/src/pages/admin/AdminAuditPage.tsx — FOUND
- frontend/src/App.tsx — FOUND (modified)

Commits verified:
- b20224d — FOUND
- 15362dc — FOUND

TypeScript compilation: zero errors (npx tsc --noEmit exit 0)

---
*Phase: 07-admin-dashboard*
*Completed: 2026-04-09*
