---
phase: 05-production-hardening
plan: 04
subsystem: jobs/uploads
tags: [security, presigned-urls, job-token, container-auth]
dependency_graph:
  requires: [05-02]
  provides: [on-demand-upload-urls, job-token-auth]
  affects: [jobs-router, worker-tasks, runpod-provider, container-pipeline]
tech_stack:
  added: []
  patterns: [job-token-auth, on-demand-presigned-urls]
key_files:
  created:
    - supabase/migrations/20260406000002_job_token.sql
  modified:
    - backend/jobs/router.py
    - backend/worker/tasks.py
    - backend/config.py
    - backend/gpu/runpod.py
    - docker/rfdiffusion/run_pipeline.py
decisions:
  - "Job token is secrets.token_urlsafe(32), stored in DB, passed as JOB_TOKEN env var"
  - "Upload URL expiry is 3600s (1 hour), configurable via upload_url_expiry_seconds"
  - "Filename sanitization strips / and \\ to prevent path traversal"
metrics:
  duration: 4min
  completed: "2026-04-07T03:22:00Z"
---

# Phase 5 Plan 4: On-Demand Upload URLs Summary

Replaced pre-generated presigned upload URLs with on-demand URL generation authenticated by per-job tokens, eliminating URL expiry issues for long-running jobs.

## What Was Built

### POST /jobs/{job_id}/upload-urls Endpoint
- Accepts `{"filenames": ["design_001.pdb", "metrics.csv"]}`, returns presigned PUT URLs
- Authenticated via `Authorization: Bearer {job_token}` header (not user JWT)
- Validates job exists, token matches, and job is in `running` state
- Returns 401 for missing/invalid tokens, 404 for missing jobs, 409 for non-running jobs
- Sanitizes filenames by stripping `/` and `\` to prevent path traversal attacks

### Job Token Generation (worker/tasks.py)
- Generates `secrets.token_urlsafe(32)` per job before pod creation
- Stores token in `job_token` column on the jobs table
- Passes token to container via `JOB_TOKEN` env var (through RunPod pod creation)
- Includes `upload_urls_endpoint` in the job payload so container knows where to POST

### Container Refactor (run_pipeline.py)
- Added `request_upload_urls()` function that calls the backend endpoint with Bearer auth
- Builds filename list from passing designs, requests all URLs in a single batch call
- Uploads each PDB and metrics.csv using the fresh presigned URLs
- Gracefully handles URL request failures (logs error, continues pipeline)

### Removed
- Pre-generated `output_presigned_urls` list from dispatch payload
- Pre-generated `report_presigned_url` from dispatch payload
- `generate_presigned_put_url` import from worker/tasks.py

## Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | On-demand upload URL endpoint with job token auth | 602f932 | jobs/router.py, worker/tasks.py, config.py, runpod.py, migration |
| 2 | Container refactored to use on-demand upload URLs | 6bf64a6 | run_pipeline.py |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None.
