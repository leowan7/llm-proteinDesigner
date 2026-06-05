---
phase: 13
slug: public-api
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-04
---

# Phase 13 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Sourced from `13-RESEARCH.md` §4 (Validation Architecture). Update both sides if the test map changes.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.5 + pytest-asyncio 0.24.0 + respx 0.22.0 (backend); pytest + httpx mocks (bindwave-python) |
| **Config file** | `backend/pytest.ini` (existing); `bindwave-python/pyproject.toml [tool.pytest.ini_options]` (new) |
| **Quick run command** | `pytest backend/tests/api_v1 -x` |
| **Full suite command** | `pytest backend/tests -x --tb=short` |
| **Estimated runtime** | ~5s quick (api_v1 only), ~60-90s full backend, ~10s SDK |

---

## Sampling Rate

- **After every task commit:** Run `pytest backend/tests/api_v1 -x` (new directory only — fast feedback)
- **After every plan wave:** Run `pytest backend/tests -x --tb=short` (full backend suite — must stay green so regression rows hold)
- **Phase gate (before `/gsd:verify-work`):** Full backend suite green + `pytest bindwave-python/tests -x` green + manual smoke on Swagger UI at `/api/docs`
- **Max feedback latency:** ~5 seconds (quick run)

---

## Per-Task Verification Map

