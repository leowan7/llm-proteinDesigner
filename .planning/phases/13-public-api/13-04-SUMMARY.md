---
phase: 13-public-api
plan: 04
subsystem: public-api
tags: [phase-13, public-api, self-management, sdk-sync]
requires: [13-02, 13-03]
provides:
  - "/api/v1/api-keys GET + POST /{key_id}/revoke (SDK self-management)"
  - "/user/api-keys GET + POST + POST /{key_id}/revoke (web-flow mint-once)"
  - "bindwave.Client (sync) + jobs/api_keys resources + exception hierarchy"
affects:
  - backend OpenAPI surface (now 5 /api/v1 paths)
  - bindwave-python public surface (real Client replaces 13-02 placeholders)
tech-stack:
  added: []
  patterns:
    - "org-scoped SQL guard (WHERE organization_id = $1) on every key query"
    - "mint-once: plaintext only in POST /user/api-keys response, never on GET"
    - "SDK full-path constants matching OpenAPI spec verbatim (13-07 contract)"
key-files:
  created:
    - backend/api/v1/api_keys.py
    - backend/user/api_keys.py
    - bindwave-python/src/bindwave/_client.py
    - bindwave-python/src/bindwave/jobs.py
    - bindwave-python/src/bindwave/api_keys.py
    - bindwave-python/src/bindwave/_exceptions.py
    - bindwave-python/src/bindwave/_idempotency.py
    - bindwave-python/src/bindwave/types/__init__.py
    - bindwave-python/src/bindwave/types/job.py
    - bindwave-python/src/bindwave/types/api_key.py
    - bindwave-python/tests/test_client.py
    - bindwave-python/tests/test_jobs.py
    - bindwave-python/tests/test_api_keys.py
    - bindwave-python/examples/submit_and_wait.py
  modified:
    - backend/api/v1/__init__.py
    - backend/user/router.py
    - backend/main.py
    - backend/tests/api_v1/test_api_keys.py
    - backend/tests/contract/_openapi_paths_snapshot.txt
    - bindwave-python/src/bindwave/__init__.py
    - bindwave-python/README.md
decisions:
  - "org_role enum is owner/scientist/viewer (NOT admin/member as the plan text said); revoke restricted to owner"
  - "SDK base_url defaults to origin (https://app.bindwave.com); resources use full /api/v1/... paths so the strings match the OpenAPI spec verbatim for the 13-07 contract test"
  - "collection paths carry a trailing slash (/api/v1/api-keys/, /api/v1/jobs/) — matches emitted spec; snapshot regenerated from app.openapi()"
metrics:
  duration: ~35m
  completed: 2026-07-02
status: complete
---

# Phase 13 Plan 04: API-key self-management + bindwave-python sync Client Summary

Shipped the API-key self-management surface (SDK-side `/api/v1/api-keys` + web-side mint-once `/user/api-keys`) and the real synchronous `bindwave.Client` with working `jobs.submit/get/list/cancel` + `api_keys.list/revoke`, RFC 7807 exception routing, auto `Idempotency-Key`, and 429/5xx backoff — replacing the 13-02 placeholders.

## What shipped

**Backend**
- `backend/api/v1/api_keys.py` — `GET /api/v1/api-keys/` (org-scoped list, revoked excluded) + `POST /api/v1/api-keys/{key_id}/revoke` (org-scoped, 404 cross-org). Bearer/API-key auth via `require_role_api`; per-key rate limit.
- `backend/user/api_keys.py` — web-flow CRUD: `GET` (list, no plaintext), `POST` (mint; returns `{id, name, prefix, plaintext, role, created_at}` — plaintext ONCE), `POST /{key_id}/revoke`. Constructed `APIRouter(..., include_in_schema=False)` directly (D-15). `10/hour` create throttle. Cookie + `X-Org-Id` + `require_role` web auth.
- Wiring: `api/v1/__init__.py` includes `api_keys_router`; `main.py` registers `user_api_keys_router` standalone (its prefix is the full `/user/api-keys`, so it is NOT included on the `/user` parent — that would double-prefix).
- `_openapi_paths_snapshot.txt` regenerated from the real spec → 5 paths.

**SDK (bindwave-python)**
- `_client.py` sync `Client`: Bearer header, no `X-Org-Id`, `BINDWAVE_API_KEY`/`BINDWAVE_BASE_URL` env; 429 (Retry-After) + 5xx exponential backoff capped at `max_retries`; auto `Idempotency-Key` (uuid4 hex) on job submit; `>=400` → `parse_error_response`. Context-manager support.
- `jobs.py` / `api_keys.py` resources with full `/api/v1/...` path constants.
- `_exceptions.py` hierarchy (`BindwaveError` → Auth/RateLimit/Validation/Job/API) + `parse_error_response`. `_idempotency.py`. `types/` models. `__init__.py` exports real classes; `AsyncClient` stays a `NotImplementedError` stub (13-05).
- Tests, `examples/submit_and_wait.py`, README quickstart.

## Deviations from Plan

### Auto-fixed / adapted (Rule 1 — plan text vs. codebase reality)

