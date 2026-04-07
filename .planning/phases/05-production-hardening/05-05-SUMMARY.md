---
phase: 05-production-hardening
plan: 05
subsystem: monitoring, security
tags: [sentry, gpu-alerting, sse-limiter, input-validation]
dependency_graph:
  requires: [05-01, 05-03]
  provides: [frontend-error-tracking, gpu-spend-alerts, sse-connection-limits]
  affects: [agent-router, jobs-router, worker-cron, frontend-main]
tech_stack:
  added: ["@sentry/react ^8.55.1"]
  patterns: [redis-sse-counter, cron-based-alerting, input-validation-middleware]
key_files:
  created:
    - frontend/src/lib/sentry.ts
  modified:
    - backend/config.py
    - backend/worker/cleanup.py
    - backend/worker/main.py
    - backend/agent/router.py
    - backend/jobs/router.py
    - backend/pdb_utils/router.py
    - frontend/src/main.tsx
    - frontend/src/pages/Login.tsx
    - frontend/src/components/UserMenu.tsx
    - frontend/package.json
decisions:
  - "Sentry APM disabled (tracesSampleRate: 0) for v1 -- error tracking only"
  - "SSE counter uses shared Redis key with 5-min TTL safety net"
  - "GPU spend alert sent to leo@ranomics.com via Resend"
metrics:
  duration: 5min
  completed: "2026-04-07T03:29:00Z"
  tasks: 2
  files: 10
---

# Phase 5 Plan 5: Monitoring, SSE Limits, and Input Validation Summary

Sentry JS SDK for frontend error tracking with user context, Redis-based SSE connection limiter (max 3/user), daily GPU spend alerting via arq cron + Resend email, and input validation hardening across agent/jobs/pdb endpoints.

## Task Results

### Task 1: GPU spend alerting and SSE connection limiter

| Item | Detail |
|------|--------|
| Commit | `4c4bee3` |
| Files | backend/config.py, backend/worker/cleanup.py, backend/worker/main.py, backend/agent/router.py, backend/jobs/router.py, backend/pdb_utils/router.py |

**What was done:**

1. **Config additions:** `gpu_daily_spend_alert_usd` (default $50), `max_sse_connections_per_user` (default 3), `sentry_dsn_frontend` (empty default).

2. **GPU spend alerting:** `check_daily_gpu_spend()` in `worker/cleanup.py` queries jobs table for `SUM(gpu_cost_usd)` over the last 24 hours. If over threshold, sends alert email via Resend to `leo@ranomics.com`. Registered as arq cron job running at 08:00 and 20:00 UTC.

3. **SSE connection limiter:** Redis-based counter (`sse_count:{user_id}`) with INCR/DECR and 5-minute TTL safety net. Applied to both `agent/router.py` (agent message streaming) and `jobs/router.py` (job status streaming). Returns HTTP 429 when limit exceeded. Slot released in `finally` block on stream close.

4. **Input validation:**
   - Agent message: max 10,000 characters (400 on violation)
   - Job launch: UUID format validation on `job_id` (400 on violation)
   - PDB upload: 50MB file size limit (400 on violation)

### Task 2: Sentry frontend SDK integration

| Item | Detail |
|------|--------|
| Commit | `6fe10c2` |
| Files | frontend/src/lib/sentry.ts, frontend/src/main.tsx, frontend/src/pages/Login.tsx, frontend/src/components/UserMenu.tsx, frontend/package.json |

**What was done:**

1. **Sentry init:** Created `frontend/src/lib/sentry.ts` with `initSentry()`, `setSentryUser()`, `clearSentryUser()`. DSN read from `VITE_SENTRY_DSN` env var. Disabled in local dev when DSN is not set. `tracesSampleRate: 0` (no APM for v1).

2. **Entry point:** `initSentry()` called in `main.tsx` before `ReactDOM.createRoot`.

3. **User context:** `setSentryUser(userId, email)` called after successful login in `Login.tsx`. `clearSentryUser()` called on logout in `UserMenu.tsx`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing validation] Added PDB upload file size limit**
- **Found during:** Task 1 (input validation hardening)
- **Issue:** PDB upload endpoint had no file size check
- **Fix:** Added 50MB limit with 400 response on violation
- **Files modified:** backend/pdb_utils/router.py
- **Commit:** 4c4bee3

## Decisions Made

1. **Sentry APM disabled for v1** -- `tracesSampleRate: 0`, error tracking only. No performance monitoring overhead for <100 users.
2. **SSE counter uses shared Redis key** -- `sse_count:{user_id}` with 5-minute TTL as safety net against leaked connections. Same key namespace shared between agent and jobs routers (counts both types together).
3. **GPU spend alert recipient** -- Hardcoded to `leo@ranomics.com` as admin alert. Can be made configurable in a future admin settings plan.

## Known Stubs

None -- all functionality is fully wired.

## Verification

All plan verification checks pass:
- `@sentry/react` in frontend/package.json
- `initSentry` called in frontend/src/main.tsx
- `check_daily_gpu_spend` registered in worker cron
- `MAX_SSE_PER_USER` in agent/router.py
- `sentry_dsn_frontend` in backend/config.py

## Self-Check: PASSED

All 10 files verified present. Both commits (4c4bee3, 6fe10c2) verified in git log.
