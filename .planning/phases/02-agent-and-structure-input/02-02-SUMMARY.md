---
phase: 02-agent-and-structure-input
plan: "02"
subsystem: api
tags: [biopython, httpx, fastapi, respx, pdb, uniprot, rcsb, sasa]

requires:
  - phase: 02-agent-and-structure-input plan 01
    provides: pdb_utils/models.py with StructureSummary, NormalizationResult, HotspotCheck; agent/jobspec.py with ValidationResult; config.py with rcsb_base_url, uniprot_base_url

provides:
  - pdb_utils/fetch.py: async RCSB download + UniProt search/resolve
  - pdb_utils/normalize.py: BioPython normalization pipeline (NMR, MSE, altloc)
  - pdb_utils/validate.py: SASA hotspot accessibility checks + preflight quality validation
  - pdb_utils/router.py: 4 FastAPI endpoints (upload, fetch, search, resolve) all protected by JWT auth
  - 17 passing unit tests across test_fetch.py, test_normalize.py, test_validate.py

affects:
  - 02-agent-and-structure-input plan 03 (agent tools call fetch/normalize/validate)
  - 02-agent-and-structure-input plan 04 (frontend calls /pdb/* endpoints)

tech-stack:
  added: [respx==0.22.0 (installed in system Python for test mocking)]
  patterns:
    - TDD with respx to mock httpx calls — no real network calls in tests
    - All three input paths (upload, accession fetch, UniProt resolve) converge on normalize_structure
    - Synchronous Bio.PDB work wrapped in asyncio.run_in_executor for event-loop safety

key-files:
  created:
    - backend/pdb_utils/fetch.py
    - backend/pdb_utils/normalize.py
    - backend/pdb_utils/validate.py
    - backend/pdb_utils/router.py
  modified:
    - backend/main.py (replaced try/except guard with direct pdb_router import)
    - backend/tests/pdb/test_fetch.py (replaced STUB skips with 6 real tests)
    - backend/tests/pdb/test_normalize.py (replaced STUB skips with 6 real tests)
    - backend/tests/pdb/test_validate.py (replaced STUB skips with 5 real tests)

key-decisions:
  - "All imports use pdb_utils.* (not pdb.*) — pdb directory renamed in Plan 02-01 to avoid shadowing Python stdlib debugger"
  - "respx.mock applied as decorator per test to avoid cross-test route pollution"
  - "resolve_pdb_for_uniprot raises HTTPStatusError for unknown accessions (404 propagates naturally); search_uniprot returns empty list for no-results (200 with empty array)"
  - "BioPython DisorderedAtom handles altloc selection implicitly (highest occupancy default) — no explicit altloc pass needed in normalize_structure"
  - "run_preflight_checks_async wraps synchronous CPU-bound work in run_in_executor to avoid blocking FastAPI event loop"

patterns-established:
  - "normalize_structure: single entry point for all input paths; raises ValueError for bad format or no AA residues"
  - "Router endpoints: validate extension/format at boundary, call normalize_structure, wrap external API errors as 4xx/5xx HTTPExceptions"
  - "Tests for async fetch functions: use @respx.mock decorator + pytest.mark.anyio"

requirements-completed: [INPUT-01, INPUT-02, INPUT-03, INPUT-04, INPUT-05, AGENT-04]

duration: 6min
completed: 2026-03-19
---

# Phase 02 Plan 02: PDB Ingest Pipeline Summary

**BioPython normalization pipeline (NMR/MSE/altloc), async RCSB/UniProt fetch, SASA hotspot validation, and 4 authenticated FastAPI endpoints — all three input paths converge on a single normalize_structure function**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-19T12:42:15Z
- **Completed:** 2026-03-19T12:48:30Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Full PDB ingest pipeline implemented: upload, RCSB accession fetch, UniProt accession resolve, and free-text UniProt search all pass through normalize_structure
- BioPython normalization handles NMR multi-model selection (model 0), MSE-to-MET mutation, and altloc disambiguation; changes reported back to caller
- SASA-based hotspot accessibility via ShrakeRupley; buried residues flagged with specific warning messages
- 17 unit tests written (TDD) using respx to mock all httpx calls — no network dependency in test suite

## Task Commits

Each task was committed atomically:

1. **Task 1: PDB fetch, normalize, and validate modules** - `f8811b1` (feat)
2. **Task 2: PDB FastAPI router with upload and fetch endpoints** - `c54431b` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `backend/pdb_utils/fetch.py` - fetch_pdb_file (RCSB), search_uniprot, resolve_pdb_for_uniprot
- `backend/pdb_utils/normalize.py` - normalize_structure with FirstModelSelect; raises ValueError on bad input
- `backend/pdb_utils/validate.py` - check_hotspot_accessibility (SASA), run_preflight_checks, async wrapper
- `backend/pdb_utils/router.py` - APIRouter prefix=/pdb: upload, fetch, search, resolve — all require JWT via get_current_user
- `backend/main.py` - replaced try/except guard with direct pdb_router import
- `backend/tests/pdb/test_fetch.py` - 6 tests (RCSB fetch, UniProt resolve, NL search) using respx mocks
- `backend/tests/pdb/test_normalize.py` - 6 tests (valid upload, invalid extension, MSE, NMR, altloc, change summary)
- `backend/tests/pdb/test_validate.py` - 5 tests (buried hotspot, accessible hotspot, missing residue, low resolution, no AA)

## Decisions Made

- All imports use `pdb_utils.*` — directory was renamed from `pdb/` in Plan 02-01 to avoid shadowing Python's stdlib `pdb` debugger module
- `respx.mock` applied as a decorator per test rather than as a context manager to keep test functions clean and avoid cross-test route contamination
- `resolve_pdb_for_uniprot` lets 404 propagate as HTTPStatusError; the router converts it to a 404 HTTPException with a user-friendly message
- BioPython's `DisorderedAtom` handles altloc automatically (highest occupancy wins) — no explicit altloc pass written
- `run_preflight_checks_async` wraps synchronous BioPython SASA work in `asyncio.run_in_executor` to keep the FastAPI event loop unblocked

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] respx not installed in system Python**
- **Found during:** Task 1 (TDD RED phase)
- **Issue:** `respx==0.22.0` is in requirements.txt but was not installed in the system Python environment used for testing; pytest collected test_fetch.py and immediately errored on `import respx`
- **Fix:** Ran `pip install respx==0.22.0` in the system Python
- **Files modified:** none (pip install only)
- **Verification:** Test collection succeeded after install
- **Committed in:** f8811b1 (part of Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking — missing package)
**Impact on plan:** No scope change; respx was already in requirements.txt, just not installed locally.

## Issues Encountered

- IDE linter reported `missing-module-attribute` for all `pdb_utils.*` imports throughout execution — this is a false positive from the language server lacking backend/ as a search root. All imports verified working at runtime via direct Python invocation.
- Auth integration tests (`test_auth.py`) fail without a running Supabase stack — this is pre-existing behaviour, not introduced by this plan.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Plan 02-03 (agent tools) can now import `fetch_pdb_file`, `search_uniprot`, `resolve_pdb_for_uniprot`, `normalize_structure`, `check_hotspot_accessibility`, and `run_preflight_checks` from `pdb_utils.*`
- Plan 02-04 (frontend) can call `/pdb/upload`, `/pdb/fetch`, `/pdb/search`, `/pdb/resolve` — all endpoints active and authenticated
- No blockers for Plan 02-03

---
*Phase: 02-agent-and-structure-input*
*Completed: 2026-03-19*
