---
phase: 03
plan: 04
subsystem: frontend
tags: [frontend, job-status, sse, components, billing-display, react]
dependency_graph:
  requires: ["03-02", "03-03"]
  provides: ["job-status-page", "job-sse-client", "job-components"]
  affects: ["frontend/src/App.tsx"]
tech_stack:
  added: []
  patterns:
    - "fetch + ReadableStream SSE (mirrors agent.ts pattern)"
    - "shadcn Card > CardHeader > CardContent"
    - "Regex markdown renderer (no library)"
    - "AbortController for SSE cleanup on unmount"
key_files:
  created:
    - frontend/src/lib/jobs.ts
    - frontend/src/pages/JobPage.tsx
    - frontend/src/components/jobs/JobStatusCard.tsx
    - frontend/src/components/jobs/RunSummaryCard.tsx
    - frontend/src/components/jobs/CandidateCard.tsx
    - frontend/src/components/jobs/NextStepsCard.tsx
    - frontend/src/components/jobs/JobCompletionCard.tsx
    - frontend/src/components/jobs/BindCraftZeroOutputCard.tsx
    - frontend/src/components/jobs/JobFailureCard.tsx
    - frontend/src/components/jobs/ExpiryWarningBanner.tsx
  modified:
    - frontend/src/App.tsx
decisions:
  - "SSE subscription uses AbortController.abort() for cleanup on unmount — avoids leaked streams when user navigates away"
  - "BindCraftZeroOutputCard uses no destructive colors — zero-output is expected BindCraft behavior, not failure"
  - "JobStatusCard inline cancel confirm (not modal) — keeps user in context per UI-SPEC"
  - "ExpiryWarningBanner returns null when not within 7-day window — no conditional needed at call site"
  - "JobPage re-fetches full job on terminal SSE event — ensures candidates and billing data load correctly"
metrics:
  duration: "4 min"
  completed_date: "2026-03-19"
  tasks_completed: 2
  files_changed: 11
---

# Phase 03 Plan 04: Job Frontend Components Summary

Job status page and all job-related components: real-time SSE job tracking, results display with tool-native scores, download, cancellation, and billing display across 8 components and JobPage.

## Tasks Completed

| # | Task | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Job SSE client, API functions, route registration | 479a711 | frontend/src/lib/jobs.ts, frontend/src/App.tsx |
| 2 | All job components and JobPage | ab2ae21 | frontend/src/pages/JobPage.tsx, frontend/src/components/jobs/*.tsx |

## What Was Built

### frontend/src/lib/jobs.ts

SSE client and API functions mirroring the `agent.ts` pattern:
- `subscribeToJobStatus` — fetch + ReadableStream SSE with AbortController cleanup
- `getJob`, `getJobList`, `cancelJob` — authenticated job API calls with cookie credentials
- `downloadAllDesignsUrl` — URL builder for zip download (used with `window.open`)
- `getCostEstimate`, `getPaymentStatus`, `createCheckoutSession` — billing API
- All POST requests include `X-CSRFToken` header from cookie

### frontend/src/pages/JobPage.tsx

Route `/jobs/:id`. Conditional rendering by job status:
1. ExpiryWarningBanner — within 7 days of 30-day R2 expiry
2. JobStatusCard — always; SSE subscription active when running/queued
3. RunSummaryCard — complete or cancelled
4. "Design candidates" + BindCraftZeroOutputCard or CandidateCard list — complete
5. JobFailureCard — failed
6. NextStepsCard — complete with next_steps
7. "Previous jobs" history list

SSE subscription starts on mount, cleans up on unmount. On terminal SSE event, re-fetches full job data to load candidates and billing.

### Components

- **JobStatusCard**: Stage progress row (Queued → Initializing GPU → Running [tool] → Scoring designs → Complete), status badge, inline cancel confirmation with exact UI-SPEC copy
- **RunSummaryCard**: Tool + date header, key-value grid, GPU cost in font-mono, collapsible parameters, Download all designs button
- **CandidateCard**: Tool-native score grid (pAE/pLDDT for RFdiffusion/RFantibody, binding_energy/iPAE for BindCraft, confidence for BoltzGen), Download PDB button
- **NextStepsCard**: Regex markdown renderer, no interactive elements
- **JobCompletionCard**: Chat thread card — summary row + View full results link
- **BindCraftZeroOutputCard**: No destructive colors, exact UI-SPEC body copy, agent guidance
- **JobFailureCard**: border-destructive/40, failure category label, no retry button
- **ExpiryWarningBanner**: shadcn Alert, exact UI-SPEC copy, calculated from completedAt + 30 days

## Deviations from Plan

None — plan executed exactly as written. All copy matches UI-SPEC verbatim. No deferred features added (no Mol* viewer, no sort/filter, no retry button).

## Self-Check

- [x] frontend/src/lib/jobs.ts — created, exports all required functions
- [x] frontend/src/pages/JobPage.tsx — created, contains subscribeToJobStatus, max-w-3xl, space-y-6, Previous jobs
- [x] All 8 component files created in frontend/src/components/jobs/
- [x] Route /jobs/:id registered in App.tsx
- [x] TypeScript compiles without errors (npx tsc --noEmit: clean)
- [x] Commits 479a711 and ab2ae21 exist

## Self-Check: PASSED
