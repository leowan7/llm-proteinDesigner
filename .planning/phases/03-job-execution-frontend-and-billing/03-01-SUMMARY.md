---
phase: 03-job-execution-frontend-and-billing
plan: 01
subsystem: api
tags: [stripe, runpod, gpu, billing, pydantic, asyncpg, httpx]

requires:
  - phase: 02-agent-and-structure-input
    provides: JobSpec Pydantic model and ValidationResult from agent/jobspec.py

provides:
  - GPUProvider ABC with submit_job, get_status, cancel_job, get_results abstract methods
  - RunPodProvider concrete implementation using httpx.AsyncClient
  - JobStatus/JobStage enums and JobStatusEvent, JobResult, CandidateResult Pydantic models
  - Stripe billing client (get_or_create_customer, create_setup_session, create_portal_session, check_payment_method, record_gpu_usage)
  - Cost estimator with per-tool runtime ranges and markup-aware pricing
  - DB migration adding stripe_customer_id, job execution columns, and job_candidates table
  - 10 test scaffold files covering all Phase 3 requirements (JOB-01 through RESULT-03, BILL-01 through BILL-04)

affects: [03-02-billing-api, 03-03-job-dispatch-and-webhooks, 03-04-frontend, 03-05-e2e]

tech-stack:
  added: [stripe==14.4.1, runpod==1.8.1, resend==2.25.0, arq==0.27.0]
  patterns:
    - GPUProvider ABC isolates provider-specific logic behind a stable interface
    - RunPod results embedded in status endpoint (no separate results endpoint)
    - Stripe Billing Meters API uses string value field (not int) for gpu_seconds

key-files:
  created:
    - supabase/migrations/20260319000002_billing_and_results.sql
    - backend/gpu/provider.py
    - backend/gpu/runpod.py
    - backend/jobs/models.py
    - backend/billing/stripe_client.py
    - backend/billing/estimate.py
    - backend/tests/jobs/test_status_stream.py
    - backend/tests/jobs/test_notifications.py
    - backend/tests/jobs/test_cancel.py
    - backend/tests/jobs/test_download.py
    - backend/tests/jobs/test_results.py
    - backend/tests/jobs/test_dispatch.py
    - backend/tests/billing/test_meter.py
    - backend/tests/billing/test_estimate.py
    - backend/tests/billing/test_payment_gate.py
    - backend/tests/gpu/test_runpod_provider.py
  modified:
    - backend/config.py
    - backend/requirements.txt

key-decisions:
  - "GPUProvider ABC defines 4 abstract methods (submit_job, get_status, cancel_job, get_results) — get_results preserves interface for Phase 4 Modal integration"
  - "RunPod get_results re-fetches the status endpoint and returns the output field — RunPod has no separate results endpoint"
  - "Stripe MeterEvent payload value must be str(gpu_seconds), not int — Stripe Billing Meters API rejects integer values"
  - "estimate_cost_range scales by max(1, num_designs/10) — batch sizes up to 10 run concurrently with minimal overhead"

patterns-established:
  - "GPUProvider ABC pattern: all GPU providers implement the same 4-method interface enabling provider swap without changing dispatch logic"
  - "Test stub pattern: pytest.skip('STUB -- implementation in Plan 03-XX') with target plan number for all scaffolded tests"
  - "Stripe customer ID cached in users.stripe_customer_id to avoid redundant API calls"

requirements-completed: [JOB-01, JOB-02, JOB-03, RESULT-01, RESULT-02, RESULT-03, BILL-01, BILL-02, BILL-03, BILL-04]

duration: 15min
completed: 2026-03-19
---

# Phase 3 Plan 1: Foundation Layer Summary

**GPUProvider ABC + RunPodProvider, Stripe billing client with Meters API, cost estimator, DB migration for billing/results schema, and 10 Phase-3 test scaffolds covering all 10 requirements**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-19T18:20:00Z
- **Completed:** 2026-03-19T18:35:00Z
- **Tasks:** 3
- **Files modified:** 18

## Accomplishments

- DB migration establishes billing schema: stripe_customer_id on users, 8 execution columns on jobs, and job_candidates table with RLS policy
- Type contract layer complete: GPUProvider ABC (4 abstract methods), RunPodProvider (httpx-based), all job/billing Pydantic models, Stripe client, cost estimator
- 10 test scaffold files created covering all Phase 3 requirements; 2 estimate tests run green immediately, 23 stubs skip cleanly

## Task Commits

1. **Task 0: Scaffolding** - `907d7b2` (chore)
2. **Task 1: Type contracts** - `9e5bb1d` (feat)
3. **Task 2: Test scaffolds** - `151f05a` (test)

## Files Created/Modified

- `supabase/migrations/20260319000002_billing_and_results.sql` - Billing and results DB schema
- `backend/gpu/provider.py` - GPUProvider ABC with GPUJobSubmission and GPUJobStatus dataclasses
- `backend/gpu/runpod.py` - RunPodProvider implementing GPUProvider using httpx.AsyncClient
- `backend/jobs/models.py` - JobStatus/JobStage enums, TOOL_STAGE_MAP, JobStatusEvent, CandidateResult, JobResult
- `backend/billing/stripe_client.py` - 5 Stripe functions including record_gpu_usage with Meters API
- `backend/billing/estimate.py` - estimate_cost_range with per-tool runtime ranges and markup
- `backend/config.py` - Added Stripe, RunPod, Resend, and GPU pricing config fields
- `backend/requirements.txt` - Added stripe, runpod, resend, arq
- `backend/gpu/__init__.py`, `backend/jobs/__init__.py`, `backend/billing/__init__.py`, `backend/worker/__init__.py`, `backend/webhooks/__init__.py` - Package init files
- 10 test scaffold files in tests/jobs/, tests/billing/, tests/gpu/

## Decisions Made

- RunPod get_results re-fetches the status endpoint because RunPod embeds output in the status response (no separate results endpoint). The method signature is preserved for providers (Modal) that do have a dedicated results endpoint.
- Stripe MeterEvent value must be `str(gpu_seconds)` — the Stripe Billing Meters API rejects integer values; this is documented in the function docstring and enforced in the test scaffold.
- Cost scaling uses `max(1, num_designs / 10)` — reflects that batches up to 10 designs run concurrently; beyond 10 cost scales linearly.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Python executable on this machine is at `/c/Users/lab/AppData/Local/Programs/Python/Python313/python.exe` (not `python` or `python3` in PATH). Used full path for all verification commands. This is a local environment issue and does not affect Docker-based execution.

## User Setup Required

None - no external service configuration required at this stage. Stripe and RunPod credentials are config fields with empty string defaults; they will be needed when Plans 03-02 and 03-03 are executed.

## Next Phase Readiness

- All Phase 3 type contracts are in place; Plans 03-02 and 03-03 can import from gpu.*, jobs.*, billing.* without modification
- Test scaffolds provide clear targets for each plan's implementation work
- DB migration is ready to apply when the Supabase stack is running

---
*Phase: 03-job-execution-frontend-and-billing*
*Completed: 2026-03-19*
