---
phase: 05-production-hardening
plan: 03
subsystem: heartbeat-and-stale-detection
tags: [webhooks, heartbeat, stale-detection, gpu-watchdog, sse-progress]
dependency_graph:
  requires: ["05-01"]
  provides: ["heartbeat-endpoint", "stale-job-watchdog", "container-heartbeat-sender"]
  affects: ["webhooks/router.py", "worker/cleanup.py", "worker/main.py", "run_pipeline.py"]
tech_stack:
  added: []
  patterns: ["container-heartbeat", "stale-job-detection", "progress-sse"]
key_files:
  created:
    - supabase/migrations/20260406000001_heartbeat_columns.sql
  modified:
    - backend/webhooks/router.py
    - backend/worker/cleanup.py
    - backend/worker/main.py
    - docker/rfdiffusion/run_pipeline.py
decisions:
  - "Heartbeat URL derived from webhook URL via string replace (/webhooks/runpod -> /webhooks/heartbeat)"
  - "Stale billing capped at last_heartbeat_at + 10min threshold to avoid charging for hung GPU time"
  - "Stale detection runs every 10 minutes offset by 2 min from orphan cleanup to avoid overlap"
metrics:
  duration: 4min
  completed: "2026-04-07"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 5
---

# Phase 5 Plan 3: Container Heartbeat and Stale Job Detection Summary

Container heartbeat reporting with live progress SSE and 10-minute stale job auto-kill watchdog protecting against stuck GPU billing.

## What Was Built

### Task 1: Heartbeat webhook endpoint and DB migration (f3b084a)
- Created `supabase/migrations/20260406000001_heartbeat_columns.sql` adding `last_heartbeat_at TIMESTAMPTZ` column to jobs table
- Added `POST /webhooks/heartbeat` endpoint to `backend/webhooks/router.py`:
  - Validates RunPod HMAC signature (same as completion webhook)
  - Builds human-readable progress string (e.g., "Running RFdiffusion - 45/100 designs")
  - Skips heartbeats for non-running or unknown jobs
  - Updates `last_heartbeat_at`, `stage`, and `updated_at` in DB
  - Publishes SSE event via Redis pub/sub for live frontend progress

### Task 2: Stale job watchdog and container heartbeat sender (f1de0a7)
- Added `detect_stale_jobs()` to `backend/worker/cleanup.py`:
  - Finds jobs running with no heartbeat for 10+ minutes (or never sent one)
  - Marks stale jobs as failed with `error_category = "Job timed out - no response from GPU"`
  - Caps billed `gpu_seconds` at the stale threshold to avoid charging for hung time
  - Terminates associated RunPod pod
  - Publishes SSE failure event and sends failure notification email
- Registered `detect_stale_jobs` as arq cron job in `backend/worker/main.py` running every 10 minutes (offset by 2 min from orphan cleanup)
- Added `send_heartbeat()` to `docker/rfdiffusion/run_pipeline.py`:
  - Derives heartbeat URL from webhook URL by replacing path segment
  - Called at the start of each pipeline stage with initial progress
  - Called after each per-design iteration in ProteinMPNN and AF2 validation stages
  - Stage functions updated with optional `webhook_url` and `job_id` kwargs (backward compatible)

## Decisions Made

1. **Heartbeat URL derivation:** Container replaces `/webhooks/runpod` with `/webhooks/heartbeat` in the webhook URL rather than requiring a separate env var. Keeps container config simple.
2. **Stale billing cap:** GPU seconds are capped at `reference_time + STALE_HEARTBEAT_SECONDS` minus `started_at`. Users are not billed for hung time beyond 10 minutes after the last heartbeat.
3. **Cron offset:** Stale detection runs at minutes {2, 12, 22, 32, 42, 52} to avoid overlapping with orphan pod cleanup at {0, 10, 20, 30, 40, 50}.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None.

## Verification Results

- `POST /webhooks/heartbeat` endpoint present in router
- `STALE_HEARTBEAT_SECONDS = 600` defined in cleanup module
- `detect_stale_jobs` registered in arq cron_jobs
- `send_heartbeat` function defined and called in all 3 pipeline stages
- Migration file creates `last_heartbeat_at` column

## Self-Check: PASSED

All 5 files found. Both commits (f3b084a, f1de0a7) verified in git log.
