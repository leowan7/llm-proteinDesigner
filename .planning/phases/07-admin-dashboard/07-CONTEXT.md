# Phase 7: Admin Dashboard - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning
**Mode:** Auto-selected (recommended defaults)

<domain>
## Phase Boundary

Platform operator (Leo) has full visibility into users, jobs, revenue, and system health through a custom admin dashboard at /admin. Admin can manage users, monitor jobs, view revenue metrics, and check system health. All admin actions are audit-logged.

This phase does NOT include: customer-facing analytics, team/organization management (Phase 12), or public API monitoring (Phase 13).

</domain>

<decisions>
## Implementation Decisions

### Admin Authentication
- **D-01:** Admin role stored as `is_admin BOOLEAN DEFAULT FALSE` column on `public.users` table (not Supabase custom claims in raw_app_meta_data — simpler, queryable, no JWT refresh delay)
- **D-02:** New `get_current_admin()` FastAPI dependency — calls `get_current_user()` then checks `is_admin` flag via DB query. Returns 403 if not admin.
- **D-03:** Admin routes at `/admin/*` prefix, separate FastAPI router with `Depends(get_current_admin)` on all endpoints
- **D-04:** Frontend admin guard: check `is_admin` from a `/user/me` endpoint (or extend existing `/user/settings` response). Redirect non-admins to /chat with no error (don't reveal admin exists).
- **D-05:** First admin bootstrapped via direct SQL: `UPDATE public.users SET is_admin = TRUE WHERE email = 'leo@ranomics.com'` — no self-service admin creation

### Admin Layout
- **D-06:** Separate `AdminLayout` component (not reusing `AuthenticatedLayout` sidebar) — admin has different nav structure
- **D-07:** Admin nav: Users, Jobs, Revenue, System, Audit Log — top-level tabs or sidebar sections
- **D-08:** Admin pages live at `/admin`, `/admin/users`, `/admin/jobs`, `/admin/revenue`, `/admin/system`, `/admin/audit`
- **D-09:** Dark theme consistent with rest of app (same Tailwind/shadcn tokens)

### Users Management
- **D-10:** Users table: email, display_name, created_at, last_login (from Supabase auth.users), payment_status (has Stripe customer + payment method), job_count (aggregated), total_spend
- **D-11:** No user edit/delete from admin — too risky for v1. View-only with link to Supabase Studio for manual operations.
- **D-12:** Search/filter by email, sort by created_at or job_count

### Jobs Management
- **D-13:** All-users jobs table: id, user email, tool, status, name, created_at, completed_at, gpu_seconds, gpu_cost_usd, error_category
- **D-14:** Admin can cancel stuck jobs (calls RunPod cancel API + updates DB status)
- **D-15:** Filter by status (running/complete/failed/cancelled), tool, user email
- **D-16:** Click job row to expand details: full parameters JSON, error message, candidate count, session link

### Revenue Overview
- **D-17:** Revenue is GPU cost from the jobs table (not Stripe MRR — Kendrew uses metered billing, Stripe doesn't report metered usage as MRR)
- **D-18:** Show: total GPU revenue (sum of gpu_cost_usd), total GPU cost-of-goods (sum of actual RunPod spend — if tracked, otherwise defer), margin
- **D-19:** Time period selector: This month, Last 30 days, All time
- **D-20:** Revenue by tool breakdown (pie or bar chart) — shows which tools generate most revenue
- **D-21:** Use Recharts for charts (lightweight, React-native, good dark theme support)

### System Health
- **D-22:** API health: hit `/health` endpoint and display status of API, DB, Redis
- **D-23:** GPU queue: count of jobs with status='running' and status='queued'
- **D-24:** Storage: R2 bucket size if available via API, otherwise defer
- **D-25:** No real-time monitoring (Grafana/Datadog is Phase 5 territory) — this is a snapshot dashboard, not live metrics

### Audit Log
- **D-26:** New `audit_log` table: id, admin_user_id, action (enum: cancel_job, view_user, etc.), target_id, metadata JSONB, created_at
- **D-27:** Log every admin action: page views (view_users, view_jobs), mutations (cancel_job)
- **D-28:** Audit log page shows reverse-chronological list with admin email, action, target, timestamp
- **D-29:** No retention policy for v1 — keep everything

### Claude's Discretion
- Exact card/table layout and spacing
- Chart color palette (within dark theme)
- Loading skeleton design for admin pages
- Pagination vs infinite scroll on admin tables (recommend pagination with 50/page)
- Whether to show admin link in the main sidebar or use a separate URL entry point

</decisions>

<specifics>
## Specific Ideas

- Revenue overview should feel like a simple Stripe dashboard — not a full BI tool, just the key numbers at a glance
- Admin should load fast — these are operational pages, not fancy data viz
- Keep it simple: tables with filters, a few summary cards at the top of each page, one chart for revenue breakdown
- No need for real-time updates — manual refresh is fine for v1

</specifics>

<canonical_refs>
## Canonical References

No external specs — requirements are fully captured in ROADMAP.md success criteria and decisions above.

### Codebase references
- `backend/auth/dependencies.py` — Existing `get_current_user()` pattern to extend for admin
- `backend/jobs/router.py` — Existing job endpoints pattern (reuse for admin-scoped queries)
- `backend/billing/router.py` — Stripe client patterns for payment status checks
- `frontend/src/components/layout/AuthenticatedLayout.tsx` — Layout pattern to reference (admin uses separate layout)
- `frontend/src/pages/JobHistoryPage.tsx` — Table + filter + pagination pattern to reuse
- `supabase/migrations/` — Latest schema for users, jobs, sessions tables

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/components/ui/table.tsx` — Full shadcn table (TableHeader, TableBody, TableCell, etc.)
- `frontend/src/components/common/StatusBadge.tsx` — Status badge with sr-only text (reuse for job status)
- `frontend/src/lib/api.ts` — `api()` fetch wrapper with auth cookies and CSRF
- `backend/auth/dependencies.py:get_current_user()` — JWT validation dependency to extend

### Established Patterns
- Backend: FastAPI router + `Depends(get_current_user)` + asyncpg pool queries
- Frontend: React Router nested routes + layout wrappers + shadcn components
- DB: Supabase migrations + RLS policies (admin will need RLS bypass or service role)

### Integration Points
- `backend/main.py` — Register admin router
- `frontend/src/App.tsx` — Add /admin routes (outside AuthenticatedLayout, inside AdminLayout)
- `supabase/migrations/` — New migration for is_admin column + audit_log table

</code_context>

<deferred>
## Deferred Ideas

- Real-time system monitoring with Grafana/Datadog — Phase 5 (Production Hardening)
- User impersonation ("login as user") — security risk, defer to much later
- Bulk job operations (cancel all stuck, re-run failed) — add to backlog
- Export data to CSV from admin tables — nice-to-have, add to backlog
- Email notifications to admin on job failures — Phase 5 territory

</deferred>

---

*Phase: 07-admin-dashboard*
*Context gathered: 2026-04-09*
