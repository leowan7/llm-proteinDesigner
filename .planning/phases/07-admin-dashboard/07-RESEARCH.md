# Phase 7: Admin Dashboard - Research

**Researched:** 2026-04-09
**Domain:** Internal admin tooling — FastAPI backend, React/shadcn frontend, Supabase/asyncpg, Recharts
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Admin Authentication**
- D-01: Admin role stored as `is_admin BOOLEAN DEFAULT FALSE` column on `public.users` (not Supabase custom claims — simpler, queryable, no JWT refresh delay)
- D-02: New `get_current_admin()` FastAPI dependency — calls `get_current_user()` then checks `is_admin` flag via DB query. Returns 403 if not admin.
- D-03: Admin routes at `/admin/*` prefix, separate FastAPI router with `Depends(get_current_admin)` on all endpoints
- D-04: Frontend admin guard checks `is_admin` from `/user/me` (or extend `/user/settings`). Redirect non-admins to /chat silently — do not reveal admin exists.
- D-05: First admin bootstrapped via direct SQL: `UPDATE public.users SET is_admin = TRUE WHERE email = 'leo@ranomics.com'` — no self-service admin creation

**Admin Layout**
- D-06: Separate `AdminLayout` component — does not reuse `AuthenticatedLayout`
- D-07: Admin nav: Users, Jobs, Revenue, System, Audit Log
- D-08: Admin pages at `/admin`, `/admin/users`, `/admin/jobs`, `/admin/revenue`, `/admin/system`, `/admin/audit`
- D-09: Dark theme consistent with rest of app (same Tailwind/shadcn tokens)

**Users Management**
- D-10: Users table: email, display_name, created_at, last_login (auth.users), payment_status, job_count, total_spend
- D-11: View-only in v1 — no edit/delete from admin UI
- D-12: Search/filter by email; sort by created_at or job_count

**Jobs Management**
- D-13: All-users jobs table: id, user email, tool, status, name, created_at, completed_at, gpu_seconds, gpu_cost_usd, error_category
- D-14: Admin can cancel stuck jobs (calls RunPod cancel API + updates DB status)
- D-15: Filter by status, tool, user email
- D-16: Click row to expand details: full parameters JSON, error message, candidate count, session link

**Revenue Overview**
- D-17: Revenue sourced from jobs table (not Stripe MRR — metered billing excluded from Stripe MRR)
- D-18: Show: total GPU revenue, cost-of-goods (if tracked), margin
- D-19: Time period selector: This month, Last 30 days, All time
- D-20: Revenue breakdown by tool (pie or bar chart)
- D-21: Use Recharts for charts

**System Health**
- D-22: API health: hit `/health` endpoint and display status of API, DB, Redis
- D-23: GPU queue: count of jobs with status='running' and status='queued'
- D-24: Storage: R2 bucket size if available via API, otherwise defer
- D-25: No real-time monitoring — snapshot dashboard, manual refresh

**Audit Log**
- D-26: New `audit_log` table: id, admin_user_id, action (enum), target_id, metadata JSONB, created_at
- D-27: Log every admin action: page views and mutations
- D-28: Audit log page: reverse-chronological, admin email, action, target, timestamp
- D-29: No retention policy for v1

### Claude's Discretion
- Exact card/table layout and spacing
- Chart color palette (within dark theme)
- Loading skeleton design for admin pages
- Pagination vs infinite scroll on admin tables (recommend pagination with 50/page)
- Whether to show admin link in main sidebar or use separate URL entry point

### Deferred Ideas (OUT OF SCOPE)
- Real-time monitoring with Grafana/Datadog (Phase 5)
- User impersonation
- Bulk job operations
- CSV export from admin tables
- Email notifications to admin on job failures (Phase 5)
</user_constraints>

---

## Summary

Phase 7 adds an internal admin dashboard at `/admin` for platform operator visibility. The phase has two clean layers: (1) backend — a new FastAPI router with a `get_current_admin` dependency plus DB migration for `is_admin`, `audit_log`; (2) frontend — a new `AdminLayout`, five admin pages, and Recharts for revenue charts.

