---
phase: 02-agent-and-structure-input
plan: 03
subsystem: agent
tags: [claude, tool-use, sse, redis, session, fastapi]
dependency_graph:
  requires: ["02-01"]
  provides: ["agent-backend", "sse-streaming", "session-management"]
  affects: ["02-04", "02-05"]
tech_stack:
  added: [anthropic-sdk, redis.asyncio, fastapi-streaming-response]
  patterns: [tool-use-loop, sse-streaming, redis-session, dispatch-handler]
key_files:
  created:
    - backend/agent/system_prompt.py
    - backend/agent/tools.py
    - backend/agent/session.py
    - backend/agent/router.py
  modified:
    - backend/main.py
    - backend/tests/agent/test_tools.py
    - backend/tests/agent/test_session.py
decisions:
  - "pdb_utils imports guarded with try/except ImportError in tools.py and main.py — Plan 02-02 runs in the same wave; guard prevents ImportError if 02-03 completes first"
  - "dispatch_tool is async but Anthropic SDK messages.create is synchronous — kept sync call inside async generator; acceptable for now since tool I/O is the real latency source"
  - "APIError exception caught by attribute getattr(exc, 'message', str(exc)) — avoids breaking if Anthropic SDK version changes the attribute name"
metrics:
  duration: "5 min"
  completed_date: "2026-03-19"
  tasks_completed: 2
  files_changed: 7
---

# Phase 02 Plan 03: Agent Backend Summary

Claude-powered protein design agent with 4-tool dispatch loop, Redis session management, and SSE streaming via FastAPI.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | System prompt, tool definitions, session manager | 93ae0d1 | agent/system_prompt.py, agent/tools.py, agent/session.py, tests/agent/ |
| 2 | FastAPI router with SSE streaming | fc1efa7 | agent/router.py, main.py |

## What Was Built

### System Prompt (`backend/agent/system_prompt.py`)
`AGENT_SYSTEM_PROMPT` encodes the 5-step wizard flow (resolve structure → classify intent → recommend tool → collect parameters → validate preflight), Ranomics domain knowledge for tool selection, and explicit tool use policy (never invent PDB accessions, never skip user confirmation).

### Tool Definitions and Handlers (`backend/agent/tools.py`)
`TOOL_DEFINITIONS`: 4 JSON schemas sent to the Claude API:
- `resolve_structure` — fetches from RCSB PDB or UniProt (3 query types: pdb_accession, uniprot_accession, natural_language)
- `classify_intent` — passthrough; Claude classifies into binder_design / de_novo_backbone / motif_scaffolding
- `collect_parameters` — returns WIZARD_PARAMS defaults merged with user overrides
- `validate_preflight` — runs SASA hotspot checks + parameter min/max sanity

`dispatch_tool` routes tool calls to per-tool async handlers. pdb_utils imports are guarded with try/except ImportError to survive Plan 02-02 running in the same wave.

### Session Manager (`backend/agent/session.py`)
`SessionManager` stores the full messages[] array in Redis per `session:{user_id}:{session_id}` key with TTL from `agent_session_ttl_seconds`. Methods: create, load, save, delete, get_active_session. Accepts an optional redis_client in constructor for test injection.

### FastAPI Router (`backend/agent/router.py`)
Three endpoints:
- `POST /agent/session` — creates session, returns session_id
- `POST /agent/message` — runs Claude tool-use loop, streams SSE events (status / text / tool_result / done / error)
- `DELETE /agent/session/{id}` — clears conversation

The tool-use loop continues until stop_reason == "end_turn", dispatching tools and appending tool_result blocks as user messages (Anthropic API requirement).

## Tests

19 tests pass across:
- `TestIntentClassification` — classify_intent schema has correct enums
- `TestToolRecommendation` — all 4 tools have required fields, rationale is required
- `TestDispatchTool` — dispatch_tool routes correctly, collect_parameters applies overrides, validate_preflight detects out-of-range params
- `TestSessionManagement` — save/load roundtrip, user isolation, missing session raises ValueError, delete, get_active_session
- `TestWizardCompletion` — full wizard message flow produces a valid JobSpec

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] Guard pdb_utils imports in tools.py**
- **Found during:** Task 1 implementation
- **Issue:** Plan imports `from pdb.fetch import ...` and `from pdb.validate import ...` at module level; pdb_utils.fetch and pdb_utils.validate don't exist yet (Plan 02-02 in same wave)
- **Fix:** Moved pdb_utils imports inside handler functions with try/except ImportError; returns graceful error JSON instead of crashing
- **Files modified:** backend/agent/tools.py
- **Commit:** 93ae0d1

**2. [Rule 3 - Blocking issue] Corrected import path pdb -> pdb_utils**
- **Found during:** Task 1 — per `<important_deviation>` in execution prompt
- **Issue:** Plan references `from pdb.fetch import ...` but backend/pdb/ was renamed to backend/pdb_utils/ in Plan 02-01 to avoid shadowing Python's stdlib pdb module
- **Fix:** All import paths use `pdb_utils.*` throughout tools.py
- **Files modified:** backend/agent/tools.py
- **Commit:** 93ae0d1

**3. [Rule 2 - Missing critical functionality] Guard pdb_utils.router import in main.py**
- **Found during:** Task 2 — pdb_utils/router.py doesn't exist yet
- **Issue:** Unconditional import would fail if 02-03 completes before 02-02
- **Fix:** Wrapped pdb_utils.router import in try/except ImportError with explanatory comment
- **Files modified:** backend/main.py
- **Commit:** fc1efa7

## Self-Check: PASSED

All created files confirmed on disk. Both task commits (93ae0d1, fc1efa7) confirmed in git log.
