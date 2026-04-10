---
phase: 08-post-run-analysis-agent
verified: 2026-04-10T21:12:59Z
status: gaps_found
score: 5/7 success criteria verified
overrides_applied: 0
gaps:
  - truth: "Agent can rank candidates by user-specified criteria including metrics where lower is better"
    status: partial
    reason: "handle_analyze_candidates calls rank_candidates without consulting METRIC_THRESHOLDS lower_is_better flags. Metrics like dG, Relaxed_Clashes, Surface_Hydrophobicity (all lower_is_better=True) are sorted descending by default, ranking the worst candidates first when the user asks to rank by those metrics."
    artifacts:
      - path: "backend/agent/analysis/tools.py"
        issue: "Line ~341: rank_candidates(candidates, sort_by=sort_by) uses ascending=False default for all metrics. METRIC_THRESHOLDS already has lower_is_better flags but they are never consulted in handle_analyze_candidates."
    missing:
      - "Derive ascending from METRIC_THRESHOLDS.get(sort_by, {}).get('lower_is_better', False) before calling rank_candidates in handle_analyze_candidates"
  - truth: "Agent can identify red flags including sequence similarity to known allergens or immunogens"
    status: failed
    reason: "SC-6 in ROADMAP specifies red flags include 'sequence similarity to known allergens/immunogens'. Only 4 metric-based red flag patterns are implemented (ipTM+SC combo, dG+hydrophobicity, Relaxed_Clashes>0, pLDDT<0.7). No allergen/immunogen sequence check exists anywhere in the codebase. No later phase addresses this."
    artifacts:
      - path: "backend/agent/analysis/tools.py"
        issue: "handle_flag_red_flags implements 4 structural/metric-based red flags but no sequence-based allergen similarity check"
    missing:
      - "Allergen/immunogen sequence similarity check is either missing from implementation or the ROADMAP SC-6 was over-specified and this item should be formally descoped"
human_verification:
  - test: "Export Report prompt injection end-to-end"
    expected: "Click Export Report on a completed job page. Browser navigates to /chat. The chat input should contain 'Generate a full analysis report for job {id} with shortlisted candidates, metric explanations, and next steps.' pre-filled. Sending this message should trigger the agent to call load_job_results then generate_report."
    why_human: "IN-04 in the code review identified a potential race condition: ChatPage reads ?prompt= in a useEffect([]) on initial mount, then resolveSession navigates to /chat/SESSION_ID. If React Router reuses the same component instance the state persists; if it remounts the prompt is lost. Cannot verify programmatically without running the app."
  - test: "Full analysis agent workflow"
    expected: "In a chat session for a completed job: (1) Ask about job results -> agent calls load_job_results and summarizes candidates with distribution stats. (2) Ask 'rank by ipTM' -> agent calls analyze_candidates and returns ranked list with threshold annotations (strong/passable/red_flag). (3) Ask 'any red flags?' -> agent calls flag_red_flags and reports findings. (4) Ask 'generate a report' -> agent calls generate_report and returns pdf_url, csv_url, markdown_url. Download PDF and verify Kendrew branding, shortlist table, and PDB download links."
    why_human: "End-to-end agent tool execution requires a running backend, real Anthropic API calls, and a completed job in the database. Plan 08-04 Task 3 is an explicit blocking human verification checkpoint."
  - test: "Zero-output job diagnostic mode"
    expected: "Navigate to a chat session for a job that produced 0 candidates. Ask about results. Agent should provide diagnostic reasoning about what likely went wrong and suggest parameter adjustments."
    why_human: "Requires a real completed zero-output job in the database and a live agent session."
---

# Phase 08: Post-Run Analysis Agent Verification Report

