---
phase: 07-admin-dashboard
plan: 01
subsystem: api
tags: [fastapi, asyncpg, postgres, admin, audit, stripe, runpod]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: get_current_user dependency, asyncpg pool, users table, JWT auth
  - phase: 03-job-execution-frontend-and-billing
    provides: jobs table with gpu_cost_usd/gpu_seconds, RunPodProvider, Stripe billing

provides:
  - is_admin BOOLEAN column on public.users
  - audit_log table (append-only, no RLS)
  - idx_jobs_created_at index for revenue query performance
  - get_current_admin FastAPI dependency (DB-checked, 403 for non-admins)
  - write_audit helper (synchronous, append-only)
  - cancel_job_by_id shared service (admin + user cancel reuse)
  - GET /admin/users — all users with email filter, sort, keyset pagination
  - GET /admin/jobs — all-user jobs with status/tool/email filters
  - GET /admin/jobs/{id} — full job detail endpoint
  - POST /admin/jobs/{id}/cancel — admin job cancel with billing
  - GET /admin/revenue — period-filtered revenue summary with cost_of_goods/margin
  - GET /admin/system — API/DB/Redis health + GPU queue counts
  - GET /admin/audit — paginated audit log
  - is_admin field added to GET /user/settings response

affects: [07-02-admin-frontend, 07-03-admin-frontend-jobs, 07-04-admin-revenue-chart, 07-05-admin-system-audit]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - get_current_admin wraps get_current_user with DB-level is_admin check — no JWT claim shortcut
    - write_audit called synchronously in every endpoint (not background task) to guarantee no audit gap
    - cancel_job_by_id extracted to service.py — shared between user router (with ownership check) and admin router (without)
    - Admin queries omit user_id filter — same asyncpg pool, postgres superuser bypasses RLS

key-files:
  created:
    - supabase/migrations/20260409000001_admin.sql
    - backend/admin/__init__.py
    - backend/admin/dependencies.py
    - backend/admin/audit.py
    - backend/admin/router.py
    - backend/jobs/service.py
  modified:
    - backend/main.py
    - backend/user/router.py
    - backend/jobs/router.py

key-decisions:
  - "cancel_job_by_id extracted to jobs/service.py — shared cancel logic with ownership check kept in user router, none in admin router"
  - "cost_of_goods_usd reverse-calculated from gpu_markup_percent since jobs table stores billed amount (with markup), not raw RunPod cost"
  - "is_admin not accepted in UserSettingsUpdate Pydantic model — only returned in GET response (T-07-02)"
  - "write_audit called synchronously in request path, not as background task, to prevent audit gaps on handler failure"
  - "TOOL_IMAGES dict moved to service.py and imported from router.py (single source of truth)"

patterns-established:
  - "Admin auth: DB query on every request, 403 response does not reveal admin route existence"
  - "Audit logging: synchronous write in request path after every admin action (page views + mutations)"
  - "Admin cancel: try/finally around audit write ensures audit is recorded even if write fails"

requirements-completed: [SC-5, SC-1, SC-2, SC-3, SC-6]

# Metrics
duration: 25min
completed: 2026-04-09
---

# Phase 7 Plan 01: Admin Backend Summary

**FastAPI admin router with 7 endpoints, get_current_admin dependency, audit_log table, and shared cancel service — full admin API layer ready for frontend plans.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-04-09
- **Completed:** 2026-04-09
- **Tasks:** 3
- **Files modified:** 9 (5 created, 4 modified)

## Accomplishments
- DB migration adds is_admin column, audit_log table, and idx_jobs_created_at index
- get_current_admin dependency enforces DB-level admin check on all 7 /admin/* endpoints; returns 403 (not 404) to avoid revealing admin surface
- All 7 admin endpoints implemented with audit logging: users list, jobs list, job detail, job cancel, revenue summary, system health, audit log
- cancel_job_by_id extracted into shared service (used by both user and admin cancel endpoints); jobs/router.py cancel_job now delegates to it after ownership check
- /user/settings now returns is_admin boolean for frontend admin guard (D-04)

## Task Commits

1. **Task 1: DB migration, auth dependency, audit helper, cancel service** - `51a18a8` (feat)
2. **Task 2: Admin router (users, jobs, cancel) + main.py + user/settings** - `be93ad5` (feat)
3. **Task 3: Revenue, system health, and audit endpoints** - `30a6013` (feat)

## Files Created/Modified
- `supabase/migrations/20260409000001_admin.sql` - is_admin column, audit_log table, idx_jobs_created_at
- `backend/admin/__init__.py` - module package
- `backend/admin/dependencies.py` - get_current_admin dependency (403 for non-admins)
- `backend/admin/audit.py` - write_audit helper (synchronous, append-only)
- `backend/admin/router.py` - all 7 /admin/* endpoints with audit logging
- `backend/jobs/service.py` - cancel_job_by_id shared service + TOOL_IMAGES dict
- `backend/main.py` - registered admin_router
- `backend/user/router.py` - is_admin added to GET /user/settings response
- `backend/jobs/router.py` - cancel_job refactored to use cancel_job_by_id; TOOL_IMAGES imported from service

## Decisions Made
- cost_of_goods_usd is reverse-calculated from total_revenue and gpu_markup_percent (jobs table stores billed amount including markup, not raw RunPod cost). Returns null when markup is 0.
- TOOL_IMAGES dict moved to jobs/service.py as the single source of truth; jobs/router.py imports from there.
- write_audit in cancel_admin_job wrapped in try/except (not try/finally) because audit failure must not surface to admin as an error — the cancel result is returned regardless.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Python not on PATH in bash shell (Windows). Used full path `/c/Users/lab/AppData/Local/Programs/Python/Python313/python.exe` for import verification. No code change required.

## Known Stubs

None — all endpoints implement real query logic. No hardcoded empty values or placeholder responses.

## Threat Flags

No new threat surface beyond what is documented in the plan's threat model. All T-07-01 through T-07-05 mitigations implemented:
- T-07-01: get_current_admin queries DB on every request; 403 does not reveal admin route
- T-07-02: is_admin not in UserSettingsUpdate model; only returned in GET response
- T-07-03: write_audit is append-only INSERT; no UPDATE/DELETE on audit_log
- T-07-04: email filter uses parameterized ILIKE ($1 parameter, not string concat)
- T-07-05: admin cancel calls cancel_job_by_id which includes Stripe billing recording

## Next Phase Readiness
- All 7 /admin/* API endpoints are ready for the Wave 2 frontend plans (07-02 through 07-05)
- /user/settings returns is_admin — AdminLayout frontend guard can use this immediately
- Admin bootstrap requires manual SQL: `UPDATE public.users SET is_admin = TRUE WHERE email = 'leo@ranomics.com'`

---
*Phase: 07-admin-dashboard*
*Completed: 2026-04-09*
