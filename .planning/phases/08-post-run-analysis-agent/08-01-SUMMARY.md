---
phase: 08-post-run-analysis-agent
plan: 01
subsystem: api
tags: [pandas, analysis, agent-tools, ranking, protein-design, bindcraft]

requires:
  - phase: 02-agent-and-structure-input
    provides: TOOL_DEFINITIONS list and dispatch_tool switch in agent/tools.py
  - phase: 03-job-execution-frontend-and-billing
    provides: job_candidates DB table with rank/pdb_key/scores JSONB schema

provides:
  - In-memory candidate cache keyed by job_id (agent/analysis/cache.py)
  - Pandas-based ranking with percentile annotations (agent/analysis/ranking.py)
  - Multi-criteria AND filtering (agent/analysis/ranking.py)
  - Three new agent tools: load_job_results, analyze_candidates, flag_red_flags
  - METRIC_THRESHOLDS dict with 7 BindCraft metrics and lower_is_better flags
  - Per-user ownership check on all DB queries (T-08-01)
  - Metric interpretation profiles template (agent/reference/03_metric_profiles.md)
  - Experimental guidance profiles template (agent/reference/04_guidance_profiles.md)
  - System prompt extended with POST-RUN ANALYSIS workflow

affects:
  - 08-02-PLAN (PDB structural features — uses agent.analysis cache)
  - 08-03-PLAN (report generation — uses ranked candidates from this plan)
  - 08-04-PLAN (refolding jobs — agent workflow from this plan's system prompt)

tech-stack:
  added:
    - pandas==2.2.3 (ranking, filtering, distribution stats)
    - fpdf2==2.8.7 (PDF report generation — added to requirements.txt for Plan 08-03)
    - pyyaml==6.0.2 (transitive dep, pinned explicitly)
  patterns:
    - Analysis subpackage pattern: cache.py / ranking.py / tools.py separation
    - Lazy DB import inside async handlers (same as agent/tools.py pattern)
    - TDD: test file written first, implementation written to pass tests

key-files:
  created:
    - backend/agent/analysis/__init__.py
    - backend/agent/analysis/cache.py
    - backend/agent/analysis/ranking.py
    - backend/agent/analysis/tools.py
    - backend/agent/reference/03_metric_profiles.md
    - backend/agent/reference/04_guidance_profiles.md
    - backend/tests/agent/test_analysis_tools.py
  modified:
    - backend/requirements.txt (added pandas, fpdf2, pyyaml)
    - backend/agent/system_prompt.py (_METRIC_PROFILES, _GUIDANCE_PROFILES, POST-RUN ANALYSIS section)
    - backend/agent/tools.py (3 new TOOL_DEFINITIONS entries + 3 dispatch_tool branches)

key-decisions:
  - "Lazy import of get_db_pool inside handle_load_job_results (not at module top) — matches existing tools.py pattern; tests patch db.connection.get_db_pool at the source module"
  - "Percentile computed as higher-raw-value = higher-percentile always, regardless of ascending sort flag — consistent definition for cross-metric comparison"
  - "In-memory cache is process-local; cache miss triggers DB re-fetch — acceptable for single-worker dev; multi-worker production uses Redis-backed session (future plan)"
  - "METRIC_THRESHOLDS hardcoded in tools.py per D-09; 03_metric_profiles.md provides human-readable reference for Leo to customize thresholds"

patterns-established:
  - "Analysis subpackage: cache.py owns state, ranking.py owns pure-function data transforms, tools.py owns async DB handlers — each layer testable independently"
  - "Threshold assessment: _assess_threshold() returns strong/passable/red_flag for each metric based on lower_is_better flag — used in both analyze_candidates and flag_red_flags"

requirements-completed: [ANA-01, ANA-02, ANA-03, ANA-04, ANA-05, ANA-06]

duration: 9min
completed: 2026-04-10
---

# Phase 08 Plan 01: Post-Run Analysis Infrastructure Summary

**Three-tool analysis pipeline with pandas ranking, in-memory cache, ownership-checked DB loading, proactive red flag detection, and metric/guidance profile reference files loaded into agent system prompt**

## Performance

- **Duration:** 9 min
- **Started:** 2026-04-10T18:04:15Z
- **Completed:** 2026-04-10T18:13:00Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments

- Analysis subpackage (cache, ranking, tools) with 25 unit tests — all pass
- Three new agent tools registered in TOOL_DEFINITIONS (8 total): load_job_results, analyze_candidates, flag_red_flags
- Ownership check (WHERE j.id = $1 AND j.user_id = $2) enforced in load_job_results — T-08-01 mitigated
- Metric profiles and guidance profiles templates created for Leo to populate with Ranomics-calibrated thresholds
- System prompt extended with metric profiles, guidance profiles, and POST-RUN ANALYSIS workflow instructions

## Task Commits

1. **Task 1: Analysis subpackage — cache, ranking engine, and tool handlers** - `73d11f9` (feat)
2. **Task 2: Metric/guidance profiles, system prompt update, TOOL_DEFINITIONS registration** - `c9fb309` (feat)

## Files Created/Modified

- `backend/agent/analysis/__init__.py` — Subpackage init
- `backend/agent/analysis/cache.py` — In-memory candidate cache with get_cached/set_cached/clear_cache
- `backend/agent/analysis/ranking.py` — rank_candidates (sort + percentile), filter_candidates (AND criteria), compute_distribution_stats
- `backend/agent/analysis/tools.py` — handle_load_job_results / handle_analyze_candidates / handle_flag_red_flags; METRIC_THRESHOLDS dict
- `backend/agent/reference/03_metric_profiles.md` — BindCraft/RFdiffusion/BoltzGen metric tables with literature thresholds and red flag combos
- `backend/agent/reference/04_guidance_profiles.md` — Expression, purification, SPR/BLI, yeast display, cyclic peptide protocols
- `backend/agent/system_prompt.py` — Added _METRIC_PROFILES/_GUIDANCE_PROFILES load + POST-RUN ANALYSIS section
- `backend/agent/tools.py` — 3 new TOOL_DEFINITIONS entries + 3 dispatch_tool elif branches
- `backend/requirements.txt` — pandas==2.2.3, fpdf2==2.8.7, pyyaml==6.0.2 added
- `backend/tests/agent/test_analysis_tools.py` — 25 tests covering all behaviors

## Decisions Made

- Lazy import of `get_db_pool` inside `handle_load_job_results` (not at module top) to match existing tools.py pattern. Tests patch `db.connection.get_db_pool` at the source module rather than `agent.analysis.tools.get_db_pool`.
- Percentile always computed as "higher raw value = higher percentile" regardless of sort direction. Provides a consistent definition for cross-metric comparison — callers interpret percentile as raw position in distribution.
- METRIC_THRESHOLDS hardcoded in tools.py as Python dict per D-09. The 03_metric_profiles.md reference file is the human-readable version for Leo to update — thresholds in tools.py should be kept in sync with that file.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- Test patch target: plan specified `patch("agent.analysis.tools.get_db_pool")` but `get_db_pool` is imported lazily inside the async function, so the name doesn't exist at module level. Fixed by patching `db.connection.get_db_pool` instead (Rule 1 auto-fix, corrected in same task before commit).

## Known Stubs

- `backend/agent/reference/03_metric_profiles.md`: All threshold values are literature-sourced starting points. Marked with `*LEO: Replace threshold values above with Ranomics/Kendrew-calibrated thresholds*`. These are intentional template stubs — Leo populates with proprietary benchmarking data.
- `backend/agent/reference/04_guidance_profiles.md`: Protocol recommendations are general best practices. Marked with `*LEO: Customize these protocols based on Ranomics internal SOPs*`. Intentional template stubs.

Both stubs are intentional authored-content placeholders per D-11 and D-19. They provide functional defaults while Leo customizes. These do not block any plan objectives — the infrastructure loads and renders whatever content is in the files.

## Threat Surface

T-08-01 mitigated: `handle_load_job_results` enforces `WHERE j.id = $1 AND j.user_id = $2` on the jobs ownership check before returning any candidates.
T-08-02 mitigated: `handle_analyze_candidates` and `handle_flag_red_flags` read only from the in-memory cache, which is populated exclusively by ownership-checked `load_job_results` calls — no direct DB bypass path exists.

## Next Phase Readiness

- Plan 08-02 (PDB structural features) can use `agent.analysis.cache` to load candidates and pass pdb_keys to BioPython analysis
- Plan 08-03 (report generation) can call `handle_analyze_candidates` and `handle_flag_red_flags` to populate report content
- Plan 08-04 (refolding jobs) builds on the analysis workflow instructions now in the system prompt

---
*Phase: 08-post-run-analysis-agent*
*Completed: 2026-04-10*