**Phase Goal:** After a design job completes, the agent assists the scientist in analyzing results — ranking candidates, explaining metrics, identifying the best designs to order for experimental validation, and suggesting next steps.
**Verified:** 2026-04-10T21:12:59Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Agent can load completed job results into conversation context | VERIFIED | `handle_load_job_results` exists with ownership check (WHERE j.id=$1 AND j.user_id=$2). 25 tests pass. Module imports cleanly. |
| 2 | Agent can rank candidates by user-specified criteria | PARTIAL | `analyze_candidates` + `rank_candidates` functional for ascending metrics (ipTM, pLDDT, dSASA, ShapeComplementarity). **Bug (WR-02)**: lower_is_better metrics (dG, Relaxed_Clashes, Surface_Hydrophobicity) are sorted descending, ranking worst candidates first. |
| 3 | Agent explains metrics in context of user's specific design | VERIFIED | Metric profiles loaded into system prompt (_METRIC_PROFILES). System prompt instructs: "Use metric profiles above for threshold interpretation. Do NOT use generic LLM knowledge." analyze_candidates returns threshold annotations (strong/passable/red_flag) per candidate. Human verification required for quality. |
| 4 | Agent compares candidates and recommends shortlist for validation | VERIFIED | analyze_candidates tool with filter and limit params. System prompt Step 6: "Recommend a shortlist (5-20 designs) with reasoning." Tested in 25-test suite. |
| 5 | Agent provides actionable next-step guidance | VERIFIED | 04_guidance_profiles.md loaded into system prompt with expression system, purification, SPR/BLI, yeast display protocols. System prompt Step 7: "Provide protocol-level next-step guidance from the guidance profiles." No cost/timeline estimates enforced per D-21. |
| 6 | Agent identifies red flags | PARTIAL | 4 red flag patterns implemented (ipTM+SC, dG+hydrophobicity, Relaxed_Clashes>0, pLDDT<0.7). **Gap**: ROADMAP SC-6 explicitly requires "sequence similarity to known allergens/immunogens" which is not implemented and not addressed in any later phase. |
| 7 | Agent generates downloadable summary report | VERIFIED | PDF (Kendrew-branded, `%PDF` bytes confirmed), CSV (pandas DataFrame.to_csv), Markdown. All 3 uploaded to MinIO with presigned URLs. 13 report tests pass. |