All locked decisions are simple extensions of existing patterns. `get_current_admin` wraps `get_current_user` with a DB check. Admin job queries are identical to `jobs/router.py` queries but without the `user_id` filter. The `JobHistoryPage.tsx` keyset pagination pattern is directly reusable. The existing `public.jobs` schema already has all columns needed for admin views (`gpu_cost_usd`, `gpu_seconds`, `error_category`, `tool`, `status`).

The main technical decision points are: (a) how admin backend queries bypass RLS, (b) how `last_login` is read from `auth.users` (requires service-role key), and (c) how the RunPod cancel endpoint is called from an admin context that differs from the user-scoped cancel endpoint.

**Primary recommendation:** Use service-role key for admin DB queries (bypasses RLS cleanly). Expose `last_login` via a join on `auth.users` in the admin user query. Reuse the existing `cancel_job` business logic by extracting it into a shared `_cancel_job_by_id(job_id, pool)` function called by both the user and admin cancel endpoints.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| recharts | 3.8.1 | Revenue charts (bar, pie) | Already decided (D-21); lightweight React-native charting [VERIFIED: npm registry] |
| asyncpg | existing | Admin DB queries | Already in use throughout backend |
| shadcn/ui | 4.x | Admin table/card components | Already in project (table.tsx, StatusBadge.tsx) |
| react-router-dom | 7.13.1 | Admin route nesting | Already in use [VERIFIED: package.json] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| lucide-react | 0.577.0 | Admin nav icons | Already installed [VERIFIED: package.json] |
| tailwindcss | 4.2.2 | Admin layout styling | Already installed [VERIFIED: package.json] |

### Not Needed (Confirmed)
- No new auth library — extend existing `get_current_user` in `dependencies.py`
- No new charting library — Recharts decided (D-21)
- No real-time subscription — manual refresh only (D-25)

**Installation (Recharts only — not yet in project):**
```bash
cd frontend && npm install recharts
```

**Version verification:** recharts 3.8.1 confirmed via `npm view recharts version` — current as of 2026-04-09. [VERIFIED: npm registry]

---

## Architecture Patterns

### Backend Admin Router Pattern

The admin router follows the exact same FastAPI pattern as `jobs/router.py` and `user/router.py` — file at `backend/admin/router.py`, registered in `main.py`.

```python
# backend/admin/dependencies.py
# Source: extends existing backend/auth/dependencies.py pattern
from fastapi import Depends, HTTPException, status
from auth.dependencies import get_current_user
from db.connection import get_db_pool

async def get_current_admin(user_id: str = Depends(get_current_user)) -> str:
    """
    FastAPI dependency that verifies the authenticated user has is_admin = TRUE.

    Returns:
        user_id string if admin.

    Raises:
        HTTPException 403: If user is not admin. Does not reveal admin exists.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_admin FROM public.users WHERE id = $1",
            user_id,
        )
    if not row or not row["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    return user_id
```

### RLS Bypass for Admin Queries

**Critical decision:** The existing asyncpg pool uses the standard Postgres connection string which runs as the `postgres` superuser role on Supabase local dev. This already bypasses RLS for backend queries — RLS only applies when connecting as `anon` or `authenticated` roles via Supabase's PostgREST layer. [VERIFIED: Supabase docs pattern, confirmed by existing backend queries which already read all rows with `WHERE user_id = $1` without RLS issues]

Admin queries simply omit the `user_id = $1` filter — no special configuration needed. [ASSUMED — verify that prod Supabase also uses service-role Postgres URL, not anon key URL]

### Accessing auth.users for last_login

`auth.users` is a Supabase-managed table. The standard asyncpg pool can query it directly because the backend connects as the `postgres` superuser:

```sql
-- Admin users query with last_login from auth.users
SELECT
    u.id,
    u.email,
    u.display_name,
    u.created_at,
    u.stripe_customer_id,
    a.last_sign_in_at AS last_login,
    COUNT(DISTINCT j.id) AS job_count,
    COALESCE(SUM(j.gpu_cost_usd) FILTER (WHERE j.status = 'complete'), 0) AS total_spend
FROM public.users u
LEFT JOIN auth.users a ON a.id = u.id
LEFT JOIN public.jobs j ON j.user_id = u.id
GROUP BY u.id, u.email, u.display_name, u.created_at, u.stripe_customer_id, a.last_sign_in_at
ORDER BY u.created_at DESC;
```

