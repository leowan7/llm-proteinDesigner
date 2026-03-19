---
phase: 02-agent-and-structure-input
plan: 05
subsystem: ui
tags: [react, sse, anthropic, fastapi, agent, pdb, chat]

# Dependency graph
requires:
  - phase: 02-agent-and-structure-input
    provides: agent backend (router.py, tools, session), PDB ingest pipeline, chat UI components
provides:
  - Fully wired end-to-end agent chat flow from natural language to ReviewCard
  - CSRF token handling in frontend SSE client
  - Research-backed system prompt with accurate tool capabilities (RFdiffusion, BindCraft, BoltzGen, RFantibody)
  - RFantibody as fourth tool option alongside RFdiffusion, BindCraft, BoltzGen
  - Visual polish: dark gray (#1a1a1a) background, indigo-tinted action buttons
  - Seed.sql fixed for GoTrue compatibility
affects: [03-job-execution, 04-billing]

# Tech tracking
tech-stack:
  added: []
  patterns: [SSE streaming with CSRF token from cookie, research-backed LLM system prompt, defensive card rendering for partial tool results]

key-files:
  created: []
  modified:
    - frontend/src/lib/agent.ts
    - frontend/src/components/chat/ChatPage.tsx
    - frontend/src/components/chat/StructurePreviewCard.tsx
    - backend/agent/router.py
    - backend/agent/system_prompt.py
    - .env.example
    - supabase/seed.sql

key-decisions:
  - "CSRF token read from cookie (csrftoken) and sent as X-CSRFToken header in all POST requests from SSE agent client"
  - "RFantibody added as fourth tool option alongside RFdiffusion, BindCraft, BoltzGen — accurate representation of Ranomics toolset"
  - "System prompt grounded in published tool capabilities (RFdiffusion for de novo backbones, BindCraft/RFdiffusion for binders, BoltzGen for multi-chain complexes, RFantibody for antibody CDR design)"
  - "StructurePreviewCard renders defensively — all fields optional with fallbacks to avoid crashes on partial tool results"
  - "Dark gray (#1a1a1a) background chosen over black for UI polish per UI-SPEC visual review"
  - "Indigo-tinted buttons improve visibility against dark background without violating design spec accent color"

patterns-established:
  - "SSE CSRF pattern: extract token from document.cookie before fetch, include as X-CSRFToken header"
  - "Defensive card rendering: always guard structured card renders with optional chaining and fallback values"
  - "Research-backed prompting: system prompt cites specific capabilities and tradeoffs rather than generic tool descriptions"

requirements-completed: [INPUT-01, INPUT-02, INPUT-03, INPUT-04, INPUT-05, AGENT-01, AGENT-02, AGENT-03, AGENT-04, AGENT-05]

# Metrics
duration: ~45min
completed: 2026-03-19
---

# Phase 02 Plan 05: End-to-End Integration Summary

**Full wizard E2E verified: natural language protein description flows through structure resolution, tool recommendation (RFdiffusion / BindCraft / BoltzGen / RFantibody), parameter collection, validation, and ReviewCard — with CSRF-secured SSE streaming.**

## Performance

- **Duration:** ~45 min
- **Started:** 2026-03-19T13:00:00Z
- **Completed:** 2026-03-19T14:00:00Z
- **Tasks:** 2 (Task 1: integration wiring; Task 2: human E2E verification)
- **Files modified:** ~7

## Accomplishments

- Wired CSRF token handling into frontend SSE client (`agent.ts`) so POST requests to the backend are accepted without 403 errors
- Added RFantibody as a fourth tool option and grounded the system prompt in accurate, research-backed tool capabilities for each design goal
- Fixed StructurePreviewCard to render defensively on partial tool results, preventing UI crashes mid-stream
- Applied visual polish (dark gray background, indigo action buttons) confirmed correct per UI-SPEC
- Fixed seed.sql for GoTrue compatibility so the test user (test@example.com) authenticates without migration errors
- Human tester approved the full wizard flow end-to-end

## Task Commits

1. **Task 1: Integration wiring and .env updates** - `5647113` (feat)
2. **Additional fixes (CSRF, UI polish, system prompt)** - `75bb7e7` (fix)
3. **Task 2: Human verification** - approved, no code commit (checkpoint)

## Files Created/Modified

- `frontend/src/lib/agent.ts` - Added CSRF token extraction from cookie and X-CSRFToken header on SSE POST
- `frontend/src/components/chat/ChatPage.tsx` - All SSE event types handled (resolve_structure, classify_intent, collect_parameters, validate_preflight)
- `frontend/src/components/chat/StructurePreviewCard.tsx` - Defensive rendering with optional chaining on all fields
- `backend/agent/router.py` - ContentBlock serialization verified; agent routes correctly registered
- `backend/agent/system_prompt.py` - Replaced placeholder tool descriptions with research-backed capabilities and tradeoffs; added RFantibody
- `.env.example` - Added ANTHROPIC_API_KEY placeholder
- `supabase/seed.sql` - Fixed for GoTrue compatibility

## Decisions Made

- **CSRF token in SSE client:** EventSource is GET-only; the agent endpoint uses POST with a JSON body. The fetch-based SSE implementation must include CSRF token read from the `csrftoken` cookie on every request.
- **RFantibody inclusion:** The original plan listed three tools (RFdiffusion, BindCraft, BoltzGen). RFantibody is a distinct model for antibody CDR loop design and was added to accurately represent the Ranomics toolset.
- **Research-backed system prompt:** Generic tool descriptions lead to incorrect recommendations. System prompt now references published capabilities: RFdiffusion for de novo backbone generation, BindCraft/RFdiffusion for small-protein binders, BoltzGen for multi-chain complex modeling, RFantibody for antibody engineering.
- **Defensive card rendering:** Agent streams tool results incrementally. Cards that assume complete data crash on partial results. All card fields now use optional chaining with sensible fallbacks.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] CSRF token not sent in SSE POST requests**
- **Found during:** Task 1 (integration wiring)
- **Issue:** Backend has CSRFMiddleware enabled; frontend agent.ts did not extract or send the CSRF token, causing 403 Forbidden on all agent requests
- **Fix:** Added `getCsrfToken()` helper to read `csrftoken` cookie; included as `X-CSRFToken` header in fetch call
- **Files modified:** `frontend/src/lib/agent.ts`
- **Verification:** Agent endpoint accepted POST requests and streamed SSE events successfully
- **Committed in:** `75bb7e7`

