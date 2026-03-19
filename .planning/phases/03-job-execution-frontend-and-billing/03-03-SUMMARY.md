---
phase: 03-job-execution-frontend-and-billing
plan: 03
subsystem: api
tags: [arq, redis, runpod, sse, s3, resend, stripe, asyncpg, fastapi]

requires:
  - phase: 03-job-execution-frontend-and-billing
    provides: GPUProvider ABC, RunPodProvider, billing stripe_client, JobSpec, JobStatus models, DB schema

provides:
  - Job execution pipeline: dispatch (DB-first) → arq worker → RunPod → webhook → SSE → email
  - SSE status stream endpoint (GET /jobs/{id}/status) with Redis pub/sub fan-out
  - Job cancel endpoint with partial GPU billing
  - ZIP download endpoint for all design outputs
  - Job list and single-job detail endpoints with presigned S3 GET URLs
  - Email notifications on completion and failure via Resend
  - Worker docker service (arq worker.main.WorkerSettings)

affects: [03-04, 03-05, frontend job dashboard, any plan consuming job results]

tech-stack:
  added: [arq, redis.asyncio, resend, zipfile (stdlib)]
  patterns:
    - DB write (status=queued) before any GPU API call (BILL-04 compliance)
    - Worker idempotency check on runpod_job_id before submit_job
    - Redis pub/sub channel job:{id}:status for SSE fan-out
    - app.dependency_overrides for FastAPI auth mocking in tests
    - Separate router/worker pool mocks to prevent side_effect StopIteration

key-files:
  created:
    - backend/jobs/dispatch.py
    - backend/jobs/router.py
    - backend/jobs/notifications.py
    - backend/worker/main.py
    - backend/worker/tasks.py
    - backend/webhooks/router.py
    - backend/tests/jobs/test_cancel.py
    - backend/tests/jobs/test_dispatch.py
    - backend/tests/jobs/test_download.py
    - backend/tests/jobs/test_notifications.py
    - backend/tests/jobs/test_results.py
    - backend/tests/jobs/test_status_stream.py
    - backend/tests/gpu/test_runpod_provider.py
  modified:
    - backend/storage/client.py
    - backend/main.py
    - docker-compose.yml

key-decisions:
  - "jobs_router registration was missing from main.py in initial commit — added in task 2"
  - "FastAPI dependency mocking requires app.dependency_overrides not unittest.mock.patch — patch only replaces the function object, not the bound dependency"
  - "Router pool and worker pool must be separate mock objects — shared pool's side_effect iterator is consumed by both callers causing StopIteration"
  - "cancel_job makes exactly 3 router pool acquires: job fetchrow, gpu_cost_usd execute, stripe fetchrow; update_job_status uses worker.tasks.get_db_pool separately"

requirements-completed: [JOB-01, JOB-02, JOB-03, RESULT-01, RESULT-02, RESULT-03, BILL-04]

duration: 35min
completed: 2026-03-19
---

# Phase 03 Plan 03: Job Execution Pipeline Summary

**Full job execution pipeline: DB-first dispatch, arq worker with RunPod submit, webhook-triggered billing and email, SSE status streaming, cancel with partial billing, and ZIP download — 19 tests green**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-03-19 (continuation from interrupted execution)
- **Completed:** 2026-03-19
- **Tasks:** 2 (Task 1 committed in prior session as 5f01bbf; Task 2 completed here)
- **Files modified:** 14

## Accomplishments

- Complete job lifecycle: dispatch → arq worker → RunPod → webhook callbacks → SSE stream closes on terminal state
- Email notifications via Resend on job completion and failure, with job URL and design count
- Cancel endpoint calls RunPodProvider.cancel_job, calculates partial GPU seconds, records Stripe meter event, and publishes SSE terminal event
- ZIP download streams all S3 outputs (ranked PDBs + report.txt) for completed jobs
- 19 tests across jobs/ and gpu/ test directories — all passing, no skips

## Task Commits

1. **Task 1: Dispatch, worker, webhooks, and presigned URL extension** - `5f01bbf` (feat)
2. **Task 2: Job router, SSE, cancel, download, notifications, all tests** - `45ef797` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified

