---
phase: 02-agent-and-structure-input
plan: "01"
subsystem: api
tags: [pydantic, anthropic, biopython, supabase, pytest, jobspec, wizard]

requires:
  - phase: 01-foundation
    provides: Supabase jobs table schema and backend config pattern

provides:
  - JobSpec and ValidationResult Pydantic models (agent/jobspec.py)
  - WIZARD_PARAMS definitions for rfdiffusion, bindcraft, boltzgen (agent/wizard.py)
  - StructureSummary, NormalizationResult, HotspotCheck Pydantic models (pdb_utils/models.py)
  - anthropic_api_key, rcsb_base_url, uniprot_base_url, agent settings in config.py
  - DB migration adding job_spec JSONB and pdb_path TEXT to jobs table
  - Full test scaffold: 24 stubs + 3 passing JobSpec tests across 6 test files

affects:
  - 02-02 (PDB pipeline uses pdb_utils.models types)
  - 02-03 (agent backend uses JobSpec, ValidationResult, WIZARD_PARAMS)
  - 02-04 (frontend displays StructureSummary card data)
  - 03-* (Phase 3 job dispatch consumes JobSpec from jobs.job_spec column)

tech-stack:
  added:
    - anthropic==0.86.0 (Claude SDK for agent backend)
    - biopython==1.86 (PDB parsing and SASA calculations)
    - respx==0.22.0 (httpx mocking for RCSB/UniProt fetch tests)
  patterns:
    - Pydantic BaseModel for all inter-component contracts
    - Literal["rfdiffusion", "bindcraft", "boltzgen"] as tool discriminator throughout
    - pytest.skip("STUB — implementation in Plan 02-XX") convention for scaffold stubs
    - FakeRedis class in conftest for session tests with no external dependency

key-files:
  created:
    - backend/agent/jobspec.py (JobSpec, ValidationResult)
    - backend/agent/wizard.py (WIZARD_PARAMS, WizardParam)
    - backend/pdb_utils/models.py (StructureSummary, NormalizationResult, HotspotCheck)
    - supabase/migrations/20260319000001_jobspec.sql
    - backend/tests/agent/test_jobspec.py (3 passing tests)
    - backend/tests/agent/test_tools.py (stubs)
    - backend/tests/agent/test_session.py (stubs)
    - backend/tests/pdb/test_normalize.py (stubs)
    - backend/tests/pdb/test_fetch.py (stubs)
    - backend/tests/pdb/test_validate.py (stubs)
    - backend/tests/fixtures/test_structure.pdb
  modified:
    - backend/config.py (added anthropic_api_key, rcsb_base_url, uniprot_base_url, agent settings)
    - backend/requirements.txt (added anthropic, biopython, respx)
    - backend/tests/conftest.py (added test_pdb_path, temp_dir, mock_redis fixtures)

key-decisions:
  - "Renamed backend/pdb/ to backend/pdb_utils/ — 'pdb' is a Python stdlib module name; naming the package 'pdb' caused pytest's debugger to fail (AttributeError: module 'pdb' has no attribute 'set_trace') because sys.path placed backend/ before stdlib. Downstream plans must use 'from pdb_utils.models import ...'"
  - "WIZARD_PARAMS uses 3 rfdiffusion params, 4 bindcraft params, 3 boltzgen params — curated to essential inputs only; advanced params deferred to v2"
  - "agent_model defaulted to claude-sonnet-4-5 matching the executor model available in this project environment"

patterns-established:
  - "All inter-plan contracts defined as Pydantic BaseModel — no raw dicts at module boundaries"
  - "Test stubs use pytest.skip with STUB marker and target plan number for traceability"
  - "WizardParam.description field explains the reasoning behind each default value"

requirements-completed: [INPUT-01, INPUT-05, AGENT-01, AGENT-02, AGENT-04, AGENT-05]

duration: 5min
completed: 2026-03-19
---

# Phase 02 Plan 01: Type Contracts and Test Scaffolds Summary

**Pydantic type contracts (JobSpec, StructureSummary, WizardParam), WIZARD_PARAMS defaults, Supabase migration, and full test scaffold (27 tests: 3 green, 24 skipped stubs) establishing the interface layer for all Phase 2 plans**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-19T12:32:40Z
- **Completed:** 2026-03-19T12:37:45Z
- **Tasks:** 2
- **Files modified:** 14

## Accomplishments

- All type contracts importable and enforcing constraints (tool Literal blocks invalid inputs)
- WIZARD_PARAMS covers 3 tools with Ranomics-curated defaults and explanations for each
- DB migration extends jobs table with job_spec JSONB and pdb_path TEXT columns
- 6 test files scaffolded across pdb_utils and agent subsystems; 3 JobSpec tests pass green immediately

## Task Commits

