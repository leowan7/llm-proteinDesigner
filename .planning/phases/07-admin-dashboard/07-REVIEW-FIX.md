---
phase: 07-admin-dashboard
fixed_at: 2026-04-10T01:36:34Z
review_path: .planning/phases/07-admin-dashboard/07-REVIEW.md
iteration: 1
findings_in_scope: 10
fixed: 9
skipped: 1
status: partial
---

# Phase 07: Code Review Fix Report

**Fixed at:** 2026-04-10T01:36:34Z
**Source review:** .planning/phases/07-admin-dashboard/07-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 10 (CR-01, WR-01 through WR-05, IN-01 through IN-04)
- Fixed: 9
- Skipped: 1 (IN-03 — already configured)

## Fixed Issues

### CR-01: Audit failure silently swallowed after successful cancel

**Files modified:** `backend/admin/router.py`
**Commit:** 60d7dd6
**Applied fix:** Added `import logging` and `_log = logging.getLogger(__name__)` at module level. Changed the bare `except Exception: pass` in `cancel_admin_job` to `except Exception as exc: _log.error("audit write failed after cancel_job %s: %s", job_id, exc)` so audit write failures appear in Sentry/structured logs rather than being silently discarded.

---

### WR-01: `get_job_detail` and `cancel_admin_job` pass raw string job_id to SQL without UUID validation

**Files modified:** `backend/admin/router.py`
**Commit:** 7974caa
**Applied fix:** Added `import uuid as uuid_mod` at module level. Added a `try: uuid_mod.UUID(job_id) except (ValueError, AttributeError): raise HTTPException(400, ...)` guard at the top of both `get_job_detail` and `cancel_admin_job`, returning HTTP 400 instead of an unhandled asyncpg DataError 500 when a non-UUID string is supplied.

---

### WR-02: Redis connection leak in `_check_sse_limit` when `incr` returns over-limit

**Files modified:** `backend/jobs/router.py`
**Commit:** d2a1a6e
**Applied fix:** Wrapped the Redis operations in `_check_sse_limit` with a `try/finally` block so `r.aclose()` is always called regardless of whether the 429 path raises or `r.decr` itself raises. Removed the two inline `await r.aclose()` calls that were placed before the raise and at the end of the function.

---

### WR-03: `cancel_job_by_id` in service.py has a TOCTOU race on job status

**Files modified:** `backend/jobs/service.py`
**Commit:** beee862
**Applied fix:** Added `AND status IN ('running', 'queued')` to the `UPDATE public.jobs SET gpu_cost_usd` query. Captured the return value (`update_result`) and parsed the row count from the `"UPDATE N"` string. If `rows_updated == 0`, logs a WARNING via module-level `_log` so operators can identify TOCTOU races where the job transitioned to a terminal state (e.g. via webhook) between the initial fetch and the UPDATE. Added `import logging` and `_log = logging.getLogger(__name__)` at module level.

---

### WR-04: Summary stat cards show page-scoped counts with no visual distinction

**Files modified:** `frontend/src/pages/admin/AdminJobsPage.tsx`
**Commit:** 7bd755e
**Applied fix:** Added `subLabel="this page"` to the "Running Jobs" and "Queued Jobs" `AdminStatCard` components (the two cards that were missing it). All four stat cards on AdminJobsPage now consistently display `subLabel="this page"`. AdminUsersPage already had correct subLabels and required no change.

---

### WR-05: `handleCancelConfirm` silently swallows cancel errors with no user feedback

**Files modified:** `frontend/src/pages/admin/AdminJobsPage.tsx`
**Commit:** 88d781c
**Applied fix:** Added `cancelError` state (`useState<string | null>(null)`). Updated `handleCancelConfirm` to call `setCancelError(null)` on entry and set a descriptive error message in the `catch` block. Added `{cancelError && <p className="text-destructive text-sm">{cancelError}</p>}` inside `DialogContent` between the header and footer. Also updated `onOpenChange` to clear `cancelError` when the dialog is dismissed so stale errors do not persist on re-open.

### IN-01: `get_job_detail` uses `j.*` SELECT returning all columns including sensitive ones

**Files modified:** `backend/admin/router.py`
**Commit:** 542778b
**Applied fix:** Replaced `SELECT j.*, u.email AS user_email` with an explicit column list (`j.id, j.user_id, j.tool, j.status, j.stage, j.name, j.created_at, j.completed_at, j.started_at, j.gpu_seconds, j.gpu_cost_usd, j.error_category, j.results, j.job_spec, j.session_id, u.email AS user_email`), omitting `job_token` and `runpod_job_id`. The return dict already only used these columns; this makes the safety boundary explicit in the query rather than relying on the return dict to filter.

---

### IN-02: `relativeDate` helper duplicated in AdminUsersPage and AdminJobsPage

**Files modified:** `frontend/src/lib/format.ts` (created), `frontend/src/pages/admin/AdminUsersPage.tsx`, `frontend/src/pages/admin/AdminJobsPage.tsx`
**Commit:** 13b176f
**Applied fix:** Created `frontend/src/lib/format.ts` with an exported `relativeDate` function (identical logic, now with JSDoc). Removed the local `relativeDate` function from both pages and replaced with `import { relativeDate } from "@/lib/format"`.

---

### IN-04: `audit_log` table has no explicit role grants/revokes

**Files modified:** `supabase/migrations/20260409000001_admin.sql`
**Commit:** 0d64744
**Applied fix:** Added `REVOKE ALL ON public.audit_log FROM anon, authenticated;` and `GRANT SELECT, INSERT ON public.audit_log TO service_role;` after the index definitions. Added a comment clarifying the intent: PostgREST roles (`anon`, `authenticated`) are explicitly denied; only the backend service role has access.

---

## Skipped Issues

### IN-03: Tests lack `@pytest.mark.asyncio` decorator

**File:** `backend/tests/admin/test_dependencies.py`, `test_router.py`, `test_service.py`
**Reason:** Already configured — `backend/pytest.ini` contains `asyncio_mode = auto`, which causes pytest-asyncio to automatically handle all `async def` test functions without requiring per-test decorators. No code change needed.
**Original issue:** Tests are `async def` but lack `@pytest.mark.asyncio` decorator; without auto mode configured they would be silently skipped.

---

_Fixed: 2026-04-10T01:36:34Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
