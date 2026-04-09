---
phase: 07-admin-dashboard
plan: 02
subsystem: api-tests
tags: [pytest, fastapi, asyncpg, admin, testing, dependency_overrides]

# Dependency graph
requires:
  - phase: 07-admin-dashboard
    plan: 01
    provides: get_current_admin dependency, admin router (7 endpoints), cancel_job_by_id service, write_audit helper

provides:
  - test scaffold for admin auth dependency (3 tests)
  - test scaffold for all 7 admin router endpoints (10 tests)
  - test scaffold for shared cancel service (3 tests)

affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Rate limit bypass: set limiter.enabled=False at module scope — slowapi connects to Redis even in test mode"
    - "Dependency override fixture: app.dependency_overrides[get_current_admin] = _mock_admin set/torn down per test via pytest fixture"
    - "Pool mock pattern: MagicMock(return_value=_make_ctx(conn)) — acquire() must return a sync context manager, not a coroutine"
    - "Multi-acquire pool: MagicMock(side_effect=[ctx1, ctx2, ctx3]) for endpoints making multiple DB calls"

key-files:
  created:
    - backend/tests/admin/__init__.py
    - backend/tests/admin/test_dependencies.py
    - backend/tests/admin/test_router.py
    - backend/tests/admin/test_service.py
  modified: []

key-decisions:
  - "limiter.enabled=False set at module import time in test_router.py — rate limit middleware is always registered (rate_limit_enabled defaults True), cannot gate on TESTING env after main.py loads"
  - "Pool mock uses MagicMock (not AsyncMock) for acquire() — AsyncMock.acquire() returns a coroutine which does not support 'async with'; MagicMock returns the ctx object directly"
  - "write_audit patched at admin.router module level — avoids needing a real DB for the audit INSERT on every test"
  - "test_service.py calls cancel_job_by_id directly (not via HTTP) — tests the service function in isolation, not the router layer"

# Metrics
duration: ~15min
completed: 2026-04-09
---

# Phase 7 Plan 02: Admin Test Scaffolds Summary

**Test scaffolds for all admin backend functionality: 16 passing tests covering auth dependency, 7 router endpoints, and shared cancel service.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-04-09
- **Completed:** 2026-04-09
- **Tasks:** 2
- **Files created:** 4

## Accomplishments

- `tests/admin/test_dependencies.py` — 3 tests validate get_current_admin: non-admin gets 403 with "Forbidden" detail (not "Not admin"), admin user_id returned on pass-through, missing user also gets 403
- `tests/admin/test_router.py` — 10 tests cover all 7 endpoint groups (users, jobs, cancel, revenue, system, audit) plus audit-on-view assertion; uses `app.dependency_overrides[get_current_admin]` per established Phase 03 pattern
- `tests/admin/test_service.py` — 3 tests for `cancel_job_by_id`: success returns `status='cancelled'` with `gpu_seconds > 0`, not-found raises 404, billing test asserts `record_gpu_usage` called with correct `gpu_seconds`
- All 16 tests pass

## Task Commits

1. **Task 1: Admin test package and dependency tests** — `c98ced9`
2. **Task 2: Admin router endpoint tests and cancel service tests** — `23b1c12`

## Files Created

- `backend/tests/admin/__init__.py` — empty test package init
- `backend/tests/admin/test_dependencies.py` — 3 admin auth dependency tests
- `backend/tests/admin/test_router.py` — 10 admin endpoint tests (all 7 endpoint groups)
- `backend/tests/admin/test_service.py` — 3 cancel service tests

## Decisions Made

- Pool mock pattern: `pool.acquire = MagicMock(return_value=_make_ctx(conn))` where `_make_ctx` wraps a connection in an `AsyncMock` context manager. Using `AsyncMock` for `acquire()` itself causes a "coroutine does not support async context manager" error — the call must return the ctx object synchronously.
- Rate limiting bypass: `limiter.enabled = False` set at module scope in test_router.py. The slowapi middleware is registered unconditionally in main.py (only gated by `rate_limit_enabled` at startup), and in test mode there is no Redis available. Setting `limiter.enabled = False` at import time suppresses the Redis connection attempt on each request.
- `write_audit` patched at `admin.router.write_audit` (not `admin.audit.write_audit`) — patches the reference in the module where it is called, per standard Python patching convention.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Rate limiting middleware caused ConnectionError in tests**
- **Found during:** Task 2
- **Issue:** `slowapi` middleware connects to Redis on every request. `RATE_LIMIT_ENABLED` defaults to `True` and `main.py` is already imported by `conftest.py` before the test file can set env vars. The SlowAPI error handler also has a bug (`ConnectionError` lacks a `.detail` attribute) that surfaces as `AttributeError`.
- **Fix:** `from middleware.rate_limit import limiter as _limiter; _limiter.enabled = False` at module scope in `test_router.py`.
- **Files modified:** `backend/tests/admin/test_router.py`
- **Commit:** `23b1c12`

## Known Stubs

None — test files contain no hardcoded stub values or placeholder data. All mock return values are representative test data.

## Threat Flags

None — test-only plan; no new production trust boundaries introduced.

## Self-Check: PASSED

Files confirmed to exist:
- backend/tests/admin/__init__.py — FOUND
- backend/tests/admin/test_dependencies.py — FOUND
- backend/tests/admin/test_router.py — FOUND
- backend/tests/admin/test_service.py — FOUND

Commits confirmed:
- c98ced9 — FOUND (Task 1)
- 23b1c12 — FOUND (Task 2)

---
*Phase: 07-admin-dashboard*
*Completed: 2026-04-09*
