---
phase: 02-agent-and-structure-input
plan: "04"
subsystem: ui
tags: [react, typescript, shadcn, sse, streaming, chat, drag-drop]

requires:
  - phase: 02-02
    provides: POST /agent/message SSE endpoint, POST /agent/session, DELETE /agent/session/{id}
  - phase: 02-03
    provides: POST /pdb/upload multipart endpoint, PDB normalization pipeline

provides:
  - ChatPage with 2-column layout (60% chat / 40% context panel)
  - Agent SSE client (fetch + ReadableStream, not EventSource)
  - ChatInput with .pdb/.cif drag-drop and file attachment pill
  - MessageList with auto-scroll, typing indicator, and status line
  - StructurePreviewCard displaying PDB metadata with collapsible normalization summary
  - ReviewCard pre-launch confirmation with validation-gated Launch Job button
  - ValidationCard checklist with pass/warn/fail icons and warn acknowledgment flow
  - AgentMessage with inline markdown rendering and embedded structured cards
  - App.tsx routing updated: path="/" now renders ChatPage

affects: [phase-03-job-dispatch, phase-04-billing]

tech-stack:
  added:
    - shadcn/ui textarea (multi-line chat input)
    - shadcn/ui badge (status labels)
    - shadcn/ui separator (ReviewCard dividers)
    - shadcn/ui scroll-area (MessageList container)
    - shadcn/ui tooltip (hotspot residue help)
    - shadcn/ui alert (hard-block validation errors)
    - shadcn/ui sheet (mobile context panel)
  patterns:
    - SSE via fetch + ReadableStream (not EventSource) — required for POST body
    - Inline markdown via regex (no library) — bold/code/bullets only
    - Card-based structured output embedded in chat bubbles
    - Context panel mirrors last structured card for persistent reference
    - Session lifecycle: create on mount, delete on New Session confirmation

key-files:
  created:
    - frontend/src/lib/agent.ts
    - frontend/src/components/chat/ChatPage.tsx
    - frontend/src/components/chat/ChatInput.tsx
    - frontend/src/components/chat/MessageList.tsx
    - frontend/src/components/chat/AgentMessage.tsx
    - frontend/src/components/chat/UserMessage.tsx
    - frontend/src/components/chat/StructurePreviewCard.tsx
    - frontend/src/components/chat/ReviewCard.tsx
    - frontend/src/components/chat/ValidationCard.tsx
    - frontend/src/components/chat/GreetingCard.tsx
    - frontend/src/components/ui/textarea.tsx
    - frontend/src/components/ui/badge.tsx
    - frontend/src/components/ui/separator.tsx
    - frontend/src/components/ui/scroll-area.tsx
    - frontend/src/components/ui/tooltip.tsx
    - frontend/src/components/ui/alert.tsx
    - frontend/src/components/ui/sheet.tsx
  modified:
    - frontend/src/App.tsx (replaced placeholder route with ChatPage)
    - frontend/src/components/ui/button.tsx (shadcn overwrite)

key-decisions:
  - "shadcn AI chat components (chat/chat-input, chat/chat-message-list, chat/chat-bubble) unavailable via CLI with base-nova style — created manual equivalents with equivalent behavior"
  - "SSE via fetch + ReadableStream (not EventSource): EventSource is GET-only; agent endpoint requires POST body with session_id and message"
  - "Inline markdown renderer: regex-based (bold/code/bullets) with no external library — agent messages use a limited markdown subset that does not warrant a full parser"
  - "New Session confirmation: inline header popover (confirm/cancel) rather than a modal, per UI-SPEC copywriting contract"

patterns-established:
  - "ChatCard union type: tool_result SSE events map to structure_preview | review | validation cards by tool_name"
  - "warningsAcknowledged state gates Launch Job button: ReviewCard disabled until ValidationCard warn is acknowledged"
  - "Context panel mirrors lastCard state: always shows the most recent structured card for persistent reference while the user scrolls up"

requirements-completed: [INPUT-01, INPUT-02, INPUT-03, INPUT-04, AGENT-02, AGENT-03, AGENT-05]

duration: 6min
completed: 2026-03-19
---

# Phase 02 Plan 04: Chat UI Summary

**Full-width chat interface with SSE streaming, PDB drag-drop, and structured card rendering (StructurePreview, Review, Validation) — scientist can now interact with the design agent end-to-end from browser**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-19T12:51:39Z
- **Completed:** 2026-03-19T12:58:15Z
- **Tasks:** 2
- **Files modified:** 18