[ASSUMED — `last_sign_in_at` is the correct column name on `auth.users`. Verify against Supabase Studio schema view before writing migration or query.]

### Shared Job Cancel Logic

The existing `cancel_job` endpoint in `jobs/router.py` is tightly coupled to `user_id` for ownership verification. Admin cancel must reuse the same RunPod + DB + billing logic without the ownership check.

**Pattern:** Extract cancellation business logic into `backend/jobs/service.py`:

```python
# backend/jobs/service.py
async def cancel_job_by_id(job_id: str, pool) -> dict:
    """
    Cancel a running job regardless of owner (admin use) or by owner (user use).
    Returns {"status": "cancelled", "gpu_seconds": int, "gpu_cost_usd": float}.
    """
    # ... same logic as current cancel_job endpoint, minus ownership check
```

Both `jobs/router.py` (user cancel, with ownership check) and `admin/router.py` (admin cancel, without) call this shared function.

### Audit Log Write Pattern

Every admin endpoint writes to `audit_log` after the action succeeds:

```python
async def _write_audit(
    admin_user_id: str,
    action: str,
    target_id: str | None,
    metadata: dict,
    pool,
) -> None:
    """Write a record to audit_log. Called after every admin action."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_log (admin_user_id, action, target_id, metadata)
               VALUES ($1, $2, $3, $4::jsonb)""",
            admin_user_id,
            action,
            target_id,
            json.dumps(metadata),
        )
```

Log page-view actions (GET endpoints) in addition to mutations — D-27 explicitly requires logging `view_users`, `view_jobs`.

### Frontend AdminLayout Pattern

`AdminLayout` follows the same shell as `AuthenticatedLayout` but with a simpler nav (no session list, no sidebar state from Supabase). Auth guard reads `is_admin` from `/user/settings` response (already returns user data, just needs `is_admin` field added).

```tsx
// frontend/src/components/layout/AdminLayout.tsx
// Pattern: mirror AuthenticatedLayout but with admin nav tabs
export function AdminLayout() {
  const navigate = useNavigate();
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

  useEffect(() => {
    api("/user/settings").then((data) => {
      if (!data.is_admin) navigate("/chat", { replace: true });
      else setIsAdmin(true);
    }).catch(() => navigate("/login", { replace: true }));
  }, [navigate]);

  if (isAdmin === null) return null;
  return (/* admin shell with top nav */);
}
```

The `/user/settings` endpoint needs `is_admin` added to its response. This is a one-line backend change to the existing `GET /user/settings` handler.

### Recharts Revenue Chart Pattern

Recharts 3.x works with React 19. [VERIFIED: recharts 3.8.1 current, React 19 in package.json]

```tsx
// Source: recharts.org — BarChart pattern for dark theme
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

const data = [
  { tool: "RFdiffusion", revenue: 124.5 },
  { tool: "BindCraft", revenue: 87.2 },
  // ...
];

<ResponsiveContainer width="100%" height={240}>
  <BarChart data={data}>
    <XAxis dataKey="tool" stroke="hsl(var(--muted-foreground))" />
    <YAxis stroke="hsl(var(--muted-foreground))" tickFormatter={(v) => `$${v}`} />
    <Tooltip
      contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))" }}
      formatter={(value) => [`$${Number(value).toFixed(2)}`, "Revenue"]}
    />
    <Bar dataKey="revenue" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
  </BarChart>
</ResponsiveContainer>
```

Use `hsl(var(--...))` CSS variables for dark-theme consistency with existing Tailwind tokens.

### Recommended Project Structure

```
backend/
└── admin/
    ├── __init__.py
    ├── router.py          # All /admin/* endpoints
    └── dependencies.py    # get_current_admin() dependency

backend/
└── jobs/
    ├── router.py          # Existing (user-scoped cancel updated to call service.py)
    └── service.py         # NEW: cancel_job_by_id() shared logic

frontend/src/
├── components/layout/
│   └── AdminLayout.tsx    # Admin shell (separate from AuthenticatedLayout)
└── pages/admin/
    ├── AdminUsersPage.tsx
    ├── AdminJobsPage.tsx
    ├── AdminRevenuePage.tsx
    ├── AdminSystemPage.tsx
    └── AdminAuditPage.tsx

supabase/migrations/
└── 20260409000001_admin.sql   # is_admin column + audit_log table
```

