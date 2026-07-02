---
phase: 13-public-api
plan: 07
subsystem: public-api-release
status: complete
tags: [phase-13, release, verification, pypi-publish, contract-test]
requires:
  - 13-03 (/api/v1/jobs router)
  - 13-04 (/api/v1/api-keys + sync Client)
  - 13-05 (AsyncClient + convenience methods)
  - 13-06 (Settings API Keys tab)
provides:
  - OpenAPI-vs-SDK contract test locking the published surface
  - bindwave-python PyPI release workflow (3-gate, staged)
  - api_key_pepper rotation runbook
  - Phase 13 verification record + planning-artifact close-out
affects:
  - .planning/REQUIREMENTS.md
  - .planning/ROADMAP.md
  - .planning/STATE.md
tech-stack:
  added: [pypa/gh-action-pypi-publish (OIDC trusted-publisher), hatch build]
  patterns: [frozen-SDK-contract, OpenAPI snapshot lock, signed-tag release gate]
key-files:
  created:
    - backend/tests/contract/_sdk_contract_v0_1_0.py
    - backend/tests/contract/test_openapi_contract.py
    - bindwave-python/.github/workflows/release.yml
    - bindwave-python/tests/test_e2e.py
    - .planning/phases/13-public-api/13-VERIFICATION.md
  modified:
    - docs/deploy.md
    - .planning/REQUIREMENTS.md
    - .planning/ROADMAP.md
    - .planning/STATE.md
decisions:
  - Frozen SDK contract encodes trailing-slash collection paths to match emitted spec
  - Contract test imports from tests.contract.* (F9 sys.path convention)
  - release.yml uses 3 supply-chain gates; PyPI publish deferred to human-action checkpoints
metrics:
  duration: ~35 min
  completed: 2026-07-02
---

# Phase 13 Plan 07: Public API Close-out + Release Coordinator Summary

**One-liner:** Locked the published `/api/v1` surface against the bindwave-python SDK via a frozen-contract OpenAPI test, staged a 3-gate PyPI release workflow (signed tag + environment approval + OIDC trusted-publisher), documented the api_key_pepper rotation runbook, and closed out Phase 13 across all planning artifacts — with the actual PyPI publish left as a deliberate human-action handoff.

## What Was Built (Tasks 1-2)

### Task 1 — Contract test + SDK inventory + release workflow + E2E + rotation docs
- `backend/tests/contract/_sdk_contract_v0_1_0.py` — frozen list of 6 SDK endpoints (4 jobs + 2 api-keys) for v0.1.0. Paths carry trailing slashes on collections to match the emitted OpenAPI spec verbatim.
- `backend/tests/contract/test_openapi_contract.py` — 2 tests: (a) every SDK contract entry appears in `app.openapi()["paths"]` with matching method + documented status; (b) all contract paths are `/api/v1/*`. Imports via `from tests.contract._sdk_contract_v0_1_0 import ...` (F9 convention).
- `bindwave-python/.github/workflows/release.yml` — tag-push (`v*`) PyPI publish with 3 gates: signed-tag verify (`git tag --verify`), `pypi-release` GitHub environment (second-maintainer approval), OIDC trusted-publisher (`id-token: write`, no long-lived token).
- `bindwave-python/tests/test_e2e.py` — staging smoke test (submit → cancel → get). Skipped unless `BINDWAVE_E2E_ENABLED=1`.
- `docs/deploy.md` — new "API key pepper rotation (Phase 13)" section: 24h grace window, `API_KEY_PEPPER` / `API_KEY_PEPPER_PREV` runbook, cold-storage caveat, Last Pepper Rotations table.

### Task 2 — Planning artifact close-out
- `.planning/REQUIREMENTS.md` — API-01..API-12 flipped to `[x]` / Validated (list + traceability table); PLAT-V2-01 already Validated; verification footnote added. Coverage footer left untouched per plan.
- `.planning/ROADMAP.md` — Phase 13 ticked `[x]` (completed 2026-06-04); Plans TBD → 7-plan list all `[x]`; Verified footnote; moved to Completed table (7/7, 2026-06-04); removed from Post-launch table.
- `.planning/STATE.md` — completed_phases 10→11, completed_plans 55→62, percent 77→91; position = Phase 13 complete; 4 Phase-13 decisions added.
- `.planning/phases/13-public-api/13-VERIFICATION.md` — 41 `[x]` must-haves across SC 1-6 + sampling rates + threat register.

## Verification Results

| Check | Result |
|-------|--------|
| Contract suite (`pytest tests/contract -x`) | **4 passed** (openapi_snapshot, routers_hidden, openapi_contract, sdk_contract_endpoints_are_only_v1) |
| SDK contract length | `len(SDK_CONTRACT_V0_1_0) == 6` ✓ |
| SDK E2E collection (`pytest tests/test_e2e.py -q`) | **1 skipped** (collectable, not error) |
| Full backend no-regression (`pytest -q -p no:cacheprovider`) | **425 passed, 19 skipped, 6 xfailed, 0 failed** (baseline 423 → +2 new contract tests) |
| REQUIREMENTS Validated count | 23 (≥13) ✓ |
| VERIFICATION `[x]` count | 41 (≥30) ✓ |
| STATE completed_phases | 11 ✓ |

Contract test confirmed passing green against the REAL `app.openapi()` spec.

## Deviations from Plan