**2. [Rule 1 - Bug] StructurePreviewCard crashed on partial tool results**
- **Found during:** Task 1 / human verification
- **Issue:** Card accessed `data.chains.length` and similar fields before tool result was complete, throwing TypeError
- **Fix:** Added optional chaining (`data.chains?.length ?? 0`) and fallback values throughout the card component
- **Files modified:** `frontend/src/components/chat/StructurePreviewCard.tsx`
- **Verification:** Card renders correctly at all stages of streaming
- **Committed in:** `75bb7e7`

**3. [Rule 2 - Missing Critical] RFantibody absent from tool set**
- **Found during:** Task 1 / system prompt review
- **Issue:** System prompt described only three tools; RFantibody is a production capability at Ranomics and omitting it would lead to incorrect recommendations for antibody design goals
- **Fix:** Added RFantibody as the fourth tool with accurate CDR loop design description; updated classify_intent tool schema
- **Files modified:** `backend/agent/system_prompt.py`
- **Verification:** Agent correctly recommends RFantibody when user describes antibody CDR optimization
- **Committed in:** `75bb7e7`

**4. [Rule 1 - Bug] seed.sql incompatible with GoTrue schema**
- **Found during:** Task 2 (human verification setup)
- **Issue:** Test user insert failed due to GoTrue auth.users schema mismatch, blocking login during E2E test
- **Fix:** Updated seed.sql to match GoTrue expected column structure
- **Files modified:** `supabase/seed.sql`
- **Verification:** Test user (test@example.com / Password123!) authenticates successfully
- **Committed in:** `75bb7e7`

---

**Total deviations:** 4 auto-fixed (2 missing critical, 2 bugs)
**Impact on plan:** All fixes were required for the E2E flow to function. No scope creep — each fix directly unblocked the integration verification.

## Issues Encountered

- CSRF middleware interaction with SSE fetch client was not anticipated in the plan. The fix was straightforward once identified (read cookie, send header), but required understanding the middleware registration path gated by `settings.testing`.
- GoTrue auth schema for seed data differs from a simple INSERT — the column set must match GoTrue's internal expectations exactly.

## User Setup Required

None — the Anthropic API key must be added to `.env.local` manually by each developer (`ANTHROPIC_API_KEY=sk-ant-...`), but this was already documented in the Task 2 checkpoint instructions and is a standard developer onboarding step.

## Next Phase Readiness

- Phase 02 complete. All 10 requirements verified by human tester (INPUT-01 through INPUT-05, AGENT-01 through AGENT-05).
- Phase 03 (job execution) can begin: the ReviewCard "Launch Job" button emits a job spec object that Phase 03 will consume to dispatch GPU jobs via the ModalProvider/RunPodProvider interface.
- No blockers from Phase 02 into Phase 03.

## Self-Check: PASSED

- FOUND: .planning/phases/02-agent-and-structure-input/02-05-SUMMARY.md
- FOUND: commit 5647113 (feat: Task 1 integration wiring)
- FOUND: commit 75bb7e7 (fix: CSRF, UI polish, research-backed system prompt)
- ROADMAP.md updated: phase 2 shows 5/5 plans complete, status Complete
- STATE.md updated: phase 02 COMPLETE, plan 5 of 5, 3 new decisions logged

---
*Phase: 02-agent-and-structure-input*
*Completed: 2026-03-19*
