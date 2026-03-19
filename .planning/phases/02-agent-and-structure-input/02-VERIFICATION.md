---
phase: 02-agent-and-structure-input
verified: 2026-03-19T14:30:00Z
status: gaps_found
score: 9/10 must-haves verified
gaps:
  - truth: "Agent classifies design intent into binder_design, de_novo_backbone, or motif_scaffolding"
    status: partial
    reason: "test_classify_intent_returns_valid_type and test_classify_intent_includes_tool_recommendation fail because the tests assert len(enum)==3, but the classify_intent tool schema was expanded in Plan 05 to 6 design_types (minibinder, vhh_nanobody, de_novo_backbone, motif_scaffolding, conformational_ensemble, structure_prediction) and 4 recommended_tools (rfdiffusion, rfantibody, bindcraft, boltzgen). The production schema is correct and more accurate; the tests were not updated."
    artifacts:
      - path: "backend/tests/agent/test_tools.py"
        issue: "TestIntentClassification::test_classify_intent_returns_valid_type asserts 'binder_design' in enum (missing from updated schema) and len==3 (actual: 6). TestIntentClassification::test_classify_intent_includes_tool_recommendation asserts len==3 (actual: 4)."
    missing:
      - "Update test_classify_intent_returns_valid_type to assert the 6 current design_type values (minibinder, vhh_nanobody, de_novo_backbone, motif_scaffolding, conformational_ensemble, structure_prediction) and remove the len==3 assertion"
      - "Update test_classify_intent_includes_tool_recommendation to assert all 4 current tool values (rfdiffusion, rfantibody, bindcraft, boltzgen) and remove the len==3 assertion"
human_verification:
  - test: "Full agent wizard flow end-to-end"
    expected: "User describes protein, agent resolves structure (StructurePreviewCard), classifies intent, recommends tool with action buttons, collects parameters, runs validation (ValidationCard), presents ReviewCard with Launch Job button gated by warning acknowledgment"
    why_human: "SSE streaming, real-time card rendering, and multi-turn Claude tool-use loop cannot be verified programmatically. Human-verified by Plan 05 Task 2 checkpoint (approved 2026-03-19)."
  - test: "PDB file drag-drop upload"
    expected: "Dragging a .pdb or .cif file onto the chat input shows a file attachment pill; sending triggers /pdb/upload, normalization, and StructurePreviewCard with collapsible normalization changes"
    why_human: "File drag-drop events and cross-component UI state require browser interaction"
  - test: "New Session confirmation and reset"
    expected: "Clicking 'New Session' shows confirmation dialog; confirming clears messages and starts fresh session"
    why_human: "Dialog state and session lifecycle require browser interaction"
---

# Phase 02: Agent and Structure Input Verification Report

**Phase Goal:** Build the agent-guided conversation flow and structure input pipeline — from target identification through tool recommendation to validated JobSpec.
**Verified:** 2026-03-19T14:30:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | JobSpec Pydantic model validates tool, target_pdb_path, target_chain, parameters, validation_results, estimated_cost_usd | VERIFIED | `from agent.jobspec import JobSpec` imports clean; all 8 fields confirmed present; `tool` Literal now includes rfantibody (4 tools) |
| 2 | Wizard question sets exist for rfdiffusion, bindcraft, boltzgen (and rfantibody) with Ranomics-curated defaults | VERIFIED | `WIZARD_PARAMS` has 4 keys; rfdiffusion=3 params, bindcraft=4 params, boltzgen=3 params, rfantibody confirmed present |
| 3 | Database migration adds job_spec JSONB and pdb_path TEXT to jobs table | VERIFIED | `supabase/migrations/20260319000001_jobspec.sql` contains both `ALTER TABLE` statements, idx_jobs_tool index, and column comments |
| 4 | Config module includes anthropic_api_key and rcsb/uniprot base URL settings | VERIFIED | `settings.anthropic_api_key`, `settings.rcsb_base_url`, `settings.uniprot_base_url`, `settings.agent_model` all exist |
| 5 | PDB pipeline (upload, RCSB fetch, UniProt resolve, NL search) normalizes structures and returns summaries | VERIFIED | `pdb_utils/{fetch,normalize,validate,router}.py` all import; PDB router has 4 routes; 17 pdb tests pass |
| 6 | Agent classifies design intent and recommends tool with rationale | PARTIAL | `classify_intent` tool schema has 6 design_types and 4 recommended_tools (correct, expanded in Plan 05); but 2 tests in `test_tools.py` assert old 3-value enums and fail |
| 7 | Agent maintains multi-turn message history in Redis per session | VERIFIED | `SessionManager` has create/load/save/delete methods; session tests pass (7/7); key format `session:{user_id}:{session_id}` confirmed |
| 8 | Agent router exposes SSE streaming endpoint and is registered in main app | VERIFIED | `/agent/message`, `/agent/session`, `/agent/session/{id}` all registered; `dispatch_tool` and `session_manager.load` wired in router |
| 9 | Frontend chat UI renders all structured cards and handles all SSE event types | VERIFIED | All 9 chat components exist and are substantive (ChatPage=498 lines, ChatInput=161, ReviewCard=138); all 4 tool_result handlers present; TypeScript compiles clean |
| 10 | End-to-end flow from natural language to ReviewCard verified by human | VERIFIED | Plan 05 Task 2 (human checkpoint) approved; CSRF token handling, defensive card rendering, and visual polish all confirmed |