## Accomplishments

- Agent API client (`agent.ts`) with typed SSE stream parser using fetch + ReadableStream for POST-based streaming
- Complete 9-component chat UI: ChatPage (2-column layout), ChatInput (drag-drop), MessageList (auto-scroll, typing indicator), AgentMessage (markdown + embedded cards), UserMessage, StructurePreviewCard, ReviewCard, ValidationCard, GreetingCard
- App.tsx routing updated — path="/" renders ChatPage; scientists land directly in the chat interface after login

## Task Commits

Each task was committed atomically:

1. **Task 1: Install shadcn components and create agent API client** - `4705c01` (feat)
2. **Task 2: Chat page components and App.tsx routing** - `0b9544c` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `frontend/src/lib/agent.ts` - Typed SSE client: createSession, deleteSession, sendMessage, uploadPdbFile; AgentEvent union type; StructureSummary, ReviewData, ChatMessage, ChatCard interfaces
- `frontend/src/components/chat/ChatPage.tsx` - Full-width 2-column layout, session lifecycle, SSE event dispatcher
- `frontend/src/components/chat/ChatInput.tsx` - Textarea with drag-drop (.pdb/.cif), file attachment pill, Enter-to-send, disabled during processing
- `frontend/src/components/chat/MessageList.tsx` - ScrollArea wrapper, auto-scroll with user-scroll preservation, typing indicator (3 animated dots), status line
- `frontend/src/components/chat/AgentMessage.tsx` - Left-aligned bubble, inline markdown (bold/code/bullets), action buttons, embedded card rendering
- `frontend/src/components/chat/UserMessage.tsx` - Right-aligned secondary bubble, plain text
- `frontend/src/components/chat/StructurePreviewCard.tsx` - PDB ID (monospace), metadata grid, collapsible normalization changes, "Use a different structure" ghost button
- `frontend/src/components/chat/ReviewCard.tsx` - ring-2 ring-primary/30 border, parameter table, Launch Job / Edit buttons with validation-gated enable
- `frontend/src/components/chat/ValidationCard.tsx` - CheckCircle/AlertTriangle/XCircle icons, destructive Alert for failures, warn acknowledgment button
- `frontend/src/components/chat/GreetingCard.tsx` - Centered card with "What are you designing today?" heading
- `frontend/src/App.tsx` - Route path="/" replaced placeholder with ChatPage
- `frontend/src/components/ui/{textarea,badge,separator,scroll-area,tooltip,alert,sheet}.tsx` - shadcn components installed

## Decisions Made

- shadcn AI chat components (`chat/chat-input`, `chat/chat-message-list`, `chat/chat-bubble`) are unavailable for the `base-nova` style via CLI. Created manual equivalents (MessageList wraps ScrollArea; ChatInput wraps Textarea with drag handlers; AgentMessage/UserMessage are custom bubbles). No functional difference.
- SSE client uses `fetch + ReadableStream` rather than `EventSource` — EventSource is GET-only and cannot carry the session_id + message body required by the agent endpoint.
- Inline markdown renderer is regex-based (no library). Agent responses use only bold, inline code, and bullet lists. A full parser would add dependency weight without benefit.

## Deviations from Plan

None — plan executed exactly as written. The AI chat component CLI failure was documented in the plan as an expected fallback ("If these fail... create manual equivalents").

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Chat UI is complete and wired to the agent backend. Phase 03 (job dispatch) can now surface job status updates as additional SSE event types and render them in the existing MessageList
- The `ChatCard` union type and `buildCard` dispatcher in ChatPage are extension points — Phase 03 job status cards follow the same pattern
- `warningsAcknowledged` gate and `ReviewCard` "Launch Job" button are functional — Phase 03 needs to handle the actual job dispatch POST on that button click

## Self-Check: PASSED

- FOUND: frontend/src/lib/agent.ts
- FOUND: frontend/src/components/chat/ChatPage.tsx (11 chat components)
- FOUND: frontend/src/components/ui/{textarea,badge,scroll-area,separator,tooltip,alert,sheet}.tsx
- FOUND: commits 4705c01 and 0b9544c in git log
- FOUND: 02-04-SUMMARY.md
- npx tsc --noEmit exits 0
- npx vitest run: 2 passed

---
*Phase: 02-agent-and-structure-input*
*Completed: 2026-03-19*
