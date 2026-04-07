---
phase: 05-production-hardening
plan: 02
subsystem: payments
tags: [stripe, idempotency, webhooks, billing, security]

# Dependency graph
requires:
  - phase: 03-job-execution-frontend-and-billing
    provides: Stripe billing meter events, webhook handler, cancel_job endpoint
provides:
  - Idempotent Stripe meter events using job_id as idempotency key
  - Webhook replay protection (5-minute timestamp window)
  - Terminal-state guard preventing double-processing of completed jobs
  - UTC timestamp in container webhook payloads
affects: [05-production-hardening, 04-pipeline-validation]

# Tech tracking
tech-stack:
  added: []
  patterns: [stripe-idempotency-key, webhook-replay-protection, terminal-state-guard]

key-files:
  created: []
  modified:
    - backend/billing/stripe_client.py
    - backend/webhooks/router.py
    - backend/jobs/router.py
    - docker/rfdiffusion/run_pipeline.py

key-decisions:
  - "Stripe idempotency key format gpu_usage_{job_id} -- scoped per job, 24hr dedup window"
  - "Malformed timestamps skip replay check rather than rejecting -- backward compatibility with existing webhooks"

patterns-established:
  - "Idempotency key pattern: all Stripe meter events keyed by job_id to prevent double-billing"
  - "Webhook defense-in-depth: signature -> replay check -> terminal guard -> processing"

requirements-completed: []

# Metrics
duration: 4min
completed: 2026-04-07
---

# Phase 5 Plan 2: Billing Idempotency and Webhook Hardening Summary

**Stripe idempotency keys on meter events, webhook replay protection (5-min window), and terminal-state guard preventing double-processing**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-07T03:09:33Z
- **Completed:** 2026-04-07T03:13:33Z
- **Tasks:** 1
- **Files modified:** 4

## Accomplishments
- `record_gpu_usage` now accepts `job_id` and passes `idempotency_key=f"gpu_usage_{job_id}"` to Stripe -- duplicate dispatch produces exactly one billing event
- Webhook replay protection rejects payloads with timestamps older than 5 minutes, guarding against replay attacks
- Terminal-state guard returns early without processing if job is already complete/failed/cancelled, preventing double state updates and billing
- Container `post_webhook` now includes UTC timestamp in payload for replay detection

## Task Commits

Each task was committed atomically:

1. **Task 1: Stripe billing idempotency key and webhook replay protection** - `8ece876` (feat)

**Plan metadata:** (pending)

## Files Created/Modified
- `backend/billing/stripe_client.py` - Added `job_id` parameter and `idempotency_key` to `record_gpu_usage`
- `backend/webhooks/router.py` - Added replay protection (5-min timestamp check), terminal-state guard, updated `record_gpu_usage` caller
- `backend/jobs/router.py` - Updated `record_gpu_usage` caller in `cancel_job` to pass `job_id`
- `docker/rfdiffusion/run_pipeline.py` - Added `datetime` import and UTC timestamp to webhook payload body

## Decisions Made
- Stripe idempotency key format is `gpu_usage_{job_id}` -- simple, unique per job, leverages Stripe's built-in 24-hour deduplication
- Malformed or missing timestamps skip the replay check rather than rejecting the webhook -- ensures backward compatibility with containers that have not yet been updated

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all changes are fully wired with no placeholder data.

## Next Phase Readiness
- Billing integrity hardened: idempotent meter events, replay-protected webhooks, terminal-state guard
- Ready for 05-03 (container heartbeat endpoint, stale job watchdog)
- All 5 pipeline Docker images will need the same timestamp addition to their `post_webhook` calls (currently only RFdiffusion has it)

## Self-Check: PASSED

All files verified present. Commit 8ece876 confirmed in git log.

---
*Phase: 05-production-hardening*
*Completed: 2026-04-07*
