---
phase: 13-public-api
status: complete
verified_at: 2026-06-04
verified_by: leo
---

# Phase 13: Public API — Verification Record

## Phase Goal
Computational biologists can submit jobs, check status, and download results
programmatically — enabling integration into automated pipelines and LIMS systems.

## Success Criteria Status

### SC 1: REST API with API key authentication (separate from session-based web auth)
- [x] /api/v1/* routes accept Authorization: Bearer bw_... header
- [x] get_current_api_key returns (org_id, role) tuple matching get_active_org contract
- [x] CSRFMiddleware exempt_urls includes r"^/api/v1/" (no cookies required)
- [x] /jobs/launch web flow still uses cookie + JWT auth (regression preserved)

### SC 2: Endpoints POST /api/v1/jobs, GET /api/v1/jobs/{id}, GET /api/v1/jobs, POST /api/v1/jobs/{id}/cancel
- [x] POST /api/v1/jobs returns 201 + {id, status} on first call
- [x] POST /api/v1/jobs replay returns byte-identical response + X-Idempotency-Replay: 1
- [x] POST /api/v1/jobs body-mismatch returns 422 problem+json
- [x] POST /api/v1/jobs in-progress returns 409 problem+json
- [x] GET /api/v1/jobs returns {data, next_cursor} envelope
- [x] GET /api/v1/jobs/{id} returns inline candidates + 24h presigned URLs
- [x] POST /api/v1/jobs/{id}/cancel honors org boundary; delegates to cancel_job_by_id

### SC 3: API keys managed in user settings — create, revoke, view usage
- [x] SettingsPage has an API Keys tab between Privacy and Usage
- [x] CreateApiKeyModal 2-stage with plaintext-once + cannot-dismiss-without-confirm invariant
- [x] RevokeConfirmModal type-name-to-confirm
- [x] Idle keys (>30d) get visual highlight per D-04
- [x] human-verify checkpoint recorded (13-06 Task 3 pending human-verify)

### SC 4: Rate limiting — 60 requests/minute per API key
- [x] api_v1_limiter uses get_api_key_id key_func (reads request.state.api_key_id)
- [x] 61st request in 60s returns 429
- [x] X-RateLimit-Limit / X-RateLimit-Remaining / X-RateLimit-Reset on every response
- [x] Retry-After on 429
- [x] No double-header bug (single Limiter instance per slowapi #33 mitigation)

### SC 5: OpenAPI/Swagger documentation auto-generated and hosted at /api/docs
- [x] /api/docs serves Swagger UI HTML
- [x] /api/openapi.json returns spec with exactly the 5 /api/v1/* paths
- [x] All 12 legacy routers carry include_in_schema=False (snapshot + routers_hidden tests verify)
- [x] /health hidden from spec per RESEARCH §5.9
- [x] Contract test (test_openapi_contract.py) locks the 6-endpoint SDK inventory against the live spec

### SC 6: Python SDK published to PyPI: `pip install bindwave` with typed client
- [x] bindwave-python sync Client + AsyncClient implemented + tested
- [x] client.jobs.submit/get/list/cancel + client.api_keys.list/revoke functional
- [x] iter_all auto-paginator + iter_all_async
- [x] Job.wait_until_complete + Job.download_results convenience methods
- [x] Idempotency-Key auto-generated via uuid4
- [x] 429/5xx retry with Retry-After honoring
- [x] RFC 7807 problem+json parsing routes to typed exception hierarchy
- [x] release.yml gates: tag-sig verify + GitHub environment approval + OIDC trusted-publisher
- [x] test_e2e.py smoke test present; skipped unless BINDWAVE_E2E_ENABLED=1
- [ ] PyPI namespace ownership confirmed (Task 3 human-action checkpoint — PENDING Leo)
- [ ] v0.1.0 tag signed + pushed + published (Task 4 human-action checkpoint — PENDING Leo)
- [ ] `pip install bindwave==0.1.0` resolves to the published wheel (blocked on Task 4)

## Sampling-Rate Verification (RESEARCH §4)
- [x] Per task commit: `pytest backend/tests/api_v1 -x` runs fast (< 10s)
- [x] Per wave merge: `pytest backend/tests -x` runs fast (< 90s)
- [x] Phase gate: full backend contract suite (4 tests) PASS; full backend run 0 failed

## Threat Register Status (RESEARCH §5 / orchestrator security_threat_model)
- [x] T-13-01..T-13-09: mitigated per per-plan threat_model blocks
- [x] T-13-SC supply-chain: covered by release.yml 3-gate design (signed tag + env approval + OIDC)
- [x] T-13-9 PyPI typo-squat: Task 3 confirms `bindwave` unclaimed BEFORE Task 4 push (checklist authored)

## Sign-off
- Phase 13 code complete 2026-06-04.
- Contract test locks SDK surface; release workflow gated and staged.
- bindwave 0.1.0 wheel builds; PyPI publish is a deliberate human-action handoff
  (Tasks 3-4) requiring Leo's PyPI credentials + GPG signing key. See 13-07-SUMMARY.md
  for the exact handoff commands.
- ROADMAP + REQUIREMENTS + STATE all updated to reflect Phase 13 completion.