**Score:** 5/7 truths fully verified (2 partial/failed)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/agent/analysis/__init__.py` | Subpackage init | VERIFIED | Exists |
| `backend/agent/analysis/cache.py` | In-memory cache with get_cached/set_cached | VERIFIED | Both functions confirmed, clear_cache() for tests |
| `backend/agent/analysis/ranking.py` | rank_candidates, filter_candidates, compute_distribution_stats | VERIFIED | All 3 functions confirmed at lines 13, 70, 132 |
| `backend/agent/analysis/tools.py` | handle_load_job_results, handle_analyze_candidates, handle_flag_red_flags | VERIFIED | All 3 handlers confirmed. METRIC_THRESHOLDS dict with all 7 metrics. Ownership check at line 168. |
| `backend/agent/analysis/pdb_features.py` | compute_bsa, count_clashes, count_interface_contacts, extract_structural_features | VERIFIED | All 4 functions confirmed. ShrakeRupley import confirmed. VDW_RADII dict with C,N,O,S,H,P,SE,FE,ZN,MG. Returns None in except blocks. |
| `backend/agent/analysis/report.py` | PDF/CSV/Markdown generation + handler | VERIFIED | KendrewReport(FPDF) class, all 4 functions, fpdf2 import, generate_presigned_get_url with expires_in=86400. "Kendrew Design Analysis Report" string confirmed. |
| `backend/agent/analysis/refolding.py` | handle_submit_refolding_job | VERIFIED | Ownership check, INSERT INTO public.jobs, mode=refolding_validation confirmed |
| `backend/agent/reference/03_metric_profiles.md` | Metric profiles with ipTM, BindCraft Metrics, Red Flag Combinations | VERIFIED | All 3 search terms confirmed |
| `backend/agent/reference/04_guidance_profiles.md` | Guidance with Expression System, SPR, Yeast Display, No cost/timeline | VERIFIED | All 4 search terms confirmed |
| `backend/agent/system_prompt.py` | _METRIC_PROFILES, _GUIDANCE_PROFILES, POST-RUN ANALYSIS section | VERIFIED | All 3 confirmed at lines 18-19, 127 |
| `backend/agent/tools.py` | 10 TOOL_DEFINITIONS, 5 new dispatch branches | VERIFIED | 10 tools confirmed by runtime check. All 5 new elif branches confirmed. |
| `backend/agent/router.py` | dispatch_tool passes user_id, 10 status messages | VERIFIED | user_id=user_id at line 141, all 5 new status messages at lines 255-259 |
| `frontend/src/pages/JobPage.tsx` | Export Report button conditional on complete+candidates | VERIFIED | Button inside `{isComplete && ... {job.candidates.length > 0 && (` double gate |
| `frontend/src/components/chat/ChatPage.tsx` | searchParams.get("prompt") injection | VERIFIED (conditional) | Code exists at lines 96-101. Functional correctness under session redirect depends on React Router instance reuse — see human verification. |
| `backend/tests/fixtures/two_chain.pdb` | Two-chain PDB with chain A and B | VERIFIED | 80 ATOM records, 40 chain A, 40 chain B confirmed |
| `backend/tests/agent/test_analysis_tools.py` | 25 tests | VERIFIED | 25 test functions confirmed, all pass |
| `backend/tests/agent/test_pdb_features.py` | 8 tests | VERIFIED | 8 test functions confirmed, all pass |
| `backend/tests/agent/test_report.py` | 13 tests | VERIFIED | 13 test functions confirmed, all pass |
| `backend/tests/agent/test_refolding.py` | 7 tests | VERIFIED | 7 test functions confirmed, all pass |

**Total test suite: 53 tests, all passing**

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/agent/tools.py` | `backend/agent/analysis/tools.py` | `from agent.analysis.tools import handle_load_job_results` | VERIFIED | Lines 322-328 confirmed |
| `backend/agent/system_prompt.py` | `backend/agent/reference/03_metric_profiles.md` | `_load_reference("03_metric_profiles.md")` | VERIFIED | Line 18 confirmed |
| `backend/agent/analysis/tools.py` | `backend/agent/analysis/cache.py` | `from agent.analysis.cache import get_cached, set_cached` | VERIFIED | Line 20 confirmed |
| `backend/agent/analysis/report.py` | `fpdf2` | `from fpdf import FPDF` | VERIFIED | Line 23 confirmed |
| `backend/agent/analysis/report.py` | `backend/storage/client.py` | `generate_presigned_get_url` with expires_in=86400 | VERIFIED | Line 345 confirmed |
| `backend/agent/analysis/refolding.py` | `backend/db/connection.py` | `INSERT INTO public.jobs` | VERIFIED | Lines 106, 172 confirmed |
| `backend/agent/router.py` | `backend/agent/tools.py dispatch_tool` | `dispatch_tool(..., user_id=user_id)` | VERIFIED | Line 141 confirmed |
| `frontend/src/pages/JobPage.tsx` | `frontend/src/components/chat/ChatPage.tsx` | `navigate('/chat?prompt=...')` | VERIFIED (see human note) | Lines 191-195 confirmed. Prompt injection depends on component instance behavior — see IN-04. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `handle_load_job_results` | `candidates` | `SELECT rank, pdb_key, scores FROM public.job_candidates WHERE job_id=$1 ORDER BY rank` | Yes — real DB query with ownership check | FLOWING |
| `handle_analyze_candidates` | `candidates` | In-memory cache (populated by ownership-checked load_job_results) | Yes — cache guaranteed to hold DB-fetched data | FLOWING |
| `generate_pdf_report` | `shortlist`, `all_candidates` | From cache via handle_generate_report | Yes — DB-backed candidates | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 10 tools registered | `python -c "from agent.tools import TOOL_DEFINITIONS; print(len(TOOL_DEFINITIONS))"` | 10 | PASS |
| System prompt has metric profiles | `python -c "from agent.system_prompt import AGENT_SYSTEM_PROMPT; print('METRIC INTERPRETATION' in AGENT_SYSTEM_PROMPT)"` | True | PASS |
| Router status messages for all 5 new tools | `from agent.router import _tool_status_text; _tool_status_text('load_job_results')` etc. | All 5 return correct strings | PASS |
| All 53 phase tests pass | `pytest tests/agent/test_analysis_tools.py tests/agent/test_pdb_features.py tests/agent/test_report.py tests/agent/test_refolding.py` | 53 passed, 1 warning in 4.40s | PASS |

### Requirements Coverage

The ANA-01 through ANA-12 requirement IDs appear only in PLAN frontmatter and ROADMAP.md. They are NOT defined as named requirements in REQUIREMENTS.md (which only covers v1 AUTH-/INPUT-/AGENT-/JOB-/RESULT-/BILL- IDs). The ROADMAP Success Criteria serve as the authoritative requirement definition for Phase 8.

| Phase 8 SC | Covering Plans | Status | Evidence |
|------------|---------------|--------|----------|
| SC-1: Load job results | Plans 08-01, 08-04 | SATISFIED | handle_load_job_results + router wiring |
| SC-2: Rank by criteria | Plan 08-01 | PARTIAL | Ranking exists but lower_is_better metrics ranked incorrectly (WR-02) |
| SC-3: Explain metrics in context | Plans 08-01 (metric profiles + system prompt) | NEEDS HUMAN | Code infrastructure correct; scientific quality of explanations requires human review |
| SC-4: Compare and recommend shortlist | Plan 08-01 | SATISFIED | analyze_candidates + system prompt workflow |
| SC-5: Next-step guidance | Plan 08-01 (guidance profiles) | SATISFIED | 04_guidance_profiles.md loaded; expression/purification/SPR/yeast display covered |
| SC-6: Identify red flags | Plans 08-01 | PARTIAL | 4 structural/metric red flags implemented; allergen/immunogen sequence check missing |
| SC-7: Generate downloadable report | Plan 08-03 | SATISFIED | PDF/CSV/MD generated and uploaded with presigned URLs |

Note: REQUIREMENTS.md traceability table has no entries for Phase 8. ANA- requirement IDs are not defined in REQUIREMENTS.md. This is an orphaned requirements situation — Phase 8 requirements exist only in ROADMAP.md, not in the canonical requirements document.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backend/agent/analysis/tools.py` | ~341 | `rank_candidates(candidates, sort_by=sort_by)` — missing lower_is_better lookup | Blocker | dG, Relaxed_Clashes, Surface_Hydrophobicity ranked worst-first when user asks to rank by these metrics |
| `backend/agent/analysis/tools.py` | ~154 | `get_cached(job_id)` — cache key is job_id only, not user_id:job_id | Warning | Multi-user security: User B can retrieve User A's cached candidates by guessing job_id |
| `backend/agent/router.py` | ~189 | `getattr(exc, "message", str(exc))` — str(exc) may expose API key in error responses | Warning | Information disclosure: Anthropic SDK str(exc) may include x-api-key header in some error subclasses |
| `backend/agent/analysis/refolding.py` | ~155-184 | INSERT loop without DB transaction | Warning | Partial inserts leave orphaned draft job rows if any INSERT fails mid-loop |
| `frontend/src/components/chat/ChatPage.tsx` | 200, 206 | `navigate('/chat/${sessions[0].id}', { replace: true })` without preserving `window.location.search` | Warning | ?prompt= query param may be lost during session resolution, breaking Export Report prompt injection |

### Human Verification Required

#### 1. Export Report Prompt Injection

**Test:** Navigate to a completed job at `/jobs/{id}`. Click "Export Report" button. Browser should navigate to `/chat?prompt=Generate a full analysis report...`. Verify the chat input field contains the pre-filled prompt text.
**Expected:** Chat input shows pre-filled analysis prompt. User can send it immediately.
**Why human:** Code review identified that ChatPage session resolution may or may not preserve the ?prompt= query param depending on React Router component instance reuse behavior. Cannot verify programmatically without running the app.

#### 2. Full Agent Analysis Workflow

**Test:** In chat with a completed BindCraft job, send: "Show me the results for job {id}". Then: "Rank these by ipTM". Then: "Any red flags?". Then: "Generate a report".
**Expected:** 
- Step 1: Agent calls load_job_results, summarizes candidate count and key metric distribution
- Step 2: Agent calls analyze_candidates, shows ranked list with threshold annotations (strong/passable/red_flag per metric)
- Step 3: Agent calls flag_red_flags, surfaces any problematic combinations
- Step 4: Agent calls generate_report, returns download links for PDF, CSV, Markdown. PDF has "Kendrew Design Analysis Report" header.
**Why human:** End-to-end requires running backend, live Anthropic API, and real completed job data. Plan 08-04 Task 3 is an explicit blocking human checkpoint.

#### 3. Metric Explanation Quality

**Test:** Load results for a completed job. Ask the agent: "What does ipTM mean for my top candidate?" and "Should I be concerned about its ShapeComplementarity score?"
**Expected:** Agent cites specific threshold values from the metric profiles (e.g., "your ipTM of 0.85 is above the 0.70 strong threshold") rather than generic LLM knowledge.
**Why human:** Scientific accuracy and contextual relevance of metric explanations requires domain expert review.

### Gaps Summary

**Gap 1 — Ranking bug for lower_is_better metrics (blocks SC-2)**
`handle_analyze_candidates` does not look up the `lower_is_better` flag in `METRIC_THRESHOLDS` when calling `rank_candidates`. For dG (lower/more negative is better), Relaxed_Clashes (0 is best), and Surface_Hydrophobicity (lower = less aggregation risk), the tool will sort descending and present the worst candidates first. Fix requires one line: derive `ascending = METRIC_THRESHOLDS.get(sort_by, {}).get("lower_is_better", False)` before the `rank_candidates` call.

**Gap 2 — Allergen/immunogen check absent (partially blocks SC-6)**
ROADMAP SC-6 explicitly lists "sequence similarity to known allergens/immunogens" as a required red flag type. The implemented `handle_flag_red_flags` covers 4 metric-based patterns but none that involve sequence analysis. No later phase in the milestone roadmap addresses this. This item either needs to be implemented or formally descoped from SC-6.

**Relation between gaps:** These are independent concerns. Gap 1 is a one-line code fix. Gap 2 is either a planned feature addition (requires external allergen DB or similarity tool integration) or a scope reduction decision from Leo.

---

_Verified: 2026-04-10T21:12:59Z_
_Verifier: Claude (gsd-verifier)_
