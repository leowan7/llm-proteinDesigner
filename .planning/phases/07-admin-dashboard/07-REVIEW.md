---
phase: 07-admin-dashboard
reviewed: 2026-04-09T00:00:00Z
depth: standard
files_reviewed: 22
files_reviewed_list:
  - backend/admin/__init__.py
  - backend/admin/audit.py
  - backend/admin/dependencies.py
  - backend/admin/router.py
  - backend/jobs/service.py
  - backend/jobs/router.py
  - backend/main.py
  - backend/user/router.py
  - backend/tests/admin/test_dependencies.py
  - backend/tests/admin/test_router.py
  - backend/tests/admin/test_service.py
  - frontend/src/App.tsx
  - frontend/src/components/admin/AdminStatCard.tsx
  - frontend/src/components/layout/AdminLayout.tsx
  - frontend/src/lib/admin.ts
  - frontend/src/pages/admin/AdminAuditPage.tsx
  - frontend/src/pages/admin/AdminJobsPage.tsx
  - frontend/src/pages/admin/AdminRevenuePage.tsx
  - frontend/src/pages/admin/AdminSystemPage.tsx
  - frontend/src/pages/admin/AdminUsersPage.tsx
  - supabase/migrations/20260409000001_admin.sql
  - frontend/package.json
findings:
  critical: 1
  warning: 5
  info: 4
  total: 10
status: issues_found
---

# Phase 07: Code Review Report

**Reviewed:** 2026-04-09
**Depth:** standard
**Files Reviewed:** 22
**Status:** issues_found

## Summary

The admin dashboard is well-structured overall. The auth dependency chain (JWT → `is_admin` DB check) is correct and consistently applied. All SQL queries use parameterized inputs with no injection risk. The audit log is written synchronously in-request as intended. The frontend auth guard correctly redirects non-admins to `/chat` rather than revealing the admin surface.

One critical bug was found: the `cancel_admin_job` endpoint has a TOCTOU (time-of-check/time-of-use) race condition that can trigger a billing record against the wrong job state. Five warnings cover logic bugs that would produce silent incorrect behavior in production (wrong audit silencing, missing UUID validation, Redis connection leak in the SSE limit path, inconsistent cancel error handling, and misleading summary stats). Four info items cover code quality.

---

## Critical Issues

### CR-01: Audit failure silently swallowed after successful cancel, hiding all future write failures

**File:** `backend/admin/router.py:346-355`

**Issue:** The `cancel_admin_job` handler wraps `write_audit` in a bare `except Exception: pass`. The comment says "audit failure must not hide the cancel result," which is reasonable — but the exception is *completely discarded* with no logging. In production, if the DB connection is saturated or the audit table is locked, every admin cancel action will silently produce no audit trail while returning HTTP 200. This is undetectable without separately querying the audit table.

The cancel result is already returned before the audit write, so there is no need to swallow the error: the HTTP response has already been constructed. The fix is to log the exception at ERROR level so operators see it via Sentry/structured logs. This is a critical audit-integrity issue.

**Fix:**
```python
import logging
_log = logging.getLogger(__name__)

try:
    await write_audit(
        admin_id,
        "cancel_job",
        job_id,
        {"gpu_seconds": result["gpu_seconds"], "gpu_cost_usd": result["gpu_cost_usd"]},
    )
except Exception as exc:
    # Do not re-raise — cancel succeeded; audit is best-effort here.
    # But always log so the operator sees this in Sentry/structured logs.
    _log.error("audit write failed after cancel_job %s: %s", job_id, exc)
```

---

## Warnings

### WR-01: `get_job_detail` passes a raw string `job_id` to SQL without UUID validation

**File:** `backend/admin/router.py:256-312`

**Issue:** `GET /admin/jobs/{job_id}` accepts a plain `str` path parameter and passes it directly to `conn.fetchrow(...WHERE j.id = $1", job_id)`. The `id` column is `UUID`. asyncpg will raise an `asyncpg.exceptions.DataError` (invalid UUID) rather than returning a clean 404 when a non-UUID string is passed (e.g., `GET /admin/jobs/not-a-uuid`). This surfaces as an unhandled 500 to the client instead of a 400.