### Anti-Patterns to Avoid

- **Reusing AuthenticatedLayout for admin:** D-06 explicitly forbids this. Admin nav structure is different.
- **Fetching auth.users via Supabase Admin SDK:** Not needed — asyncpg pool already has postgres-level access. Adding a second client creates unnecessary complexity.
- **Creating admin-specific asyncpg pool:** Not needed. Same pool, same connection, RLS not active at this layer.
- **Logging audit events in a background task:** Write synchronously in the request handler to guarantee no audit gap on failure.
- **Exposing is_admin in JWT claims:** D-01 explicitly chose DB query over JWT claims to avoid JWT refresh delay.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Revenue charts | Custom SVG chart | Recharts BarChart/PieChart | D-21, handles responsive sizing, tooltips, dark theme theming |
| Admin auth | Custom session table | Extend existing get_current_user + is_admin column | Avoids second auth system, consistent with existing JWT flow |
| Pagination | Custom cursor logic | Copy JobHistoryPage keyset pattern | Pattern already proven in production, same keyset approach works |
| Audit log | Structured logging only | Dedicated audit_log table | Needs queryable history for admin UI (D-28), logs are append-only and hard to query |

---

## Common Pitfalls

### Pitfall 1: Silent 403 vs 404 on Admin Check
**What goes wrong:** Returning 404 (not found) instead of 403 when `is_admin` is false reveals that the route exists.
**Why it happens:** Developers use 404 to "hide" admin routes.
**How to avoid:** Return 403 with generic "Forbidden" message. D-04 requires non-admins to be redirected to /chat on the frontend — but the backend must still return 403 for any direct API call.
**Warning signs:** Frontend receives 404 and falls through to wrong error state.

### Pitfall 2: auth.users Column Name Assumption
**What goes wrong:** Querying `auth.users.last_sign_in_at` when the column is actually named differently.
**Why it happens:** Supabase `auth.users` schema is internal and not documented as a stable contract.
**How to avoid:** Verify the column name in Supabase Studio (`Table Editor → auth.users`) before writing the query. Alternative: use `SELECT column_name FROM information_schema.columns WHERE table_schema = 'auth' AND table_name = 'users'`.
**Warning signs:** `column "last_sign_in_at" does not exist` asyncpg error.

### Pitfall 3: Recharts + React 19 Peer Dependency Warning
**What goes wrong:** npm install recharts shows peer dependency warnings for React 18 (recharts peer dep) vs React 19 in project.
**Why it happens:** recharts 3.x peer deps list React ≤18 as requirement; project uses React 19.
**How to avoid:** Install with `--legacy-peer-deps` if npm blocks; recharts 3.8.1 works at runtime with React 19. [ASSUMED — functional compatibility, not officially certified for React 19]
**Warning signs:** npm install fails; if so use `npm install recharts --legacy-peer-deps`.

### Pitfall 4: Audit Log Missing on Failed Admin Action
**What goes wrong:** Admin cancels job, RunPod returns error, audit log entry never written.
**Why it happens:** Audit write comes after the action, exception skips it.
**How to avoid:** For mutations, write audit log in a try/finally block or write it before the mutation with a "attempted" status. For page views, the audit write can be fire-and-forget (non-critical).

### Pitfall 5: Revenue Query Performance
**What goes wrong:** `SUM(gpu_cost_usd) FROM public.jobs` with no date filter scans all rows — slow as data grows.
**Why it happens:** First version queries all historical data.
**How to avoid:** Always apply the time period filter in revenue queries. Add `created_at` index if not already present (check migration 20260318000000_init.sql — `created_at` not indexed on jobs; `idx_jobs_tool` exists but not on `created_at`). Add `CREATE INDEX idx_jobs_created_at ON public.jobs(created_at DESC)` in the Phase 7 migration.

