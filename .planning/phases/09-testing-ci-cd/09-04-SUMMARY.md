---
phase: 09-testing-ci-cd
plan: "04"
subsystem: ci-cd
tags: [github-actions, ci, cd, playwright, pytest, vitest, supabase, smoke-test]
dependency_graph:
  requires:
    - 09-01 (backend tests exist for pytest gate)
    - 09-02 (vitest scripts exist for frontend-unit gate)
    - 09-03 (playwright config + e2e specs exist for e2e gate)
  provides: [test.yml PR gate, smoke.yml post-deploy check, frontend/e2e/smoke.spec.ts]
  affects: []
tech_stack:
  added: []
  patterns:
    - "4-job CI pipeline with concurrency group to cancel stale PR runs"
    - "supabase/setup-cli@v1 in both backend and e2e jobs for real DB"
    - "e2e job depends on backend + frontend-unit (fail-fast on unit errors)"
    - "workflow_dispatch smoke test triggered manually post-deploy"
    - "SMOKE_TARGET_URL env var to point Playwright smoke spec at production URL"
key_files:
  created:
    - .github/workflows/test.yml
    - .github/workflows/smoke.yml
    - frontend/e2e/smoke.spec.ts
  modified: []
decisions:
  - "smoke.yml uses workflow_dispatch (not push) — triggered manually after deploy per D-13; no automated rollback per D-12"
  - "e2e job has needs: [backend, frontend-unit] — saves CI compute by not running E2E when unit tests already fail"
  - "SUPABASE_SERVICE_KEY passed as GitHub Secret, not hardcoded — per D-10 and T-09-06 mitigate disposition"
  - "test@example.com smoke test credential accepted per T-09-07 (seed account, no real data)"
metrics:
  duration_minutes: 2
  completed_date: "2026-04-10"
  tasks_completed: 2
  files_created: 3
  files_modified: 0
requirements_completed: [TEST-05, TEST-06, TEST-07]
---

# Phase 09 Plan 04: CI/CD Pipeline — Summary

GitHub Actions CI pipeline (test.yml) with 4 PR gates and post-deploy smoke test workflow (smoke.yml) with dedicated smoke.spec.ts. All 5 existing Docker build workflows verified as valid YAML.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create test.yml CI pipeline with 4 gates and verify Docker workflows | 3c762b7 | .github/workflows/test.yml |
| 2 | Create smoke.yml post-deploy workflow with smoke.spec.ts | 7f968d5 | .github/workflows/smoke.yml, frontend/e2e/smoke.spec.ts |

## What Was Built

### test.yml — PR Gate Workflow

4-job pipeline triggered on every `pull_request` to `main` or `master`. Concurrency group cancels stale runs on the same PR branch.

**backend job:** Checks out repo, starts Supabase via `supabase/setup-cli@v1`, installs Python 3.11 deps, runs `pytest tests/ --cov=. --cov-fail-under=80 --cov-report=xml`. Coverage XML uploaded as artifact. SUPABASE_SERVICE_KEY from GitHub Secrets.

**frontend-unit job:** Installs Node 20, runs `npm ci`, runs `npm run test` (Vitest). Caches `node_modules` by `package-lock.json` hash.

**e2e job:** `needs: [backend, frontend-unit]` — only runs after unit tests pass. Starts Supabase, installs both Python and Node deps, installs Playwright Chromium, starts uvicorn backend, runs `npx playwright test`.

**lint job:** Runs in parallel with backend/frontend-unit. Installs `ruff` and runs `ruff check backend/`. Also runs `npx tsc --noEmit` and `npx eslint .` on the frontend.

### smoke.yml — Post-Deploy Smoke Test

Triggered via `workflow_dispatch` with a required `environment` input (the production URL). Runs 4 sequential checks:

1. **Health 200:** `curl -o /dev/null -w "%{http_code}"` hits `{environment}/health` — must return 200.
2. **Response time <2s:** `curl -w "%{time_total}"` to `/health` — fails if >2.0s.
3. **Auth flow:** `curl -X POST {environment}/auth/login` with test@example.com — must return 200.
4. **Frontend Playwright:** `npx playwright test e2e/smoke.spec.ts` with `SMOKE_TARGET_URL` env var set to the production URL.

Failure step emits `::error::` annotation with manual rollback guidance (Railway/Vercel deploy history) and reference to Sentry.

### smoke.spec.ts — Frontend Smoke Spec

Playwright test navigating to `{SMOKE_TARGET_URL}/login`, asserting the page title is truthy (React mounted) and a `form` element is visible (login page rendered). Reads production URL from `SMOKE_TARGET_URL` env var.

### Docker Workflow Verification (TEST-06)

All 5 pre-existing Docker build workflows verified present and valid YAML:
- `.github/workflows/docker-rfdiffusion.yml` — VALID
- `.github/workflows/docker-bindcraft.yml` — VALID
- `.github/workflows/docker-rfantibody.yml` — VALID
- `.github/workflows/docker-boltzgen.yml` — VALID
- `.github/workflows/docker-pxdesign.yml` — VALID

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — workflow files are complete configurations; smoke.spec.ts is a functional test, not a placeholder.

## Threat Flags

No new network endpoints or auth paths. Security notes:
- T-09-06 mitigated: secrets accessed via `${{ secrets.* }}` syntax (GitHub masks in logs).
- T-09-07 accepted: test@example.com smoke credential is a seed account with no real data.
- T-09-08 mitigated: no `permissions: write` escalation in test.yml; workflows use default read-only permissions.

## Self-Check

Files created:
- .github/workflows/test.yml — FOUND
- .github/workflows/smoke.yml — FOUND
- frontend/e2e/smoke.spec.ts — FOUND

Commits:
- 3c762b7 — Task 1 (test.yml)
- 7f968d5 — Task 2 (smoke.yml, smoke.spec.ts)

## Self-Check: PASSED