**Score:** 9/10 truths verified (1 partial due to stale tests)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/agent/jobspec.py` | JobSpec, ValidationResult Pydantic models | VERIFIED | All 8 JobSpec fields present; tool Literal includes rfantibody |
| `backend/agent/wizard.py` | WIZARD_PARAMS for 3 tools (+ rfantibody) | VERIFIED | 4 keys; rfdiffusion=3, bindcraft=4, boltzgen=3, rfantibody params present |
| `backend/pdb_utils/models.py` | StructureSummary, NormalizationResult, HotspotCheck | VERIFIED | All 3 models import cleanly with correct fields |
| `backend/pdb_utils/fetch.py` | fetch_pdb_file, search_uniprot, resolve_pdb_for_uniprot | VERIFIED | All 3 async functions exported; RCSB/UniProt URLs via settings |
| `backend/pdb_utils/normalize.py` | normalize_structure | VERIFIED | Function exists; handles NMR, MSE, altloc |
| `backend/pdb_utils/validate.py` | check_hotspot_accessibility, run_preflight_checks | VERIFIED | Both functions exported; SASA computation present |
| `backend/pdb_utils/router.py` | 4 FastAPI endpoints | VERIFIED | /pdb/upload, /pdb/fetch, /pdb/search, /pdb/resolve all registered |
| `backend/agent/tools.py` | TOOL_DEFINITIONS (4 tools), dispatch_tool | VERIFIED | 4 tool definitions with name/description/input_schema; dispatch_tool routes all 4 |
| `backend/agent/session.py` | SessionManager | VERIFIED | create, load, save, delete, get_active_session methods present |
| `backend/agent/router.py` | SSE streaming agent router | VERIFIED | 3 endpoints; tool-use loop with ContentBlock serialization |
| `backend/agent/system_prompt.py` | AGENT_SYSTEM_PROMPT | VERIFIED | Research-backed prompt with Ranomics domain knowledge; RFantibody included |
| `backend/main.py` | Both pdb_router and agent_router registered | VERIFIED | Direct imports (no try/except guards); all routes present |
| `supabase/migrations/20260319000001_jobspec.sql` | job_spec JSONB, pdb_path TEXT | VERIFIED | Both ALTER TABLE statements present |
| `frontend/src/lib/agent.ts` | createSession, sendMessage, deleteSession, typed SSE client | VERIFIED | All 4 functions exported; CSRF token handling; AgentEvent union type |
| `frontend/src/components/chat/ChatPage.tsx` | Full-width 2-column chat layout, SSE event dispatcher | VERIFIED | 498 lines; all 4 tool_result handlers; createSession on mount |
| `frontend/src/components/chat/ChatInput.tsx` | Drag-drop, Send button | VERIFIED | onDragOver/onDrop handlers; SendHorizontal icon; aria-label="Send message" |
| `frontend/src/components/chat/ReviewCard.tsx` | Launch Job button, ring-2 styling | VERIFIED | "Launch Job" text present; `ring-2 ring-primary/30` class confirmed |
| `frontend/src/components/chat/ValidationCard.tsx` | Checklist with pass/warn/fail, Proceed with warnings | VERIFIED | CheckCircle/AlertTriangle/XCircle icons; "Proceed with warnings" button |
| `frontend/src/App.tsx` | ChatPage at route "/" | VERIFIED | `path="/"` with `element={<ChatPage />}` |
| `.env.example` | ANTHROPIC_API_KEY placeholder | VERIFIED | `ANTHROPIC_API_KEY=sk-ant-your-key-here` present |
| `backend/tests/agent/test_tools.py` | All agent tool tests pass | FAILED | 2 of 10 tests fail (see Gaps) |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/agent/jobspec.py` | `backend/agent/wizard.py` | JobSpec.parameters dict / WIZARD_PARAMS schema | WIRED | `WIZARD_PARAMS` imported directly in tools.py for parameter validation |
| `backend/pdb_utils/router.py` | `backend/pdb_utils/normalize.py` | `normalize_structure` called in every upload/fetch | WIRED | Lines 77 and 117 in router.py both call normalize_structure |
| `backend/pdb_utils/fetch.py` | RCSB (files.rcsb.org) | httpx GET using `settings.rcsb_base_url` | WIRED | `url = f"{settings.rcsb_base_url}/download/{pdb_id.upper()}.cif"` |
| `backend/pdb_utils/fetch.py` | UniProt (rest.uniprot.org) | httpx GET using `settings.uniprot_base_url` | WIRED | `url = f"{settings.uniprot_base_url}/uniprotkb/search"` |
| `backend/agent/router.py` | `backend/agent/session.py` | `session_manager.load` on every message | WIRED | Line 75 in router.py calls `session_manager.load(user_id, req.session_id)` |
| `backend/agent/router.py` | `backend/agent/tools.py` | `dispatch_tool` in tool-use loop | WIRED | Line 132 calls `await dispatch_tool(block.name, block.input)` |
| `backend/agent/tools.py` | `backend/pdb_utils/fetch.py` | resolve_structure tool handler | WIRED | Line 175: `from pdb_utils.fetch import fetch_pdb_file, search_uniprot, resolve_pdb_for_uniprot` (inside handler) |
| `backend/agent/tools.py` | `backend/pdb_utils/validate.py` | validate_preflight tool handler | WIRED | Line 315: `from pdb_utils.validate import check_hotspot_accessibility` (inside handler) |
| `frontend/src/lib/agent.ts` | `backend/agent/router.py` | fetch POST `/agent/message` with SSE ReadableStream | WIRED | `fetch(\`${API_BASE}/agent/message\`...)` with ReadableStream parser |
| `frontend/src/components/chat/ChatPage.tsx` | `frontend/src/lib/agent.ts` | createSession on mount, sendMessage on user input | WIRED | Lines 79 and 326 confirmed |
| `frontend/src/App.tsx` | `frontend/src/components/chat/ChatPage.tsx` | Route path="/" | WIRED | `<Route path="/" element={<ChatPage />} />` at line 59 |