### Pitfall 6: Admin Cancel Bypassing Billing
**What goes wrong:** Admin cancel route calls RunPod stop but doesn't record partial GPU billing to Stripe.
**Why it happens:** Admin cancel is built as a "force cancel" without copying billing logic.
**How to avoid:** Admin cancel must call the same `cancel_job_by_id` service function that includes Stripe meter recording — identical to user cancel.

---

## Code Examples

### DB Migration for Phase 7
```sql
-- supabase/migrations/20260409000001_admin.sql
-- Admin flag on users
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Audit log table (no RLS — admin-only, service role access)
CREATE TABLE IF NOT EXISTS public.audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id   UUID NOT NULL REFERENCES public.users(id),
    action          TEXT NOT NULL,  -- e.g. view_users, cancel_job, view_jobs
    target_id       TEXT,           -- nullable: job_id or user_id being acted on
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_created_at ON public.audit_log(created_at DESC);
CREATE INDEX idx_audit_log_admin_user ON public.audit_log(admin_user_id);

-- Performance index for revenue queries
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON public.jobs(created_at DESC);

-- Note: No RLS on audit_log — only accessible via admin router with postgres-level creds
```

### Admin Jobs Query (all users)
```python
# Source: extension of jobs/router.py list_jobs pattern — remove user_id filter
async with pool.acquire() as conn:
    rows = await conn.fetch(
        """SELECT j.id, u.email, j.tool, j.status, j.name,
                  j.created_at, j.completed_at, j.gpu_seconds,
                  j.gpu_cost_usd, j.error_category
           FROM public.jobs j
           JOIN public.users u ON u.id = j.user_id
           WHERE ($1::text IS NULL OR j.status = $1)
             AND ($2::text IS NULL OR u.email ILIKE '%' || $2 || '%')
             AND ($3::timestamptz IS NULL OR j.created_at < $3)
           ORDER BY j.created_at DESC
           LIMIT $4""",
        status_filter,   # $1
        email_filter,    # $2
        before_cursor,   # $3
        page_size,       # $4
    )
```

### Revenue Summary Query
```python
# Source: modeled on user/router.py get_usage pattern, extended to all users
async with pool.acquire() as conn:
    summary = await conn.fetchrow(
        """SELECT
               COALESCE(SUM(gpu_cost_usd) FILTER (WHERE status = 'complete'), 0) AS total_revenue,
               COUNT(*) FILTER (WHERE status = 'complete') AS completed_jobs,
               COUNT(*) FILTER (WHERE status = 'running') AS running_jobs,
               COUNT(*) FILTER (WHERE status = 'failed') AS failed_jobs
           FROM public.jobs
           WHERE ($1::timestamptz IS NULL OR created_at >= $1)""",
        period_start,  # None = all time
    )
    by_tool = await conn.fetch(
        """SELECT tool, COALESCE(SUM(gpu_cost_usd), 0) AS revenue
           FROM public.jobs
           WHERE status = 'complete'
             AND ($1::timestamptz IS NULL OR created_at >= $1)
           GROUP BY tool
           ORDER BY revenue DESC""",
        period_start,
    )
```

### Admin Route Registration in main.py
```python
# Add after existing router registrations in backend/main.py
from admin.router import router as admin_router
app.include_router(admin_router)
```