| Req ID | Behavior | Plan | Test Type | Automated Command | File Exists | Status |
|--------|----------|------|-----------|-------------------|-------------|--------|
| API-01 | create key returns plaintext once; DB stores hash | 13-01 / 13-04 | unit | `pytest backend/tests/api_v1/test_api_keys.py::test_create_returns_plaintext -x` | ❌ W0 | ⬜ pending |
| API-02 | authn returns (org_id, role) tuple matching get_active_org | 13-02 | unit | `pytest backend/tests/api_v1/test_auth.py::test_returns_org_role_tuple -x` | ❌ W0 | ⬜ pending |
| API-03 | revoked key returns 401 | 13-04 | unit | `pytest backend/tests/api_v1/test_api_keys.py::test_revoked_key_rejects -x` | ❌ W0 | ⬜ pending |
| API-04 | idempotency replay returns stored response | 13-03 | integration | `pytest backend/tests/api_v1/test_idempotency.py::test_replay -x` | ❌ W0 | ⬜ pending |
| API-04 | idempotency body-mismatch returns 422 | 13-03 | integration | `pytest backend/tests/api_v1/test_idempotency.py::test_body_mismatch_returns_422 -x` | ❌ W0 | ⬜ pending |
| API-04 | idempotency in-progress returns 409 | 13-03 | integration | `pytest backend/tests/api_v1/test_idempotency.py::test_pending_returns_409 -x` | ❌ W0 | ⬜ pending |
| API-05 | cursor pagination skips inserted rows | 13-03 | integration | `pytest backend/tests/api_v1/test_pagination.py::test_cursor_stable_under_insert -x` | ❌ W0 | ⬜ pending |
| API-05 | cursor decodes safely on garbage input | 13-03 | unit | `pytest backend/tests/api_v1/test_cursor.py::test_garbage_input -x` | ❌ W0 | ⬜ pending |
| API-06 | get-job inline returns presigned URLs | 13-03 | integration | `pytest backend/tests/api_v1/test_jobs_get.py -x` | ❌ W0 | ⬜ pending |
| API-07 | error envelope is application/problem+json on /api/v1/* | 13-03 | integration | `pytest backend/tests/api_v1/test_errors.py::test_problem_json -x` | ❌ W0 | ⬜ pending |
| API-07 | web-flow errors keep their existing shape | regression | regression | `pytest backend/tests/jobs -x` (existing, must still pass) | ✅ exists | ⬜ pending |
| API-08 | rate-limit headers present on 200 + 429 | 13-03 | integration | `pytest backend/tests/api_v1/test_rate_limit.py -x` | ❌ W0 | ⬜ pending |
| API-09 | OpenAPI spec contains ONLY /api/v1/* paths | 13-01 / 13-07 | contract | `pytest backend/tests/contract/test_openapi_snapshot.py -x` | ❌ W0 | ⬜ pending |
| API-09 | every legacy router has include_in_schema=False | 13-01 | unit | `pytest backend/tests/contract/test_routers_hidden.py -x` | ❌ W0 | ⬜ pending |
| API-10 | 61st request in 60s returns 429 | 13-03 | integration | `pytest backend/tests/api_v1/test_rate_limit.py::test_60rpm -x` | ❌ W0 | ⬜ pending |
| API-11 | /api/docs returns Swagger UI HTML | 13-01 | smoke | `curl -s http://localhost:8000/api/docs \| grep swagger-ui` | manual | ⬜ pending |
| API-12 | SDK contract: spec covers every SDK endpoint | 13-07 | contract | `pytest backend/tests/contract/test_openapi_contract.py -x` | ❌ W0 | ⬜ pending |
| API-12 | SDK end-to-end against test server | 13-05 / 13-07 | E2E | `pytest bindwave-python/tests/test_e2e.py -x` | ❌ separate repo | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Test scaffolding that MUST exist before any implementation work in Waves 1-3.

**Backend test directory (new):**
- [ ] `backend/tests/api_v1/__init__.py` — package marker
- [ ] `backend/tests/api_v1/conftest.py` — fixtures: synthetic api_key fixture, idempotency-key generator, X-Org-Id-bypass fixture, RFC 7807 response asserters
- [ ] `backend/tests/api_v1/test_api_keys.py` — stubs for API-01, API-03
- [ ] `backend/tests/api_v1/test_auth.py` — stubs for API-02
- [ ] `backend/tests/api_v1/test_idempotency.py` — stubs for API-04 (replay + body-mismatch + in-progress)
- [ ] `backend/tests/api_v1/test_pagination.py` — stubs for API-05 (cursor-stable-under-insert)
- [ ] `backend/tests/api_v1/test_cursor.py` — stubs for API-05 (garbage-input)
- [ ] `backend/tests/api_v1/test_jobs_get.py` — stubs for API-06
- [ ] `backend/tests/api_v1/test_errors.py` — stubs for API-07
- [ ] `backend/tests/api_v1/test_rate_limit.py` — stubs for API-08, API-10

**Contract test directory (new):**
- [ ] `backend/tests/contract/__init__.py` — package marker
- [ ] `backend/tests/contract/test_openapi_snapshot.py` — locks the published surface (API-09)
- [ ] `backend/tests/contract/test_routers_hidden.py` — asserts every legacy router has `include_in_schema=False` (API-09)
- [ ] `backend/tests/contract/test_openapi_contract.py` — asserts spec covers SDK endpoints (API-12)
- [ ] `backend/tests/contract/_openapi_paths_snapshot.txt` — snapshot fixture
- [ ] `backend/tests/contract/_sdk_contract_v0_1_0.py` — SDK endpoint inventory

**SDK test directory (separate repo `bindwave-python/`):**
- [ ] `bindwave-python/tests/conftest.py` — respx-based mock transport
- [ ] `bindwave-python/tests/test_client.py` — client construction + auth header
- [ ] `bindwave-python/tests/test_jobs.py` — submit / get / list / cancel
- [ ] `bindwave-python/tests/test_pagination.py` — `iter_all` auto-paginator
- [ ] `bindwave-python/tests/test_e2e.py` — end-to-end against a running test server (API-12 E2E)

**Dependencies:**
- [ ] `backend/requirements.txt` — no additions (stdlib `hmac`+`hashlib` handle API-key hashing per RESEARCH §2.10; existing `httpx`+`asyncpg`+`redis` cover the rest)
- [ ] `bindwave-python/pyproject.toml` — runtime: `httpx>=0.27`, `pydantic>=2.0`; dev: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Swagger UI renders at `/api/docs` | API-11 | UI artifact, no DOM assertion needed | `curl -s http://localhost:8000/api/docs \| grep -q swagger-ui` AND visually open the URL after dev server starts |
| PyPI release publishes `bindwave` (not `kendrew`) | API-12 / D-09 | Real PyPI namespace claim, tag-push gated | After tagging v0.1.0, verify `pip install bindwave==0.1.0` resolves to the published wheel and `python -c "import bindwave; print(bindwave.__version__)"` returns `"0.1.0"` |
| Plaintext API key shown exactly once in Settings | API-01 / D-03 specifics | UX confirmation pattern ("I have saved this key") | Create key in Settings → API Keys tab; confirm modal shows plaintext + copy-to-clipboard + acknowledgement checkbox before close |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