---

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|--------------|-------------|--------|----------|
| INPUT-01 | 02-01, 02-02, 02-04, 02-05 | User uploads PDB file as target structure | SATISFIED | POST /pdb/upload in router.py; ChatInput drag-drop; normalize_structure called |
| INPUT-02 | 02-02, 02-04, 02-05 | User provides PDB accession; system fetches from RCSB | SATISFIED | fetch_pdb_file in fetch.py; /pdb/fetch endpoint; resolve_structure tool |
| INPUT-03 | 02-02, 02-04, 02-05 | User provides UniProt accession; system resolves to PDB | SATISFIED | resolve_pdb_for_uniprot in fetch.py; /pdb/resolve endpoint |
| INPUT-04 | 02-02, 02-04, 02-05 | Natural language target description; agent resolves to PDB | SATISFIED | search_uniprot in fetch.py; natural_language branch in resolve_structure handler |
| INPUT-05 | 02-01, 02-02, 02-04, 02-05 | System normalizes PDB files (NMR, altloc, MSE) | SATISFIED | normalize_structure handles all 3 cases; 6 tests pass |
| AGENT-01 | 02-01, 02-03, 02-05 | Agent classifies design intent from natural language | PARTIAL | classify_intent tool schema is functional with 6 design_types; 2 tests that check old enum size fail |
| AGENT-02 | 02-01, 02-03, 02-04, 02-05 | Agent recommends tool with rationale; user confirms | SATISFIED | classify_intent schema requires rationale; action buttons in ChatPage for confirmation |
| AGENT-03 | 02-01, 02-03, 02-04, 02-05 | Agent collects parameters via guided wizard | SATISFIED | collect_parameters tool returns WIZARD_PARAMS defaults; wizard flow verified E2E |
| AGENT-04 | 02-01, 02-02, 02-03, 02-05 | Pre-flight validation: PDB quality, hotspot SASA, parameter sanity | SATISFIED | validate_preflight tool calls check_hotspot_accessibility and param range checks; 5 validate tests pass |
| AGENT-05 | 02-01, 02-03, 02-04, 02-05 | Validation warnings require acknowledgment before dispatch | SATISFIED | warningsAcknowledged state gates Launch Job button in ReviewCard; ValidationCard "Proceed with warnings" flow |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backend/tests/agent/test_tools.py` | 24-27 | Asserts `binder_design` in design_type enum and `len==3`; enum has been updated to 6 values with different names | BLOCKER | 2 tests fail, `python -m pytest tests/agent/ -q` reports FAILED — breaks CI |
| `backend/tests/agent/test_tools.py` | 33-36 | Asserts `len(recommended_tool enum)==3`; actual is 4 (rfantibody added) | BLOCKER | Same test run failure |
| `frontend/src/components/chat/ChatPage.tsx` | 225 | `estimated_cost_usd: 0` with comment "Phase 3 billing; placeholder until billing is wired" | INFO | Billing is explicitly Phase 3 scope; not a Phase 2 blocker |

---

### Human Verification Required

#### 1. Full Agent Wizard Flow

**Test:** Start dev environment (`make dev`), open http://localhost:5173, log in, type "I want to design a binder for IL-6 receptor", follow the full wizard to ReviewCard.
**Expected:** StructurePreviewCard appears with PDB metadata, tool recommendation buttons show, ValidationCard displays pass/warn/fail checklist, ReviewCard has Launch Job button gated by warning acknowledgment.
**Why human:** SSE streaming, multi-turn Claude tool-use loop, and structured card rendering require live browser + running backend.
**Note:** This was verified by human tester in Plan 05 Task 2 checkpoint (approved 2026-03-19).

#### 2. PDB File Drag-Drop

**Test:** Drag a .pdb file onto the ChatInput.
**Expected:** Border shifts to primary color, file attachment pill appears, sending includes the file context.
**Why human:** Browser drag-drop events and CSS state transitions require live interaction.

#### 3. New Session Reset

**Test:** Click "New Session" during an active conversation.
**Expected:** Confirmation dialog appears; confirming clears all messages and creates a fresh session.
**Why human:** Confirmation dialog state and session lifecycle require browser interaction.

---

### Gaps Summary

One gap was found: 2 tests in `backend/tests/agent/test_tools.py` (class `TestIntentClassification`) assert the original 3-enum design from Plan 02-01, but Plan 05 expanded the `classify_intent` tool schema to 6 design_types and 4 recommended_tools to accurately represent the full Ranomics toolset. The production code is correct and more accurate; the tests were not updated to match the expanded schema.

The fix is straightforward: update the two assertions in `test_classify_intent_returns_valid_type` to check for the 6 current design_type values (minibinder, vhh_nanobody, de_novo_backbone, motif_scaffolding, conformational_ensemble, structure_prediction), and update `test_classify_intent_includes_tool_recommendation` to check for 4 tools including rfantibody. No production code changes are needed.

All other automated checks pass: 26 of 28 pdb+agent tests pass, TypeScript compiles clean, all routes are registered, all key links are wired, and the end-to-end flow was human-verified.

---

_Verified: 2026-03-19T14:30:00Z_
_Verifier: Claude (gsd-verifier)_