### Frontend Admin Route Registration in App.tsx
```tsx
// Add inside BrowserRouter, outside AuthenticatedLayout block
import { AdminLayout } from "./components/layout/AdminLayout";
import { AdminUsersPage } from "./pages/admin/AdminUsersPage";
// ...

<Route element={<AdminLayout />}>
  <Route path="/admin" element={<AdminUsersPage />} />
  <Route path="/admin/users" element={<AdminUsersPage />} />
  <Route path="/admin/jobs" element={<AdminJobsPage />} />
  <Route path="/admin/revenue" element={<AdminRevenuePage />} />
  <Route path="/admin/system" element={<AdminSystemPage />} />
  <Route path="/admin/audit" element={<AdminAuditPage />} />
</Route>
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Supabase custom claims for admin role | is_admin column on users table | D-01 decision | No JWT refresh delay; queryable; simpler |
| Raw auth.user_metadata checks | DB-level is_admin boolean | This phase | Consistent with existing asyncpg query patterns |

**Deprecated/outdated:**
- Supabase custom claims path: originally mentioned in requirements spec, overridden by D-01

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | asyncpg pool bypasses RLS (postgres superuser connection) in production Supabase, not just local dev | RLS Bypass section | Admin queries would return empty results or errors; would need service-role key explicitly in connection string |
| A2 | `auth.users.last_sign_in_at` is the correct column name for last login | Accessing auth.users section | Query fails with column-not-found error |
| A3 | recharts 3.8.1 works at runtime with React 19 despite peer dep warning | Standard Stack / Pitfalls | Chart components render incorrectly or fail silently; workaround: downgrade to React 18 or await recharts 4.x |
| A4 | No existing index on `public.jobs.created_at` | Revenue Query Pitfall + migration | Adding a duplicate index is a no-op (safe), but a missing index on a large jobs table causes slow revenue queries |

---

## Open Questions (RESOLVED)

1. **Production DB connection role**
   - What we know: Local dev uses `postgresql://postgres:postgres@127.0.0.1:54322/postgres` (superuser, bypasses RLS)
   - What's unclear: Prod Supabase connection string — does it use the `service_role` key (bypasses RLS) or `anon` key (enforces RLS)?
   - Recommendation: Check `config.py` `database_url` in `.env.production`. If using anon/authenticated role, admin queries need `SET LOCAL role TO service_role` or a separate pool initialized with service-role credentials. For v1 with a single operator, this is unlikely to be an issue, but verify before deploying.
   - **RESOLVED: Local dev verified (postgres superuser, bypasses RLS). Production assumed to use service-role Postgres URL — full verification deferred to Phase 11 deployment.**

2. **auth.users column names**
   - What we know: Supabase exposes an `auth.users` table but the schema is internal
   - What's unclear: Exact column name for last login — likely `last_sign_in_at` but not confirmed
   - Recommendation: Run `SELECT column_name FROM information_schema.columns WHERE table_schema = 'auth' AND table_name = 'users' ORDER BY ordinal_position` against local Supabase before writing the admin users query.
   - **RESOLVED: Plan 05 Task 1 Step 3 includes a runtime verification step that queries `information_schema.columns` for `auth.users` columns matching `%sign_in%` and patches `backend/admin/router.py` if the column name differs. Assumed name `last_sign_in_at` will be confirmed or corrected at migration-apply time.**

3. **is_admin in /user/settings response**
   - What we know: Frontend admin guard (D-04) needs to check `is_admin`; existing `/user/settings` endpoint does not return it
   - What's unclear: Whether to add `is_admin` to `/user/settings` response or create a separate `/user/me` endpoint
   - Recommendation: Add `is_admin` to the existing `GET /user/settings` response — one additional `SELECT` field, zero new endpoint complexity. The planner should include a task to update both the backend query and the TypeScript type.
   - **RESOLVED: Plan 01 Task 2 updates `GET /user/settings` to include `is_admin` in both the SQL SELECT and the response dict. No new endpoint needed.**

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| recharts (npm) | Revenue charts | Needs install | 3.8.1 (latest) | — |
| asyncpg | Admin DB queries | Already installed | existing | — |
| Supabase (local) | DB for admin migration | Running | local dev | — |

**Missing dependencies with no fallback:**
- recharts is not yet in `frontend/package.json` — must `npm install recharts` in Wave 0 or first task

**Missing dependencies with fallback:**
- None

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | vitest 4.1.0 (frontend); pytest (backend) |
| Config file | frontend/vite.config.ts (vitest config inline); backend/pytest.ini or pyproject.toml |
| Quick run command | `cd frontend && npx vitest run` / `cd backend && python -m pytest tests/admin/ -x` |
| Full suite command | `cd frontend && npx vitest run` / `cd backend && python -m pytest -x` |