The same issue exists on `POST /admin/jobs/{job_id}/cancel` (line 320).

Both the user-facing `cancel_job` (jobs/router.py:391) and `launch_job_endpoint` (jobs/router.py:104) already validate with `uuid_mod.UUID(...)` — the admin handlers should match.

**Fix:**
```python
import uuid as uuid_mod

@router.get("/jobs/{job_id}")
async def get_job_detail(job_id: str, admin_id: str = Depends(get_current_admin)):
    try:
        uuid_mod.UUID(job_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid job_id — must be a valid UUID")
    ...
```
Apply the same guard to `cancel_admin_job`.

---

### WR-02: Redis connection leak in `_check_sse_limit` when `incr` returns over-limit

**File:** `backend/jobs/router.py:43-51`

**Issue:** In `_check_sse_limit`, when `count > MAX_SSE_PER_USER`, the code calls `await r.decr(key)` and `await r.aclose()` before raising the 429. However, if `r.decr(key)` itself raises (e.g., transient Redis error), `r.aclose()` is never called. The Redis client should be closed in a `finally` block, not inline before the raise. This is a resource-leak pattern that can exhaust the Redis connection pool under error conditions.

**Fix:**
```python
async def _check_sse_limit(user_id: str) -> None:
    r = aioredis.from_url(settings.redis_url)
    try:
        key = f"sse_count:{user_id}"
        count = await r.incr(key)
        await r.expire(key, 300)
        if count > MAX_SSE_PER_USER:
            await r.decr(key)
            raise HTTPException(status_code=429, detail="Too many active connections")
    finally:
        await r.aclose()
```

---

### WR-03: `cancel_job_by_id` in service.py has a TOCTOU race on job status

**File:** `backend/jobs/service.py:58-66`

**Issue:** The service fetches the job row (acquiring its `started_at`, `runpod_job_id`) in one DB connection (line 58-64), then opens a second connection to `UPDATE jobs SET gpu_cost_usd` (line 94-98), then a third to look up the Stripe customer (line 106-109). Between the initial fetch and the update, another request could transition the job to `complete` via the webhook path. The `UPDATE` at line 94-98 does not re-check `status IN ('running', 'queued')`, so it can overwrite a `gpu_cost_usd` that was already set by the webhook completion path.

**Fix:** Add a `WHERE status IN ('running', 'queued')` guard to the `UPDATE` and check the row count to detect the race:
```python
result = await conn.execute(
    "UPDATE public.jobs SET gpu_cost_usd = $1 WHERE id = $2 AND status IN ('running', 'queued')",
    gpu_cost_usd,
    job_id,
)
# result is "UPDATE N" — if N=0, the job transitioned under us; log a warning
```

---

### WR-04: Summary stat cards on Users and Jobs pages show page-scoped counts, not platform totals, with no clear visual distinction

**File:** `frontend/src/pages/admin/AdminUsersPage.tsx:143-148`, `frontend/src/pages/admin/AdminJobsPage.tsx:230-235`

**Issue:** The "Total Users", "Total Platform Revenue", "Running Jobs", and "Failed (24h)" cards are computed from the *current page of results*, not from the full dataset. The label `subLabel="this page"` is present on some cards but not all (e.g., `AdminStatCard label="Running Jobs"` on AdminJobsPage has no subLabel at all, line 253). An operator looking at these cards will reasonably read them as platform-wide totals.

A card showing "Running Jobs: 2" when there are actually 18 running across all pages is operationally misleading and could lead to incorrect incident assessment.

**Fix:** The running/queued counts are already available accurately from `GET /admin/system` (which queries the full jobs table). Either: (a) use the system health data for those specific counts, or (b) add `subLabel="this page"` to all derived stat cards without exception so the scope is always explicit.

---

### WR-05: `handleCancelConfirm` in AdminJobsPage silently swallows cancel errors with no user feedback

**File:** `frontend/src/pages/admin/AdminJobsPage.tsx:214-227`