**1. [Rule 1 — Wrong role enum] `owner/admin/member` → `owner/scientist/viewer`**
- **Found during:** Task 1 (reading `auth/org_dependencies.py` + migration `20260605000001_organizations.sql`).
- **Issue:** The plan (and the `require_role_api` docstring) said `public.org_role` is `owner/admin/member`. The actual enum is `owner/scientist/viewer`. Passing `"admin","member"` to `require_role` would silently reject scientists/viewers (only `owner` would ever match).
- **Fix:** List endpoints allow `owner/scientist/viewer`; revoke endpoints restrict to `owner` (the privileged role — no `admin` exists). Applies to both `/api/v1` and `/user` paths.
- **Files:** `backend/api/v1/api_keys.py`, `backend/user/api_keys.py`.

**2. [Rule 1 — Hand-typed non-slash paths] Task-1 verify one-liner asserted `/api/v1/api-keys` (no slash)**
- **Found during:** Task 1 (dumping `app.openapi()['paths']`).
- **Issue:** The plan's line-221 verify one-liner hand-typed collection paths WITHOUT the trailing slash. FastAPI actually emits `/api/v1/api-keys/` and `/api/v1/jobs/` (trailing slash) for `@router.get("/")` under a prefixed router — the pre-existing 13-03 snapshot already used `/api/v1/jobs/`.
- **Fix:** Followed the real emitted spec (per executor rule: read `app.openapi()['paths']`, never hand-type). Snapshot + the runtime 5-path assertion + the SDK path constants ALL use the trailing-slash form and match verbatim.

**3. [Deviation — SDK base_url] Used origin base_url + full paths instead of `/api/v1`-suffixed base_url**
- **Rationale:** The plan's `_client.py` sketch used `base_url="https://api.bindwave.com/api/v1"` with relative `/jobs` paths. That makes the SDK's path *strings* (`/jobs`) NOT match the OpenAPI spec (`/api/v1/jobs/`), which would fail the 13-07 SDK⇄spec contract test the prompt flagged as load-bearing. Set `base_url` default to the origin (`https://app.bindwave.com`, `BINDWAVE_BASE_URL` override) and used full `/api/v1/...` path constants exported at module level. Verified every SDK path appears verbatim in `app.openapi()['paths']`.

## Live-DB independence (Gotcha 4)

`supabase db push` was NOT run; the `api_keys` table exists in no reachable DB. All backend endpoint tests are mock-based (`dependency_overrides` for auth + `patch(...get_db_pool)` returning an `AsyncMock` conn/pool). No test needs a live table, so none were xfailed for that reason. The dev pepper `API_KEY_PEPPER=test_pepper_dev_only` is inherited from the api_v1 conftest.

## Plaintext-once invariant (how enforced + tested)

- **Enforced:** `POST /user/api-keys` is the only handler that returns a `plaintext` field (from `generate_api_key`); the DB stores only the HMAC hex (`bcrypt_hash`). `GET /user/api-keys` selects only `id,name,prefix,created_at,last_used_at,role_at_creation` — no plaintext column exists to leak. The SDK `ApiKey` model has no `plaintext` field. The `/api/v1/api-keys` surface has no create endpoint at all.
- **Tested:** `test_create_returns_plaintext` asserts POST body contains `plaintext` (+ `prefix == plaintext[:12]`) AND that a subsequent GET on the same row omits `plaintext`. SDK `test_list_returns_api_keys` asserts the parsed `ApiKey` has no `plaintext` attr.

## Verification results

- **SDK suite:** `22 passed in 3.48s` (7 client + 11 jobs + 3 api-keys + 1 placeholder).
- **backend api_v1 + contract:** `46 passed, 3 xfailed`.
- **Full backend no-regression gate:** `423 passed, 19 skipped, 6 xfailed` — **0 failed** (baseline was 418 passed / 21 skipped; delta = +5 passed / −2 skipped: the 2 formerly-`skip`ped stub tests now run and pass, plus 3 net-new endpoint tests).
- **D-15:** `TestClient(app).get('/api/openapi.json')` → exactly `['/api/v1/api-keys/', '/api/v1/api-keys/{key_id}/revoke', '/api/v1/jobs/', '/api/v1/jobs/{job_id}', '/api/v1/jobs/{job_id}/cancel']`; no path contains `/user/api-keys`; all under `/api/v1/`.
- **SDK⇄spec:** all 5 SDK path constants appear verbatim in `app.openapi()['paths']`; job-submit path `= /api/v1/jobs/`.
- **Idempotency:** SDK auto-generates a 32-char uuid4 hex `Idempotency-Key` on submit when the caller omits one (`test_submit_auto_idempotency_key`); caller override honored (`test_submit_caller_idempotency_key`); cancel/list/get/revoke send no such header.

## Commits

- `35501af` feat(13-04): /api/v1/api-keys + /user/api-keys self-management endpoints
- `7dde9b7` feat(13-04): bindwave-python sync Client + jobs/api-keys resources + exceptions

## Known stubs / follow-ups

- `AsyncClient` is intentionally a `NotImplementedError` placeholder — Plan 13-05 ships it (documented in `__init__.py`, README, and success criterion 12).
- `bindwave-python/tests/test_placeholder.py` (from 13-02) still passes and was left untouched (not in this plan's file list).

## Self-Check: PASSED
- All created files exist on disk (verified with `test -f` in the verify blocks).
- Both commits present in `git log`.
