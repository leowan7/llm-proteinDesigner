---
phase: 13-public-api
plan: 01
subsystem: backend/api-foundation
tags:
  - phase-13
  - public-api
  - schema
  - openapi-surface
  - foundation
dependency_graph:
  requires:
    - Phase 12 organizations table (api_keys FK to organizations.id)
    - Phase 12 org_role ENUM (role_at_creation column)
    - Phase 11 webhook_hmac_secret pattern (api_key_pepper mirrors it)
  provides:
    - api_keys table schema (for Plan 13-02 auth + Plan 13-04 CRUD)
    - api_key_idempotency table schema (for Plan 13-03 idempotency middleware)
    - Pydantic Settings fields: api_key_pepper, api_key_pepper_prev, api_v1_rate_limit, idempotency_ttl_hours
    - FastAPI app at title='Bindwave Public API' + docs_url='/api/docs'
    - include_in_schema=False on all 12 legacy routers (D-15 surface lock)
    - Test scaffold directories: backend/tests/api_v1/ + backend/tests/contract/
    - OpenAPI snapshot contract fixture (_openapi_paths_snapshot.txt)
  affects:
    - main.py (app metadata changed — affects Swagger UI title + docs URL)
    - All 12 legacy router files (include_in_schema=False flip)
    - REQUIREMENTS.md (12 new API-XX entries + PLAT-V2-01 promoted)
    - ROADMAP.md (SC 6 corrected: kendrew → bindwave)
tech_stack:
  added:
    - Postgres api_keys table (HMAC-SHA256 pepper, 2 partial indexes)
    - Postgres api_key_idempotency table (3-state: pending|completed, composite PK)
  patterns:
    - Dual-secret rotation (mirrors webhook_hmac_secret pattern in config.py)
    - OpenAPI snapshot test (Jest/Vitest-style contract enforcement)
    - include_in_schema=False router-level flag (FastAPI D-15 surface lock)
key_files:
  created:
    - supabase/migrations/20260607000001_api_keys.sql
    - supabase/migrations/20260607000002_api_key_idempotency.sql
    - backend/tests/api_v1/__init__.py
    - backend/tests/api_v1/conftest.py
    - backend/tests/api_v1/test_api_keys.py
    - backend/tests/api_v1/test_auth.py
    - backend/tests/api_v1/test_idempotency.py
    - backend/tests/api_v1/test_pagination.py
    - backend/tests/api_v1/test_cursor.py
    - backend/tests/api_v1/test_jobs_get.py
    - backend/tests/api_v1/test_errors.py
    - backend/tests/api_v1/test_rate_limit.py
    - backend/tests/contract/__init__.py
    - backend/tests/contract/test_openapi_snapshot.py
    - backend/tests/contract/test_routers_hidden.py
    - backend/tests/contract/_openapi_paths_snapshot.txt
  modified:
    - .planning/REQUIREMENTS.md
    - .planning/ROADMAP.md
    - backend/config.py
    - backend/main.py
    - backend/auth/router.py
    - backend/agent/router.py
    - backend/admin/router.py
    - backend/billing/router.py
    - backend/debug_routes.py
    - backend/jobs/router.py
    - backend/organizations/router.py
    - backend/pdb_utils/router.py
    - backend/sessions/router.py
    - backend/user/router.py
    - backend/webhooks/router.py
decisions:
  - "HMAC-SHA256 + pepper used instead of bcrypt (RESEARCH §2.10): API keys have 192-bit entropy; slow hash adds DoS amplification at 60 rpm with zero security benefit over HMAC"
  - "api_keys.bcrypt_hash column name retained for D-03 compatibility; COMMENT ON COLUMN documents the actual algorithm"
  - "Snapshot fixture starts empty: Plan 13-03 mounts the /api/v1 router; Plan 13-07 regenerates the snapshot with final surface"
  - "test_cursor.py is semi-active: stubs skip when api/v1/cursor not present, but run immediately once Plan 13-03 ships"
metrics:
  duration_minutes: 7
  completed_date: "2026-06-05"
  tasks_completed: 3
  tasks_total: 4
  files_created: 16
  files_modified: 15
---

# Phase 13 Plan 01: Wave 0 Foundation Summary