- `backend/jobs/dispatch.py` — launch_job: DB write (status=queued) then arq enqueue_job
- `backend/jobs/router.py` — SSE stream, cancel, download, list/get job endpoints
- `backend/jobs/notifications.py` — send_completion_email, send_failure_email via Resend
- `backend/worker/main.py` — arq WorkerSettings with run_job function
- `backend/worker/tasks.py` — run_job (idempotent), update_job_status, publish_status
- `backend/webhooks/router.py` — HMAC-validated RunPod webhook: status, billing, notifications
- `backend/storage/client.py` — added generate_presigned_put_url, generate_presigned_get_url
- `backend/main.py` — registered webhooks_router and jobs_router
- `docker-compose.yml` — added worker service running arq worker.main.WorkerSettings
- `backend/tests/jobs/test_*.py` — 15 tests across 5 files
- `backend/tests/gpu/test_runpod_provider.py` — 4 RunPodProvider tests

## Decisions Made

- `jobs_router` was missing from `main.py` in Task 1 — added in Task 2; caused all endpoint tests to 404
- FastAPI `Depends()` resolution requires `app.dependency_overrides` for testing — `unittest.mock.patch` on the function object does not intercept FastAPI's dependency injection
- Router and worker DB pool mocks must be distinct objects — the cancel endpoint uses both `jobs.router.get_db_pool` and `worker.tasks.get_db_pool`; patching both to the same mock with a side_effect list caused StopIteration when the iterator was shared
- cancel_job acquires 3 contexts from the router pool in sequence (job fetchrow, gpu_cost_usd execute, stripe fetchrow); update_job_status acquires independently from the worker pool

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Missing jobs_router registration in main.py**
- **Found during:** Task 2 (first test run)
- **Issue:** Task 1 registered `webhooks_router` but omitted `jobs_router`, causing all /jobs/* endpoints to return 404
- **Fix:** Added `from jobs.router import router as jobs_router` and `app.include_router(jobs_router)` to main.py
- **Files modified:** backend/main.py
- **Verification:** All endpoint tests returned 200 after fix
- **Committed in:** 45ef797

**2. [Rule 1 - Bug] Test patch targets used wrong module path**
- **Found during:** Task 2 (test execution)
- **Issue:** Tests patched `db.connection.get_db_pool` but router imports via `from db.connection import get_db_pool` — patch must target `jobs.router.get_db_pool` to intercept the bound name
- **Fix:** Updated all test patches to target `jobs.router.get_db_pool`
- **Files modified:** test_cancel.py, test_download.py, test_status_stream.py
- **Committed in:** 45ef797

**3. [Rule 1 - Bug] FastAPI dependency mocking via patch() bypassed by Depends()**
- **Found during:** Task 2 (401 responses on all auth-protected endpoints)
- **Issue:** `patch("auth.dependencies.get_current_user", return_value="user-id")` does not override FastAPI's dependency injection — Depends() resolves the callable at request time via the overrides registry, not via the module attribute
- **Fix:** Replaced all `patch(...)` auth overrides with `app.dependency_overrides[get_current_user] = lambda: "user-id"` with try/finally cleanup
- **Files modified:** test_cancel.py, test_download.py, test_status_stream.py
- **Committed in:** 45ef797

**4. [Rule 1 - Bug] Shared mock pool side_effect exhausted by dual get_db_pool callers**
- **Found during:** Task 2 (StopIteration on pool.acquire in cancel tests)
- **Issue:** cancel_job imports update_job_status from worker.tasks — both callers used the same patched pool object, consuming the side_effect list faster than anticipated
- **Fix:** Introduced separate `_make_router_pool` and `_make_worker_pool` helpers; each patch target gets its own pool mock
- **Files modified:** test_cancel.py
- **Committed in:** 45ef797

---

**Total deviations:** 4 auto-fixed (all Rule 1 — bugs in test scaffolding and missing router registration)
**Impact on plan:** All fixes were necessary for tests to exercise the actual code. No scope creep.

## Issues Encountered

- Pyright IDE diagnostics flagged all backend imports in test files as missing — false positives from `pyrightconfig.json` not declaring `backend/` as a search root. Runtime (pytest with `rootdir: backend/`) is correct. No action taken — out of scope.

## Next Phase Readiness

- Full job execution backend is complete and tested
- SSE stream, cancel, download, and notification endpoints are ready to connect to frontend (Plan 03-04)
- Worker service is dockerized and ready for deployment
- Billing meter events fire correctly on completion and cancellation

---
*Phase: 03-job-execution-frontend-and-billing*
*Completed: 2026-03-19*