### Phase Requirements → Test Map
| Req | Behavior | Test Type | Automated Command | File Exists? |
|-----|----------|-----------|-------------------|-------------|
| ADMIN-AUTH | get_current_admin returns 403 for non-admin | unit | `pytest tests/admin/test_dependencies.py -x` | No — Wave 0 |
| ADMIN-AUTH | get_current_admin returns user_id for admin | unit | `pytest tests/admin/test_dependencies.py -x` | No — Wave 0 |
| ADMIN-USERS | GET /admin/users returns all users with join data | integration | `pytest tests/admin/test_router.py::test_list_users -x` | No — Wave 0 |
| ADMIN-JOBS | GET /admin/jobs returns all-user jobs | integration | `pytest tests/admin/test_router.py::test_list_jobs -x` | No — Wave 0 |
| ADMIN-JOBS | POST /admin/jobs/{id}/cancel calls cancel_job_by_id | unit | `pytest tests/admin/test_router.py::test_cancel_job -x` | No — Wave 0 |
| ADMIN-REVENUE | Revenue aggregation query sums correctly by period | unit | `pytest tests/admin/test_router.py::test_revenue -x` | No — Wave 0 |
| ADMIN-AUDIT | Audit log entry written on cancel_job | unit | `pytest tests/admin/test_router.py::test_audit_log -x` | No — Wave 0 |

### Sampling Rate
- **Per task commit:** `cd backend && python -m pytest tests/admin/ -x`
- **Per wave merge:** `cd backend && python -m pytest -x && cd ../frontend && npx vitest run`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `backend/tests/admin/__init__.py` — test package
- [ ] `backend/tests/admin/test_dependencies.py` — covers admin auth
- [ ] `backend/tests/admin/test_router.py` — covers users, jobs, revenue, audit endpoints
- [ ] `backend/admin/__init__.py` — module package
- [ ] `frontend/src/pages/admin/` — directory (created during implementation)
- [ ] recharts install: `cd frontend && npm install recharts`

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Existing JWT + is_admin DB check (D-02) |
| V3 Session Management | no | Admin uses same session as regular user |
| V4 Access Control | yes | get_current_admin dependency on every /admin/* route; silent 403 to non-admins |
| V5 Input Validation | yes | Email filter uses ILIKE with parameterized query (no SQL injection); status filter validated against enum |
| V6 Cryptography | no | No new crypto — audit log stored plaintext (internal tool) |

### Known Threat Patterns for Admin Dashboard

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Privilege escalation via is_admin self-assignment | Elevation of Privilege | No `/user/settings` PUT accepts is_admin field; only direct SQL (D-05) |
| Admin route discovery by non-admin | Information Disclosure | Frontend silently redirects to /chat (D-04); backend returns 403 not 404 |
| Audit log tampering | Tampering | audit_log has no UPDATE/DELETE policy; append-only via admin router only |
| CSRF on admin cancel action | Tampering | Existing CSRFMiddleware covers all POST endpoints including /admin/* |
| SQL injection via email filter | Tampering | ILIKE uses parameterized query (`'%' || $2 || '%'`), not string concatenation |

---

## Sources

### Primary (HIGH confidence)
- `backend/auth/dependencies.py` — existing `get_current_user` pattern extended for `get_current_admin`
- `backend/jobs/router.py` — all-user query pattern (remove user_id filter)
- `backend/user/router.py` — `/user/settings` response shape to extend with `is_admin`
- `frontend/src/pages/JobHistoryPage.tsx` — keyset pagination pattern to reuse
- `frontend/src/components/layout/AuthenticatedLayout.tsx` — layout shell pattern for AdminLayout
- `frontend/package.json` — confirmed React 19.2.4, react-router-dom 7.13.1, shadcn 4.x, tailwindcss 4.x
- `supabase/migrations/` — confirmed existing schema: `public.users`, `public.jobs` columns
- npm registry — recharts 3.8.1 confirmed current

### Secondary (MEDIUM confidence)
- Recharts dark theme pattern — standard `hsl(var(--...))` CSS variable approach for shadcn token compatibility [CITED: recharts.org responsive container docs]

### Tertiary (LOW confidence)
- `auth.users.last_sign_in_at` column name — assumed from Supabase conventions, unverified against live schema

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified in package.json or npm registry
- Architecture: HIGH — follows established patterns from existing codebase with minor extensions
- DB schema: HIGH — verified against migration files; one LOW item (auth.users column name)
- Pitfalls: HIGH — derived from reading existing code patterns and identifying extension points

**Research date:** 2026-04-09
**Valid until:** 2026-05-09 (stable stack; Supabase schema stable)