Each task was committed atomically:

1. **Task 1: Type contracts, wizard params, config, and DB migration** - `7865fc1` (feat)
2. **Task 2: Test scaffolds for all Phase 2 requirements** - `8c2ec4b` (feat)

**Plan metadata:** pending (docs commit follows)

## Files Created/Modified

- `backend/agent/jobspec.py` - JobSpec and ValidationResult Pydantic models
- `backend/agent/wizard.py` - WIZARD_PARAMS for rfdiffusion (3), bindcraft (4), boltzgen (3)
- `backend/pdb_utils/models.py` - StructureSummary, NormalizationResult, HotspotCheck
- `backend/config.py` - Added anthropic_api_key, rcsb_base_url, uniprot_base_url, agent_model, agent_max_tokens, agent_session_ttl_seconds
- `backend/requirements.txt` - Added anthropic==0.86.0, biopython==1.86, respx==0.22.0
- `supabase/migrations/20260319000001_jobspec.sql` - job_spec JSONB + pdb_path TEXT + idx_jobs_tool index
- `backend/tests/conftest.py` - test_pdb_path, temp_dir, mock_redis fixtures added
- `backend/tests/fixtures/test_structure.pdb` - 3-residue alanine chain for PDB tests
- `backend/tests/pdb/test_normalize.py` - 4 stubs (INPUT-01, INPUT-05)
- `backend/tests/pdb/test_fetch.py` - 6 stubs (INPUT-02, INPUT-03, INPUT-04)
- `backend/tests/pdb/test_validate.py` - 5 stubs (AGENT-04)
- `backend/tests/agent/test_tools.py` - 4 stubs (AGENT-01, AGENT-02)
- `backend/tests/agent/test_session.py` - 3 stubs (AGENT-03)
- `backend/tests/agent/test_jobspec.py` - 3 passing tests (AGENT-05)

## Decisions Made

- Renamed `backend/pdb/` to `backend/pdb_utils/` to avoid stdlib collision (see deviation below)
- `agent_model` set to `claude-sonnet-4-5` matching current project environment
- WIZARD_PARAMS designed with description fields explaining each default, not just listing it

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Renamed backend/pdb/ package to backend/pdb_utils/**
- **Found during:** Task 2 (test scaffold execution)
- **Issue:** Python's stdlib has a module named `pdb` (the debugger). Naming our package `backend/pdb/` caused pytest to fail with `AttributeError: module 'pdb' has no attribute 'set_trace'` because `backend/` on sys.path caused our package to shadow the stdlib module before pytest's debugging hooks could load it.
- **Fix:** Renamed `backend/pdb/` to `backend/pdb_utils/`. All downstream plans (02-02, 02-03, 02-04) must use `from pdb_utils.models import ...` instead of `from pdb.models import ...`.
- **Files modified:** backend/pdb_utils/__init__.py, backend/pdb_utils/models.py (moved from backend/pdb/)
- **Verification:** `python -m pytest tests/agent/test_jobspec.py -x -q` — 3 passed, no INTERNALERROR
- **Committed in:** 8c2ec4b (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug)
**Impact on plan:** Required rename; downstream plans must reference `pdb_utils` not `pdb`. No functional scope change.

## Issues Encountered

None beyond the pdb naming conflict documented above.

## User Setup Required

None - no external service configuration required for this plan. The anthropic_api_key field added to config.py will need a value before Plan 02-03 agent backend tests run against the live API.

## Next Phase Readiness

- Plan 02-02 (PDB pipeline): can import from `pdb_utils.models` for StructureSummary, NormalizationResult, HotspotCheck; test stubs exist for all behaviors
- Plan 02-03 (agent backend): can import JobSpec, ValidationResult from `agent.jobspec`; WIZARD_PARAMS from `agent.wizard`; mock_redis fixture ready
- Plan 02-04 (frontend): StructureSummary field names confirmed for API contract
- All test scaffolds properly gated with STUB markers — no false greens

---
*Phase: 02-agent-and-structure-input*
*Completed: 2026-03-19*

## Self-Check: PASSED

- FOUND: backend/agent/jobspec.py
- FOUND: backend/agent/wizard.py
- FOUND: backend/pdb_utils/models.py
- FOUND: backend/config.py (with anthropic_api_key)
- FOUND: supabase/migrations/20260319000001_jobspec.sql
- FOUND: backend/tests/agent/test_jobspec.py (3 passing tests)
- FOUND: backend/tests/fixtures/test_structure.pdb
- FOUND commit 7865fc1: feat(02-01): type contracts, wizard params, config, and DB migration
- FOUND commit 8c2ec4b: feat(02-01): test scaffolds for all Phase 2 requirements
- FOUND commit 71f9fa8: docs(02-01): complete type-contracts-and-test-scaffolds plan