**Issue:** The cancel confirm handler catches all errors with an empty catch body and the comment "Surface failure silently — operator can retry." If the cancel API call fails (network error, 404, 500), the dialog closes (`setCancelDialogJobId(null)` runs after `cancelAdminJob`... wait — actually the `setCancelDialogJobId(null)` is *inside the try block*, before the catch, so on failure the dialog stays open). The real issue is: the `fetchJobs` refetch on line 221 is called with `void`, and if the cancel fails, no error is shown to the operator at all. The operator clicks "Yes, cancel job", sees the spinner stop, and has no idea if the cancel succeeded or failed.

**Fix:** Set an error state on catch and render it inside the dialog:
```tsx
const [cancelError, setCancelError] = useState<string | null>(null);

const handleCancelConfirm = async () => {
  if (!cancelDialogJobId) return;
  setCancelling(true);
  setCancelError(null);
  try {
    await cancelAdminJob(cancelDialogJobId);
    setCancelDialogJobId(null);
    void fetchJobs(currentCursor, statusFilter, toolFilter, debouncedEmail);
  } catch {
    setCancelError("Cancel failed. The job may have already completed. Refresh to check.");
  } finally {
    setCancelling(false);
  }
};
```
Render `{cancelError && <p className="text-destructive text-sm">{cancelError}</p>}` inside `<DialogContent>`.

---

## Info

### IN-01: `get_job_detail` uses `j.*` SELECT which returns all columns including potentially sensitive ones

**File:** `backend/admin/router.py:278-283`

**Issue:** `SELECT j.*, u.email AS user_email` fetches every column on the jobs table, including `job_token`. The `job_token` is the per-job bearer token used by containers to authenticate presigned URL requests. It is serialized to a dict via `dict(row)` implicitly through asyncpg's Record, but the endpoint's explicit return dict (lines 294-313) does not include `job_token`, so it is not sent to the client. This is safe today but fragile — a future developer adding `**row` or `results` passthrough could accidentally expose the token.

**Fix:** Replace `j.*` with explicit column list, omitting `job_token`, `runpod_job_id`, and any other internal fields not needed by the admin UI.

---

### IN-02: `relativeDate` helper is duplicated in AdminUsersPage and AdminJobsPage

**File:** `frontend/src/pages/admin/AdminUsersPage.tsx:37-51`, `frontend/src/pages/admin/AdminJobsPage.tsx:49-63`

**Issue:** The `relativeDate` function is byte-for-byte identical in both files. Extract to `src/lib/format.ts` or a shared `src/components/admin/utils.ts`.

**Fix:**
```ts
// src/lib/format.ts
export function relativeDate(isoString: string): string { ... }
```

---

### IN-03: Tests lack `@pytest.mark.anyio` (or equivalent) asyncio mode configuration

**File:** `backend/tests/admin/test_dependencies.py:52`, `backend/tests/admin/test_router.py:115`, `backend/tests/admin/test_service.py:77`

**Issue:** All test functions are `async def` but there is no `@pytest.mark.asyncio` decorator and no `pyproject.toml`/`pytest.ini` with `asyncio_mode = auto` visible in the reviewed files. If pytest-asyncio is not configured with auto mode, these tests will be silently collected and *skipped* (or fail with "coroutine was never awaited") rather than run. The tests look correct, but they may never actually execute in CI.

**Fix:** Either add `@pytest.mark.asyncio` to each test function, or confirm `asyncio_mode = "auto"` is set in `pyproject.toml`. Also verify that the `conftest.py` (not in this review scope) is configured correctly.

---

### IN-04: `audit_log` table has no RLS but comment says it relies on "postgres superuser connection"

**File:** `supabase/migrations/20260409000001_admin.sql:5-6`

**Issue:** The comment "No RLS on audit_log — only accessible via admin router through postgres superuser connection" conflates two different things. The admin router uses the same asyncpg connection pool as the rest of the backend. If that pool connects as the `postgres` superuser, it bypasses RLS on *all* tables — not just audit_log. If it connects as a regular role, then the audit_log is accessible to any SQL that runs under that role, including any potential injection elsewhere in the backend.

The migration should explicitly grant SELECT/INSERT only to the backend service role and explicitly deny access to the `anon` and `authenticated` roles used by Supabase's PostgREST layer, regardless of RLS:

```sql
REVOKE ALL ON public.audit_log FROM anon, authenticated;
GRANT SELECT, INSERT ON public.audit_log TO service_role;
```

---

_Reviewed: 2026-04-09_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