### 1. [Gotcha correction — data accuracy] Frozen SDK contract uses trailing-slash collection paths
- **Found during:** Task 1
- **Issue:** The plan's verbatim `_sdk_contract_v0_1_0.py` block hardcoded slashless collection paths (`/api/v1/jobs`, `/api/v1/api-keys`). The real FastAPI spec emits trailing slashes (`/api/v1/jobs/`, `/api/v1/api-keys/`) because the endpoints are `@router.get("/")` under a prefixed router. The contract test does an exact `path in spec["paths"]`, so slashless entries would fail.
- **Fix:** Built the frozen contract to match the verified ground-truth spec (`app.openapi()['paths']`): POST `/api/v1/jobs/` (201), GET `/api/v1/jobs/` (200), GET `/api/v1/jobs/{job_id}` (200), POST `/api/v1/jobs/{job_id}/cancel` (200), GET `/api/v1/api-keys/` (200), POST `/api/v1/api-keys/{key_id}/revoke` (200). Kept the plan's `req_fields`/`resp_fields`/`since` shape. Contract test passes green.
- **Commit:** 85bc64b

### 2. [Gotcha correction — avoid count regression] STATE completed_phases set to 11, not 9
- **Found during:** Task 2
- **Issue:** The plan text said "set completed_phases=9". That was a stale literal — the current STATE.md already had `completed_phases: 10`. Setting 9 would REGRESS the count.
- **Fix:** Phase 13 completing means the correct value is 11 (10 + Phase 13). Set `completed_phases: 11`, `completed_plans: 62` (55 + 7 Phase-13 plans), `percent: 91` (62/68).
- **Commit:** 9d63d69

### 3. [Honesty — git-verified status] PyPI-publish claims softened to "pending human handoff"
- **Found during:** Task 2
- **Issue:** The plan's VERIFICATION/ROADMAP templates asserted "bindwave 0.1.0 live on PyPI" / "pip install bindwave works". Those depend on Tasks 3-4 (human-action checkpoints) which are NOT executed in this run.
- **Fix:** VERIFICATION marks the 3 publish-dependent items `[ ]` (PENDING Leo); ROADMAP Verified footnote says "PyPI publish pending the two human-action checkpoints"; STATE position notes the deferral. All wording is git-verified-honest.
- **Commit:** 9d63d69

## PENDING — Human-Action Handoff (Tasks 3 & 4, NOT executed)

These require Leo's PyPI credentials + GPG signing key and were intentionally not run.

### Task 3 — Confirm PyPI namespace ownership BEFORE tagging
1. Log into https://pypi.org with the account that will own `bindwave`; confirm 2FA enabled.
2. Verify `bindwave` is unclaimed: visit https://pypi.org/project/bindwave/ — should 404. If it shows another project, HALT and rename the SDK.
3. Configure OIDC trusted publisher at https://pypi.org/manage/account/publishing/ — add a Pending publisher for project `bindwave`:
   - Owner: `<your-github-org>`
   - Repository: `bindwave-python`
   - Workflow: `release.yml`
   - Environment: `pypi-release`
4. Configure GitHub environment in bindwave-python: Settings → Environments → New → `pypi-release`; add yourself as Required reviewer; deployment tag rule `v*`.
5. Confirm signing key: `gpg --list-secret-keys --keyid-format=long`; register the key on GitHub (Settings → SSH and GPG keys → GPG).

Resume signal: "namespace confirmed" once steps 1-5 pass.

### Task 4 — Sign + push v0.1.0 tag to trigger PyPI publish
```bash
# 1-3: confirm versions
grep '__version__' bindwave-python/src/bindwave/__init__.py   # expect 0.1.0
grep 'version' bindwave-python/pyproject.toml                 # expect version = "0.1.0"
grep '0.1.0' bindwave-python/CHANGELOG.md                     # expect ## [0.1.0] - 2026-06-04

# 4: full SDK suite locally
cd bindwave-python && PYTHONPATH=src python -m pytest tests/ -x

# 5: E2E smoke against staging
BINDWAVE_E2E_ENABLED=1 BINDWAVE_API_KEY=bw_live_xxx \
  BINDWAVE_BASE_URL=https://staging-api.bindwave.com/api/v1 \
  pytest bindwave-python/tests/test_e2e.py -m e2e -v

# 6: create the SIGNED tag
git -C bindwave-python tag -s v0.1.0 -m "bindwave 0.1.0 — Phase 13"

# 7: push the tag (fires the release workflow)
git -C bindwave-python push origin v0.1.0

# 8: watch GitHub Actions — verify-tag-signature passes, publish pauses for
#    second-maintainer approval, then hatch build + pypa/gh-action-pypi-publish (~2 min)

# 9: verify on PyPI: https://pypi.org/project/bindwave/0.1.0/

# 10-11: from a clean env
pip install bindwave==0.1.0
python -c "import bindwave; print(bindwave.__version__)"          # -> 0.1.0
python -c "from bindwave import Client; c = Client(api_key='bw_test_x'); print(c.jobs)"
```
Resume signal: "published v0.1.0" once all steps pass and pip install works from a clean env.

## Commits
- `85bc64b` — test(13-07): OpenAPI contract test + frozen SDK inventory + release workflow + E2E + pepper rotation runbook
- `9d63d69` — docs(13-07): Phase 13 close-out — REQUIREMENTS/ROADMAP/STATE + 13-VERIFICATION.md

## Self-Check: PASSED
- All 5 created files exist on disk.
- Both commits (85bc64b, 9d63d69) present in git log.