Wave 0 foundation for Phase 13 Public API: REQUIREMENTS.md mints API-01..API-12 with verbatim RESEARCH §3 descriptions, promotes PLAT-V2-01 to Validated, ROADMAP.md corrects `pip install kendrew` to `pip install bindwave` (D-09), Pydantic Settings gains 4 new fields for pepper rotation + rate limit + idempotency TTL, two Supabase migrations create the api_keys and api_key_idempotency tables, all 12 legacy routers get include_in_schema=False so only /api/v1/* paths will surface in the OpenAPI spec, and 14 test scaffold files (api_v1/ + contract/) are created with two ACTIVE contract tests passing green.

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | REQUIREMENTS.md + ROADMAP.md + config.py edits | 23f69ea | .planning/REQUIREMENTS.md, .planning/ROADMAP.md, backend/config.py |
| 2 | Migrations + main.py + 12 router flips | 9c2c615 | 2 migration SQLs, backend/main.py, 11 router files |
| 3 | Test scaffolds (api_v1/ + contract/) + snapshot fixture | 7d8b7d0 | 14 new files |
| 4 [BLOCKING] | supabase db push | PENDING — awaiting human action | — |

## Verification

### Task 1

- `grep -c "^- \[ \] \*\*API-" .planning/REQUIREMENTS.md` → 12
- `grep "pip install bindwave" .planning/ROADMAP.md` → matches
- `grep "pip install kendrew" .planning/ROADMAP.md` → no matches
- Coverage footer: "44 total (24 + 8 organizations + 12 API) plus 7 testing", "Mapped to phases: 51"
- `python -c "from config import settings; assert settings.api_v1_rate_limit == '60/minute'; assert settings.idempotency_ttl_hours == 25"` → passes

### Task 2

- Migration 1 has 1 CREATE TABLE + CONSTRAINT name_not_blank + 2 partial indexes + HMAC COMMENT
- Migration 2 has composite PK (api_key_id, idempotency_key) + status CHECK IN ('pending','completed')
- main.py: title='Bindwave Public API', docs_url='/api/docs', openapi_url='/api/openapi.json', include_in_schema=False on /health
- include_router count: 12 pre = 12 post (no spurious additions)
- All 11 router files contain include_in_schema=False; organizations/router.py has it twice

### Task 3

- 21 tests collected: 19 stubs + 2 active
- `pytest tests/contract/test_openapi_snapshot.py -x` → PASSED (empty spec == empty fixture)
- `pytest tests/contract/test_routers_hidden.py -x` → PASSED (all 12 routers flipped)

## Deviations from Plan

None — plan executed exactly as written.

The only architectural clarification: the plan listed 11 files in the router flip acceptance criteria (noting organizations counts once) but the task action correctly identified that organizations/router.py exports 2 routers and both need the flag. This was already captured in the plan's acceptance criteria ("must contain it AT LEAST TWICE").

## Known Stubs

- backend/tests/api_v1/test_cursor.py: test_round_trip and test_garbage_input import from api/v1/cursor which does not exist yet. Tests skip gracefully via try/except ImportError + pytest.skip — not a blocking stub, they will self-activate when Plan 13-03 ships the module.
- All other test_*.py files in api_v1/ are intentional stubs pointing to their downstream plans (13-02, 13-03, 13-04).

## Threat Flags

None — no new network endpoints, auth paths, or trust-boundary-crossing schema changes beyond what is covered by the plan's threat model (T-13-08, T-13-09 mitigations applied via Task 2 router flips and migration patterns matching existing org table shape).

## Blocking Checkpoint (Task 4)

Task 4 requires human action: `supabase db push` to apply the two new migrations to the live Supabase instance. This plan is committed in repo; the schema is NOT yet applied to the live DB. Plans 13-02 onwards can land in repo without the push, but integration tests that touch the live Postgres will fail until the tables exist.

Resume signal: type "applied" once the push succeeds and table checks pass.

## Self-Check: PASSED

- 23f69ea exists in git log ✓
- 9c2c615 exists in git log ✓
- 7d8b7d0 exists in git log ✓
- supabase/migrations/20260607000001_api_keys.sql exists ✓
- supabase/migrations/20260607000002_api_key_idempotency.sql exists ✓
- backend/tests/api_v1/conftest.py exists ✓
- backend/tests/contract/_openapi_paths_snapshot.txt exists ✓
- Both active contract tests pass: 2 passed ✓
