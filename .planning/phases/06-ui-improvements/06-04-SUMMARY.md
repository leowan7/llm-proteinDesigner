---
phase: 06-ui-improvements
plan: "04"
subsystem: frontend
tags: [ui, jobs, history, pagination, accessibility, onboarding]
dependency_graph:
  requires: ["06-02"]
  provides: ["job-history-page", "status-badge", "list-jobs-api", "greeting-card-prompts"]
  affects: ["frontend/src/pages/JobHistoryPage.tsx", "frontend/src/lib/jobs.ts", "frontend/src/components/chat/GreetingCard.tsx"]
tech_stack:
  added: ["shadcn table component"]
  patterns: ["keyset pagination with cursor stack", "sr-only accessibility text", "injected input value via prop"]
key_files:
  created:
    - frontend/src/pages/JobHistoryPage.tsx
    - frontend/src/components/common/StatusBadge.tsx
    - frontend/src/components/ui/table.tsx
  modified:
    - frontend/src/lib/jobs.ts
    - frontend/src/components/chat/GreetingCard.tsx
    - frontend/src/components/chat/ChatPage.tsx
    - frontend/src/components/chat/ChatInput.tsx
    - frontend/src/components/chat/MessageList.tsx
    - frontend/src/App.tsx
decisions:
  - "Used injectedValue prop pattern on ChatInput rather than lifting full text state to ChatPage — minimizes changes to ChatInput internals while enabling prompt injection from GreetingCard"
  - "GreetingCard onPromptClick threaded through MessageList to avoid breaking MessageList props contract"
  - "Kept getJobList() legacy function in jobs.ts alongside new listJobs() — JobPage uses getJobList() and would require separate migration"
metrics:
  duration: "6 min"
  completed_date: "2026-04-07"
  tasks: 2
  files: 9
---

# Phase 06 Plan 04: Job History Page and Enhanced GreetingCard Summary

**One-liner:** Paginated job history table at /jobs with keyset cursor pagination, WCAG-compliant StatusBadge, and GreetingCard with 4 clickable example prompts and capability indicators.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Job history page with table, filters, and pagination | 8f64e95 | JobHistoryPage.tsx, StatusBadge.tsx, jobs.ts, table.tsx |
| 2 | Enhanced GreetingCard with clickable example prompts | 438febf | GreetingCard.tsx, ChatPage.tsx, ChatInput.tsx, MessageList.tsx |

## What Was Built

### Task 1: Job History Page

- **JobHistoryPage** at `/jobs`: semantic HTML table with 7 columns (Job, Tool, Status, Date, Designs, Cost, Actions)
- **Status filter** restricted to exactly: All, Running, Complete, Failed (no Cancelled) per D-17
- **Keyset pagination**: cursor stack tracks Previous/Next pages; 25 jobs per page (D-18)
- **Empty state**: "No jobs yet" with "Open chat" CTA linking to `/chat` (D-20)
- **Loading skeleton**: 5 animated rows with `animate-pulse bg-muted` (D-18)
- **Mobile card layout**: `md:hidden` card list below 768px as fallback for the table (D-19)
- **Error state**: user-facing copy per UI-SPEC
- **StatusBadge**: reusable component with `sr-only " status"` suffix for WCAG D-25; color-coded per UI-SPEC (blue/running, green/complete, destructive/failed)
- **listJobs()**: added to `lib/jobs.ts` with keyset pagination params; updated `JobListItem` interface to include `name`, `candidate_count`, `session_id`
- **Route**: `/jobs` and `/chat` added to App.tsx router
- Installed `shadcn table` component (Plan 03 parallel dependency handled)

### Task 2: Enhanced GreetingCard

- **4 example prompts**: clickable buttons that auto-fill chat input on click (D-21)
- **Capability indicators**: tool list (RFdiffusion, BindCraft, BoltzGen, RFantibody) and input types row (D-22)
- **onPromptClick wiring**: prop threaded from `GreetingCard` → `MessageList` → `ChatPage`
- **ChatInput injection**: `injectedValue` and `onInjectedValueConsumed` props enable prompt text injection; external `textareaRef` enables focus
- **Post-first-job message** (D-23): one-time inline assistant message after first job completes, gated by `localStorage.getItem("kendrew_first_job_shown")`

## Verification

- TypeScript compiles clean (`npx tsc --noEmit` exits 0)
- All acceptance criteria satisfied:
  - `JobHistoryPage.tsx` contains `TableHeader`, `scope="col"`, `StatusBadge`, `No jobs yet`, `Start a design conversation`, `Previous`, `Next`, `listJobs`, `md:hidden`
  - Status filter options: exactly All, Running, Complete, Failed (no Cancelled)
  - `StatusBadge.tsx` contains `sr-only`, `bg-blue-500/15`, `bg-green-500/15`
  - `jobs.ts` contains `export async function listJobs`, `JobListItem`
  - `GreetingCard.tsx` contains `onPromptClick`, all 4 prompts, capability indicators
  - `ChatPage.tsx` contains `onPromptClick`, `kendrew_first_job_shown`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] shadcn table component not yet installed**
- **Found during:** Task 1 (Plan 03 runs in parallel)
- **Issue:** `table.tsx` not present in `frontend/src/components/ui/`
- **Fix:** Ran `npx shadcn add table --yes` per plan instructions note
- **Files modified:** `frontend/src/components/ui/table.tsx` (created)
- **Commit:** 8f64e95

**2. [Rule 2 - Missing functionality] ChatInput had no way to receive injected text from parent**
- **Found during:** Task 2 wiring
- **Issue:** `ChatInput` manages its own text state internally; no mechanism for GreetingCard prompt clicks to fill the input
- **Fix:** Added `injectedValue`, `onInjectedValueConsumed`, and `textareaRef` props to `ChatInput`; used `useEffect` to consume injected value and set local state; this is the minimal-invasive approach consistent with the plan's stated goal
- **Files modified:** `frontend/src/components/chat/ChatInput.tsx`
- **Commit:** 438febf

**3. [Rule 2 - Missing prop thread] MessageList had no onPromptClick prop**
- **Found during:** Task 2 — GreetingCard is rendered inside MessageList, not directly in ChatPage
- **Issue:** Plan shows `<GreetingCard onPromptClick={...} />` in ChatPage, but GreetingCard is actually a child of MessageList
- **Fix:** Added `onPromptClick?: (prompt: string) => void` to MessageList props and forwarded to GreetingCard
- **Files modified:** `frontend/src/components/chat/MessageList.tsx`
- **Commit:** 438febf

## Known Stubs

None — all data flows are wired to the live `listJobs` API endpoint.

## Self-Check: PASSED

Files exist:
- `frontend/src/pages/JobHistoryPage.tsx` — FOUND
- `frontend/src/components/common/StatusBadge.tsx` — FOUND
- `frontend/src/components/ui/table.tsx` — FOUND
- `frontend/src/components/chat/GreetingCard.tsx` — FOUND (updated)

Commits exist:
- `8f64e95` — FOUND (feat(06-04): job history page, StatusBadge, and listJobs API)
- `438febf` — FOUND (feat(06-04): enhanced GreetingCard with example prompts and first-job message)
