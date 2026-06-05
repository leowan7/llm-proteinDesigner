# Phase 13: Public API — Research

**Researched:** 2026-06-04
**Domain:** REST API design, API-key auth, Python SDK packaging, RFC 7807, FastAPI OpenAPI surface management
**Confidence:** HIGH (locked decisions D-01..D-16 cover the design surface; this research only resolves the 13 Claude's-Discretion items + lands the landmines)

---

## 1. Executive Summary

Phase 13 mounts `/api/v1/*` as a parallel router that delegates into the existing `jobs/service.py` layer. The hard work was already done by Phase 12 (org-tenancy, RLS) and Phase 5 (rate-limit middleware) — Phase 13 adds (a) API-key auth that returns the same `(org_id, role)` tuple that `get_active_org` returns, (b) Stripe-style idempotency above BILL-04, (c) cursor pagination, (d) RFC 7807 errors scoped to v1, (e) `include_in_schema=False` on every legacy router, and (f) the `bindwave-python` SDK in a separate repo.

**Primary recommendation:** Plan 13 as 7 sequential plans grouped into 4 waves. Wave 0 lands DB + config + `api_keys` model + the OpenAPI-surface migration (D-15 flip on 11 existing routers — large, mechanical, contains landmines). Wave 1 adds the API-key dep + idempotency + v1 jobs router + RFC 7807 handler. Wave 2 ships the SDK in `bindwave-python/`. Wave 3 ships the Settings UI tab + ROADMAP correction + verification. The biggest landmine is the CONTEXT.md claim that bcrypt is "already in requirements" — it is NOT. Phase 1 auth delegates password hashing to Supabase Auth server-side, and `backend/requirements.txt` has no bcrypt entry. Adding it for D-03 hash-at-rest is a real change, and on every request we pay the bcrypt cost — this drives the cost-factor recommendation below.

---

## 2. Resolved Claude's Discretion

The 13 items listed in the orchestrator brief, in the same order. Each: recommendation, evidence, one alternative.

### 2.1 — Idempotency storage backend (Redis vs Postgres table)

**Recommendation:** **Postgres table `api_key_idempotency`**, 24h TTL, swept by an arq-scheduled cron. Use the SAME Supabase Postgres instance and the same asyncpg pool already in use.

**Evidence:**
- The CONTEXT.md hint says "lean toward Redis ... Postgres if the researcher finds the existing rate-limit middleware already uses Postgres." That branch is the wrong question — the rate-limit middleware *does* use Redis (`backend/middleware/rate_limit.py:58`, `storage_uri=settings.redis_url`). But that does not make Redis the right idempotency store, because rate-limit and idempotency have different durability requirements.
- **Idempotency replay must survive a Redis flush, AOF gap, or eviction.** Stripe stores idempotency keys in Postgres precisely because Redis is the wrong tier (see Brandur's reference implementation — Postgres table, cleanup cron) ([CITED: brandur.org/idempotency-keys]).
- **BILL-04 + dispatch.py already write to Postgres before any provider call** (`backend/jobs/dispatch.py:55-94`). Keeping idempotency in the same transaction-capable backend means we can guard against the partial-failure case (item 2.9 below) with a single transaction.
- Upstash Redis on prod is on the free tier (Phase 11 D-11) — adding 24h-rolling idempotency rows would push us toward eviction. Postgres has no such pressure.

**Schema (proposed):**
```sql
CREATE TABLE public.api_key_idempotency (
    api_key_id          UUID NOT NULL REFERENCES public.api_keys(id) ON DELETE CASCADE,
    idempotency_key     TEXT NOT NULL,
    request_body_hash   TEXT NOT NULL,            -- sha256 of canonicalized JSON body
    response_status     INT NOT NULL,
    response_body       JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (api_key_id, idempotency_key)
);
CREATE INDEX idx_api_key_idem_created ON public.api_key_idempotency(created_at);
```
Cron deletes WHERE created_at < now() - INTERVAL '25 hours' (1h buffer so a key written at T=23h59m doesn't get swept while a retry is in flight).

**Alternative considered:** Redis with 24h TTL. **Rejected** because (a) the eviction-under-pressure case silently drops idempotency replay (caller retries and gets a fresh job dispatch — the opposite of what idempotency promises); (b) the BILL-04 + dispatch path is already Postgres-transactional and we can co-write the idempotency row in the same transaction as the job row (closing the partial-failure window in 2.9); (c) we add zero new infra surface.

### 2.2 — `api_keys` schema columns

**Recommendation:** Match the field list in CONTEXT.md verbatim, with three additions: NULL handling on `revoked_at`, an `idx_api_keys_prefix` for fast prefix lookup, and an explicit `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` matching the org tables.

```sql
CREATE TABLE public.api_keys (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    created_by_user_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE SET NULL,
    name                    TEXT NOT NULL,
    prefix                  TEXT NOT NULL,        -- first 12 chars of plaintext: "bw_live_XXXX"
    bcrypt_hash             TEXT NOT NULL,        -- hash of the 32-char random suffix
    role_at_creation        public.org_role NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ,
    CONSTRAINT name_not_blank CHECK (length(btrim(name)) > 0)
);
CREATE INDEX idx_api_keys_org ON public.api_keys(organization_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_api_keys_prefix ON public.api_keys(prefix) WHERE revoked_at IS NULL;
```

**Evidence:**
- `public.org_role` ENUM already exists (`supabase/migrations/20260605000001_organizations.sql:18`).
- Existing migrations use `gen_random_uuid()` PK + `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` + `ON DELETE CASCADE` to org (same file, `:27-36`).
- `CONSTRAINT name_not_blank CHECK (length(btrim(name)) > 0)` mirrors the organizations table convention (same file, `:35`).
- The partial indexes `WHERE revoked_at IS NULL` matche the Phase 12 invitation index pattern (`idx_invitations_org ... WHERE accepted_at IS NULL AND revoked_at IS NULL`, same file, `:83`).

**`last_used_at` debouncing:** Yes, debounce. Writing on EVERY API call turns this into a hot row (every authenticated request UPDATEs the same row in the same `WHERE id=$1` path). Recommend a **1-minute debounce**: only write if `last_used_at IS NULL OR last_used_at < now() - INTERVAL '1 minute'`. Per-request cost goes from "always 1 UPDATE" to "≈1 UPDATE per minute per key" without losing observability (Settings UI displays "Last used today at 14:32" not "Last used 0.4 seconds ago"). Implementation: a single UPDATE with the predicate, fire-and-forget background task so it doesn't block the request.

**Alternative considered:** Add a `scopes TEXT[]` column for future-proofing. **Rejected** because D-02 explicitly defers scopes ("Inherit creator's org role only ... No new scope vocabulary in v1") and adding the column now plants a permanently-NULL field that signals "we plan to use this" — bad schema hygiene. Add via ALTER TABLE if/when scopes become real.

### 2.3 — Cursor encoding scheme

**Recommendation:** **Unsigned base64-encoded JSON `{created_at, id}`** with `urlsafe_b64encode` (no padding stripping). Match Stripe / OpenAI. NO HMAC signing.

**Evidence:**
- Stripe and OpenAI both ship opaque base64 JSON cursors without HMAC ([VERIFIED: docs.stripe.com/api/pagination, platform.openai.com/docs/api-reference/list-pagination]).
- Cursor tampering is a non-threat: the user is already authenticated as a specific (org, role) tuple and RLS scopes every query to their org. A tampered cursor at worst pages to a wrong-but-still-org-scoped position — no information leak, no privilege escalation.
- Cost of HMAC: a per-cursor secret rotation story we don't need; verification cost on every list call; a more brittle deploy pipeline (key rotation = invalidate every outstanding cursor).
- Cost of unsigned base64: zero. Pagination already keyset-orders by `(created_at DESC, id DESC)` (see existing list_jobs at `backend/jobs/router.py:423`), and the cursor is just the boundary tuple.

**Concrete implementation location:** `backend/api/v1/_cursor.py` with two functions:
```python
def encode_cursor(created_at: datetime, id: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"c": created_at.isoformat(), "i": id}).encode()
    ).decode().rstrip("=")

def decode_cursor(token: str) -> tuple[datetime, str] | None:
    try:
        padded = token + "=" * (4 - len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(payload["c"]), payload["i"]
    except Exception:
        return None  # Treat unparseable cursor as no-cursor; 400 from router.
```

**Alternative considered:** HMAC-signed token. **Rejected** per above. The cost of HMAC > the threat surface, and it makes the cursor non-portable across staging/prod (different signing keys = different cursors).

### 2.4 — Rate-limit response headers (units)

**Recommendation:** **Yes** — emit `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (unix epoch seconds, matching GitHub) on every `/api/v1/*` response, plus `Retry-After` (seconds, RFC standard) on the 429.

**Evidence:**
- slowapi supports this natively: `Limiter(..., headers_enabled=True)` emits all four headers ([CITED: slowapi.readthedocs.io/en/latest/api/]).
- GitHub uses unix epoch seconds for `X-RateLimit-Reset` ([VERIFIED: docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api]). OpenAI and Stripe match.
- **Known bug:** `slowapi` issue #33 — using `default_limits` (global) AND a `@limiter.limit(...)` decorator on the same route produces double-comma headers (`X-RateLimit-Limit: 1000,1000`). The current code uses BOTH ([CITED: github.com/laurentS/slowapi/issues/33]). Avoidance: **do not add `headers_enabled=True` to the existing global Limiter** — that would also affect the web flow, which currently has none of these headers. Instead, configure a SECOND `Limiter` instance scoped only to `/api/v1/*`, with `headers_enabled=True` and NO global default limit. Mount it via a per-router dependency, not the app-level middleware.

**Two-limiter pattern:**
```python
# backend/middleware/rate_limit.py (extend)
api_v1_limiter = Limiter(
    key_func=get_api_key_id,   # new key func — reads api_keys.id from request.state
    storage_uri=settings.redis_url,
    headers_enabled=True,
    enabled=settings.rate_limit_enabled and not settings.testing,
)
# Routes: @api_v1_limiter.limit("60/minute")
```

**Reset header units:** **Unix epoch seconds** (default for slowapi). Matches GitHub / Stripe / OpenAI; SDK can compute `wait = reset - now`. Per `Retry-After`: **seconds** (per RFC 7231 §7.1.3).

**Alternative considered:** Use RFC `RateLimit-Limit` / `RateLimit-Remaining` / `RateLimit-Reset` (no `X-` prefix, IETF draft). **Rejected** — the draft is still draft-stage ([CITED: datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers]) and ecosystem (GitHub, Stripe, OpenAI, npm, every major API) ships the `X-` prefix. Don't be the snowflake; revisit when the RFC lands.

### 2.5 — `bindwave-python` repo bootstrap

**Recommendation:** **Match Anthropic SDK layout exactly.** Hatchling build backend, ruff + mypy, GitHub Actions release on tag push, httpx-based sync + async clients sharing a `BaseClient`. The Anthropic SDK is the explicit precedent per D-12.

**Evidence:** Anthropic SDK pyproject uses `hatchling==1.26.3` + `hatch-fancy-pypi-readme`; layout is `src/anthropic/`, `tests/`, `examples/`, `bin/` (release scripts) ([VERIFIED: github.com/anthropics/anthropic-sdk-python/blob/main/pyproject.toml]). Sync + async clients share a `BaseClient[_HttpxClientT, _DefaultStreamT]` generic ([VERIFIED: deepwiki.com/anthropics/anthropic-sdk-python/4.2-synchronous-and-asynchronous-clients]).

**File layout (bindwave-python/):**
```
bindwave-python/
├── pyproject.toml                   # hatchling, version, deps (httpx + pydantic)
├── README.md                        # install + quickstart (D-09 example: 15 lines max)
├── CHANGELOG.md                     # Keep-a-Changelog format
├── LICENSE                          # MIT (matches Ranomics OSS convention)
├── src/
│   └── bindwave/
│       ├── __init__.py              # exports: Client, AsyncClient, exceptions, Job, ApiKey
│       ├── py.typed                 # marker file for PEP 561
│       ├── _client.py               # BaseClient + sync Client + AsyncClient
│       ├── _exceptions.py           # BindwaveError hierarchy (D-16)
│       ├── _cursor.py               # auto-paginate iter_all helper
│       ├── _idempotency.py          # uuid4 helper for the required header (D-05)
│       ├── jobs.py                  # client.jobs.submit / get / list / cancel / iter_all
│       ├── api_keys.py              # client.api_keys.list / revoke
│       └── types/
│           ├── __init__.py
│           ├── job.py               # Job, JobStatus, Candidate (pydantic models)
│           └── api_key.py
├── tests/
│   ├── conftest.py
│   ├── test_client.py               # sync + async constructor, env-var pickup
│   ├── test_jobs.py                 # respx-mocked endpoint responses
│   ├── test_api_keys.py
│   └── test_pagination.py
├── examples/
│   ├── submit_and_wait.py           # README quickstart (the 15-line example)
│   ├── batch_submit.py              # iter_all over historical jobs
│   └── async_pipeline.py            # AsyncClient
└── .github/workflows/
    ├── ci.yml                       # pytest, ruff, mypy on every PR
    └── release.yml                  # on tag push v*: hatch build + twine upload
```

**Key implementation patterns:**
- Public surface: `from bindwave import Client, AsyncClient` (D-10 verbatim). `__init__.py` re-exports.
- Both clients share `BaseClient[HttpClientT]` parameterized by `httpx.Client` vs `httpx.AsyncClient` (Anthropic pattern).
- Auth header: `Authorization: Bearer bw_live_…`. NO `X-Org-Id` (D-01).
- Idempotency: `client.jobs.submit(...)` accepts `idempotency_key=` kwarg; if absent, the SDK generates `uuid4().hex` so the user-facing UX never sees a 400 for missing header.
- Retry: default httpx retries on `5xx` + `429` with exponential backoff capped at 5 attempts. Reads `Retry-After` from 429 responses to schedule the next attempt.
- Errors: `BindwaveError` (base) → `BindwaveAuthError` (401), `BindwaveRateLimitError` (429), `BindwaveValidationError` (400 with `errors[]`), `BindwaveJobError` (4xx from a specific job endpoint), `BindwaveAPIError` (5xx). Parser reads `application/problem+json` and maps `type` URL → exception class.
- `Job.wait_until_complete(poll_every=30, timeout=None)` is a method on the typed `Job` model (returned from `submit` or `get`). Uses the client it was created with.
- `client.jobs.iter_all(**filters)` is a generator yielding `Job` instances; calls `list` repeatedly with the cursor (D-06).

**Alternative considered:** Poetry instead of hatch. **Rejected** — Anthropic SDK is the explicit reference per D-12, and hatch ships with Python 3.12+ (no extra install for users running CI). Also: hatch + `hatch-fancy-pypi-readme` handles the README-on-PyPI tradeoff cleanly (the GitHub README has badges and links; PyPI strips them).

### 2.6 — OpenAPI-to-SDK contract test location

**Recommendation:** Put it in **`backend/tests/contract/test_openapi_contract.py`**, run by the existing pytest gate in Phase 9's `test.yml`. NOT a separate `.github/workflows/sdk-contract.yml`.

**Evidence:**
- `backend/tests/` already exists with the same `pytest-asyncio` config (`backend/pytest.ini`). Adding `tests/contract/` is a 0-new-tooling change.
- Phase 9's `test.yml` already runs the full backend pytest suite on every PR (Phase 9 SC 4). Putting the test under `backend/tests/contract/` gives it the same gate, same caching, same Python env. A separate workflow duplicates setup and is two surfaces to maintain.
- The test only needs: (1) fetch `app.openapi()` via FastAPI's `app.openapi_url` (or just call `app.openapi()` directly without HTTP), (2) load the SDK's hard-coded endpoint table, (3) for each row assert path + method + status code + required request/response field names exist in the spec. No external repo checkout needed — the SDK ships a `bindwave/_contract.py` table that we vendor + version-pin into `backend/tests/contract/_sdk_contract_v0_1_0.py`.

**Test structure:**
```python
# backend/tests/contract/test_openapi_contract.py
from main import app

SDK_CONTRACT_V1 = [
    # (method, path, req_fields, resp_fields, status)
    ("POST", "/api/v1/jobs",          ["tool", "parameters"], ["id", "status"], 201),
    ("GET",  "/api/v1/jobs/{job_id}", [],                     ["id", "status", "candidates"], 200),
    ("GET",  "/api/v1/jobs",          [],                     ["data", "next_cursor"], 200),
    ("POST", "/api/v1/jobs/{job_id}/cancel", [], ["id", "status"], 200),
    # ... api_keys endpoints ...
]

def test_openapi_contains_sdk_contract():
    spec = app.openapi()
    for method, path, req_fields, resp_fields, status in SDK_CONTRACT_V1:
        path_spec = spec["paths"][path][method.lower()]
        assert path_spec, f"{method} {path} missing"
        # ...field assertions...
```

**Alternative considered:** A GitHub Actions workflow that checks out `bindwave-python` and runs a real HTTP smoke against a backend test server. **Rejected** — that's an integration test (good!), but it needs the SDK to be released first (chicken-and-egg on the FIRST release) and adds a cross-repo CI dependency. Make it a follow-up.

### 2.7 — RFC 7807 exception handler scoping

**Recommendation:** **App-level handler with a `request.url.path.startswith("/api/v1/")` gate.** Single registration site, single source of truth, and the existing web-flow routes are untouched.

**Evidence:**
- FastAPI exception handlers are app-scoped — you cannot register them on a sub-`APIRouter` ([CITED: github.com/fastapi/fastapi/discussions/8059, github.com/fastapi/fastapi/discussions/11741]).
- The cleanest pattern is: register ONE handler for `HTTPException` and ONE handler for `RequestValidationError`, each branching on `request.url.path` to choose response format.
- `slowapi`'s existing `RateLimitExceeded` handler is also registered at app level (`backend/middleware/rate_limit.py:74`) — we follow the same pattern.

**Implementation site:** `backend/api/v1/_problem_details.py`:
```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError, HTTPException
from fastapi.responses import JSONResponse

PROBLEM_TYPE_BASE = "https://bindwave.com/errors/"

async def http_exception_handler(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/api/v1/"):
        # Fall through to FastAPI default (web flow keeps its current shape)
        from fastapi.exception_handlers import http_exception_handler as default
        return await default(request, exc)
    type_slug = _slug_for_status(exc.status_code)  # e.g. 404 -> "job-not-found"
    return JSONResponse(
        status_code=exc.status_code,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_TYPE_BASE}{type_slug}",
            "title": _title_for_status(exc.status_code),
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": request.url.path,
        },
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/v1/"):
        from fastapi.exception_handlers import request_validation_exception_handler as default
        return await default(request, exc)
    return JSONResponse(
        status_code=422,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_TYPE_BASE}validation-error",
            "title": "Validation error",
            "status": 422,
            "detail": "One or more fields failed validation.",
            "instance": request.url.path,
            "errors": [
                {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        },
    )
```
Register in `backend/main.py` immediately after the existing `setup_rate_limiting(app)` call.

**Alternative considered:** Use the `fastapi-rfc7807` third-party library ([CITED: github.com/vapor-ware/fastapi-rfc7807]). **Rejected** — adding a maintenance-light third-party dep for what is a 60-LOC custom handler is bad math. Also, the library is app-scoped — same problem we have natively; it doesn't solve the per-router branching.

### 2.8 — `include_in_schema=False` migration scope

**Recommendation:** Flip the flag on **EVERY existing router** in Wave 0 of Phase 13. There are exactly **11 routers** today, none of which currently set this flag (verified via `grep`).

**Routers that need the flip (D-15 enforcement):**

| # | File | Prefix | Current state | Action |
|---|------|--------|---------------|--------|
| 1 | `backend/auth/router.py:21` | `/auth` | `tags=["auth"]` | Add `include_in_schema=False` |
| 2 | `backend/agent/router.py:44` | `/agent` | `tags=["agent"]` | Add `include_in_schema=False` |
| 3 | `backend/admin/router.py:32` | `/admin` | `tags=["admin"]` | Add `include_in_schema=False` |
| 4 | `backend/billing/router.py:24` | `/billing` | `tags=["billing"]` | Add `include_in_schema=False` |
| 5 | `backend/debug_routes.py:11` | `/debug` | `tags=["debug"]` | Already gated by `settings.debug` — also `include_in_schema=False` |
| 6 | `backend/jobs/router.py:33` | `/jobs` | `tags=["jobs"]` | Add `include_in_schema=False` |
| 7 | `backend/organizations/router.py:41` | `/organizations` | `tags=["organizations"]` | Add `include_in_schema=False` |
| 8 | `backend/organizations/router.py:42` | `/invitations` | `tags=["invitations"]` | Add `include_in_schema=False` |
| 9 | `backend/pdb_utils/router.py:25` | `/pdb` | `tags=["pdb"]` | Add `include_in_schema=False` |
| 10 | `backend/sessions/router.py:32` | `/sessions` | `tags=["sessions"]` | Add `include_in_schema=False` |
| 11 | `backend/user/router.py:31` | `/user` | `tags=["user"]` | Add `include_in_schema=False` |
| 12 | `backend/webhooks/router.py:40` | `/webhooks` | `tags=["webhooks"]` | Add `include_in_schema=False` |

(That's 12 routers, not 11 — I miscounted on the first pass. `organizations` exports two routers from the same file.)

**Plus `main.py:146 /health`** — a bare `@app.get` route, not a router. Decision: leave `/health` visible in the OpenAPI spec? **No.** /health is an internal probe, not part of the public API contract; mark it `include_in_schema=False` too.

**Evidence:** `grep -n "include_in_schema" backend/**/*.py` returned ONE hit — middleware/rate_limit.py uses `add_exception_handler`, not `include_in_schema`. Verified zero existing usage.

**Risk callout:** This is a sweeping but mechanical change. Touch ALL 12 router files in a single commit so the OpenAPI snapshot test (recommended in item 2.12 below) gives a single binary result: "before this commit, spec contains 100+ routes; after, spec contains only `/api/v1/*` routes." No partial state.

**Alternative considered:** Use `include_in_schema=True` on `/api/v1/*` ONLY and rely on FastAPI's default `True` for everything else. **Rejected** — D-15 is explicit ("Only `/api/v1/*` routes appear in the OpenAPI spec ... every other router ... gets `include_in_schema=False`"). The published spec IS the contract.

### 2.9 — Idempotency-Key behavior on partial failure + body-hash conflict

**Recommendation:** Three-state idempotency lifecycle. The Postgres row from item 2.1 above carries `status TEXT NOT NULL DEFAULT 'pending'` (alongside the columns shown there). Schema becomes:

```sql
CREATE TABLE public.api_key_idempotency (
    api_key_id          UUID NOT NULL REFERENCES public.api_keys(id) ON DELETE CASCADE,
    idempotency_key     TEXT NOT NULL,
    request_body_hash   TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'completed'
    response_status     INT,                              -- NULL until completed
    response_body       JSONB,                            -- NULL until completed
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,                      -- set on transition to 'completed'
    PRIMARY KEY (api_key_id, idempotency_key)
);
```

**Lifecycle:**

1. **First request arrives.** Try `INSERT ... ON CONFLICT DO NOTHING RETURNING status`. If the INSERT succeeded, the request proceeds with `status='pending'` row in place. Process the dispatch.
2. **First request completes.** UPDATE `status='completed', response_status=$1, response_body=$2, completed_at=now()`.
3. **First request crashes between dispatch + UPDATE.** The row stays at `status='pending'`. The next retry sees `status='pending'` and must NOT re-dispatch. Return **409 Conflict** with body `{"type": ".../idempotency-in-progress", "title": "Request is still being processed", "status": 409, "detail": "Retry with the same idempotency key after a few seconds.", "instance": "/api/v1/jobs"}`. Caller retries; the row stays pending until either (a) the watchdog detects the abandoned row + reaps it, OR (b) the original dispatch eventually completes (rare under crash semantics; this row is mostly a stuck row).
4. **Same key, different body.** INSERT raises a unique constraint violation; we read the existing row; if `request_body_hash != new_body_hash`, return **422 Unprocessable Entity** per the IETF idempotency draft (`{"type": ".../idempotency-key-conflict", "title": "Idempotency key reused with different request body", ...}`). (Stripe uses 400 here; OpenAI uses 422; we use 422 because the body validates structurally but the key+body combination is logically incoherent.)
5. **Same key, same body, already completed.** Read the row and **replay** the stored `response_status` + `response_body` byte-for-byte. Add a custom header `X-Idempotency-Replay: 1` so the SDK can surface "this was a replay" if it wants to.

**Atomicity guarantee:** The `INSERT ... ON CONFLICT DO NOTHING` is the gate. In the same transaction as the job-row INSERT (the existing `dispatch.py:55` UPDATE path), we can co-write the idempotency row, so the partial-failure window is closed:
```python
async with conn.transaction():
    await conn.execute("INSERT INTO public.api_key_idempotency ...")  # status='pending'
    await conn.execute("UPDATE public.jobs SET status='queued' ...")  # the existing dispatch path
    # Both commit or both rollback. No way for the job to exist without the idempotency row.
# After commit, enqueue arq + later UPDATE the idem row to 'completed'.
```

**Evidence:**
- Stripe: same-key-different-body returns "The idempotency key is being reused, but with different parameters" — 400 in their case ([CITED: docs.stripe.com/api/idempotent_requests]).
- IETF draft `draft-ietf-httpapi-idempotency-key-header-07` recommends 422 for body mismatch, 409 for in-progress ([CITED: datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07]).
- Brandur's reference implementation uses a state machine exactly like the above (pending → completed, with the 409 in-progress branch) ([CITED: brandur.org/idempotency-keys]).
- `request_body_hash`: canonicalize JSON before hashing — `json.dumps(body, sort_keys=True, separators=(",", ":"))` then `sha256` then hex. Avoids the "same body, different key order" false-positive 422.

**Alternative considered:** Skip the body-hash check entirely; trust the caller. **Rejected** — Stripe's blog post on idempotency explicitly cites the body-hash check as preventing the buggy-caller misuse case, and the cost is one `digest()` call ([CITED: stripe.com/blog/idempotency]).

### 2.10 — Bcrypt cost factor (or fast hash)

**Recommendation:** **Use HMAC-SHA256 with a server-side pepper, not bcrypt.** The CONTEXT.md D-03 says "bcrypt" — this recommendation respects D-03's intent (hash-at-rest so a leaked DB doesn't expose plaintext keys) while picking the standard tool for API-key verification.

**Critical evidence (the CONTEXT.md is wrong on a load-bearing fact):**
- CONTEXT.md `:196` says: `bcrypt already in requirements — Phase 1 auth uses it; D-03 hash-at-rest is additive, no new dep.`
- **Verified false.** `backend/requirements.txt` (29 lines) has NO bcrypt entry. `grep -rn "bcrypt\|passlib\|argon2" backend/` returns ZERO results. Phase 1 auth (`backend/auth/router.py:23`) calls `create_client(supabase_url, anon_key)` and delegates password hashing to Supabase Auth server-side — the FastAPI backend never sees a plaintext password.
- D-03 hash-at-rest is therefore **NOT additive** — it requires a new dependency.

**Why HMAC-SHA256, not bcrypt:**
- API keys are 32 random base32 characters (256 bits of entropy from the random suffix per D-03). They are NOT human-memorized passwords; the security comes from entropy, not from slow hashing ([CITED: ssojet.com/compare-hashing-algorithms/hmac-sha256-vs-bcrypt]).
- bcrypt cost 12 = ~250ms per verify on modern hardware. At 60 rpm per key, with multiple keys hitting an instance, this is a measurable CPU cost: 60 keys × 250ms = 15 seconds of CPU per minute per heavy user. Slow hash + 60rpm bucket is a DoS amplifier.
- bcrypt cost 10 (~50ms) is the OWASP minimum for passwords ([CITED: aquilax.ai/blog/password-hashing-bcrypt-argon2]). But for HIGH-entropy tokens, a single SHA-256 + pepper is universally recommended ([CITED: cybersierra.co/blog/bcrypt-performance-issues-api]).
- Pepper is a per-environment secret env var, NOT in the DB. So a DB-only leak does not reveal hash inputs.

**Concrete implementation:**
```python
# backend/auth/api_keys.py
import hmac, hashlib, secrets
from config import settings

def generate_api_key(env: str = "live") -> tuple[str, str, str]:
    """Returns (plaintext, prefix, hash)."""
    suffix = secrets.token_urlsafe(24)  # ~32 chars urlsafe base64 (~192-bit entropy)
    plaintext = f"bw_{env}_{suffix}"
    prefix = plaintext[:12]              # "bw_live_XXXX" (4 chars of suffix)
    h = hmac.new(
        settings.api_key_pepper.encode(),
        plaintext.encode(),
        hashlib.sha256,
    ).hexdigest()
    return plaintext, prefix, h

def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    h = hmac.new(settings.api_key_pepper.encode(), plaintext.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(h, stored_hash)
```

**Pepper rotation:** Stored in `settings.api_key_pepper`, with `settings.api_key_pepper_prev` for rotation grace (same pattern as the existing `webhook_hmac_secret` / `webhook_hmac_secret_prev` at `backend/config.py:94-95`). Verify tries current, then prev. Rotation runbook lives alongside the webhook rotation runbook in `docs/deploy.md`.

**Note on the column name:** D-02 says "bcrypt_hash" as the column name. **Keep the name** to avoid a CONTEXT.md edit, but the column stores a HMAC-SHA256 hex digest (64 chars). A migration comment documents this:
```sql
COMMENT ON COLUMN public.api_keys.bcrypt_hash IS
  'HMAC-SHA256 (hex) of the plaintext key + server-side pepper. Column name retained from D-03 spec for compatibility; algorithm changed per Phase 13 RESEARCH item 2.10 (high-entropy tokens do not need a slow hash).';
```

**Alternative considered:** bcrypt cost 10 per the orchestrator's "lower cost factor" suggestion. **Rejected** — even cost 10 is 50× slower than HMAC-SHA256 (≈50ms vs ≈1µs), the security model differs (high-entropy random vs human password), and the slowness amplifies any DoS attempt. If user explicitly insists on bcrypt during plan-check, fall back to cost **10** with the pepper added in addition (bcrypt of HMAC(pepper, plaintext)), but flag this as a 25× CPU regression vs the HMAC-only path.

**[ASSUMED] flag:** This research overrides a load-bearing claim in CONTEXT.md (bcrypt already present). The planner SHOULD raise this in plan-check; the user may need to confirm we're swapping bcrypt for HMAC-SHA256.

### 2.11 — Validation Architecture (Nyquist Dimension 8) — see §4 below

Pulled into its own first-class section because nyquist_validation is enabled in `.planning/config.json:9`.

### 2.12 — Migration risk + OpenAPI snapshot test

**Recommendation:** Add **`backend/tests/contract/test_openapi_snapshot.py`** alongside the SDK contract test. The snapshot test:
- Dumps `sorted(spec["paths"].keys())` to a fixture file `tests/contract/_openapi_paths_snapshot.txt`.
- Pre-D-15-flip: snapshot contains ~100 paths across all 12 routers + new `/api/v1/*`.
- Post-D-15-flip: snapshot contains ONLY `/api/v1/*` paths.
- Test asserts:
  ```python
  def test_openapi_paths_match_snapshot():
      spec = app.openapi()
      paths = sorted(spec["paths"].keys())
      with open("backend/tests/contract/_openapi_paths_snapshot.txt") as f:
          expected = [l.strip() for l in f if l.strip()]
      assert paths == expected, "OpenAPI surface changed — review with the team and update the snapshot file deliberately"
  ```
- Failure mode is deliberate: any future PR that adds a new public-API route OR accidentally exposes an internal route must update this snapshot file. This catches the "operator triage" risk you flagged.

**Evidence:** This is the standard "snapshot test" pattern from Jest / Vitest. Treats the OpenAPI surface as a versioned, reviewable contract. Cost is trivial (one text file in the repo).

### 2.13 — Landmines — see §5 below

Pulled into its own first-class section.

---

## 3. New Requirement IDs (proposed cluster: API-01..API-12)

The planner mints these into REQUIREMENTS.md during plan-phase. One line each:

| ID | Description |
|----|-------------|
| **API-01** | API key creation returns plaintext exactly once; storage is HMAC-SHA256+pepper of the plaintext (D-03 hash-at-rest intent, see RESEARCH §2.10). |
| **API-02** | API keys carry one org_id at creation time; calls authenticate as that org with the creator's role-at-creation; no X-Org-Id header (D-01, D-02). |
| **API-03** | API keys never expire; user-initiated revoke flips `revoked_at`; revoked keys reject auth immediately (D-04). |
| **API-04** | `POST /api/v1/jobs` REQUIRES `Idempotency-Key` header; missing → 400; same key + same body within 24h replays the stored response byte-for-byte; same key + different body → 422 (D-05 + RESEARCH §2.9). |
| **API-05** | `GET /api/v1/jobs` paginates by opaque cursor encoding `(created_at, id)` tiebreaker; default limit 25, max 100; supports filters `status`, `tool`, `created_after`, `created_before` (D-06 + RESEARCH §2.3). |
| **API-06** | `GET /api/v1/jobs/{id}` returns inline metadata + ranked candidate list + 24h presigned URLs in one response (D-07). |
| **API-07** | All `/api/v1/*` responses on error use `application/problem+json` per RFC 7807; web-flow routes keep their existing error shape (D-16 + RESEARCH §2.7). |
| **API-08** | All `/api/v1/*` responses emit `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (unix epoch seconds) headers; 429 responses additionally emit `Retry-After` (seconds) (RESEARCH §2.4). |
| **API-09** | Only `/api/v1/*` routes appear in the OpenAPI spec; every other router (12 total) sets `include_in_schema=False`; an OpenAPI snapshot test guards the surface (D-15 + RESEARCH §2.8, §2.12). |
| **API-10** | Per-API-key rate limit: 60 requests/minute, keyed on `api_keys.id`; uses a separate slowapi `Limiter` instance with `headers_enabled=True` (Phase 13 SC 4 + RESEARCH §2.4). |
| **API-11** | OpenAPI docs at `/api/docs` are publicly accessible; the published spec IS the public-API contract (D-13, D-14). |
| **API-12** | Python SDK `bindwave` ships from `bindwave-python` repo with sync `Client` + async `AsyncClient`; published to PyPI on tag push; a backend pytest contract test asserts the spec covers every endpoint the SDK calls (D-09, D-10, D-12 + RESEARCH §2.5, §2.6). |

**Plus:** `PLAT-V2-01` from REQUIREMENTS.md (REST API for power users) gets ticked off + linked to API-04 through API-12 in the traceability table. Promote it from v2 to Phase 13 during planning.

---

## 4. Validation Architecture (Nyquist Dimension 8)

> `workflow.nyquist_validation: true` in `.planning/config.json` — this section is load-bearing for VALIDATION.md instantiation in plan-phase step 5.5.

### 4.1 Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.5 + pytest-asyncio 0.24.0 + respx 0.22.0 (backend); pytest + httpx mocks (bindwave-python) |
| Config file | `backend/pytest.ini` (existing); `bindwave-python/pyproject.toml [tool.pytest.ini_options]` (new) |
| Quick run command | `pytest backend/tests/api_v1 -x` |
| Full suite command | `pytest backend/tests -x --tb=short` |

### 4.2 Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| API-01 | create key returns plaintext once; DB stores hash | unit | `pytest backend/tests/api_v1/test_api_keys.py::test_create_returns_plaintext -x` | ❌ Wave 0 |
| API-02 | authn returns (org_id, role) tuple matching get_active_org | unit | `pytest backend/tests/api_v1/test_auth.py::test_returns_org_role_tuple -x` | ❌ Wave 0 |
| API-03 | revoked key returns 401 | unit | `pytest backend/tests/api_v1/test_api_keys.py::test_revoked_key_rejects -x` | ❌ Wave 0 |
| API-04 | idempotency replay returns stored response | integration | `pytest backend/tests/api_v1/test_idempotency.py::test_replay -x` | ❌ Wave 0 |
| API-04 | idempotency body-mismatch returns 422 | integration | `pytest backend/tests/api_v1/test_idempotency.py::test_body_mismatch_returns_422 -x` | ❌ Wave 0 |
| API-04 | idempotency in-progress returns 409 | integration | `pytest backend/tests/api_v1/test_idempotency.py::test_pending_returns_409 -x` | ❌ Wave 0 |
| API-05 | cursor pagination skips inserted rows | integration | `pytest backend/tests/api_v1/test_pagination.py::test_cursor_stable_under_insert -x` | ❌ Wave 0 |
| API-05 | cursor decodes safely on garbage input | unit | `pytest backend/tests/api_v1/test_cursor.py::test_garbage_input -x` | ❌ Wave 0 |
| API-06 | get-job inline returns presigned URLs | integration | `pytest backend/tests/api_v1/test_jobs_get.py -x` | ❌ Wave 0 |
| API-07 | error envelope is application/problem+json on /api/v1/* | integration | `pytest backend/tests/api_v1/test_errors.py::test_problem_json -x` | ❌ Wave 0 |
| API-07 | web-flow errors keep their existing shape | regression | `pytest backend/tests/jobs -x` (existing, must still pass) | ✅ exists |
| API-08 | rate-limit headers present on 200 + 429 | integration | `pytest backend/tests/api_v1/test_rate_limit.py -x` | ❌ Wave 0 |
| API-09 | OpenAPI spec contains ONLY /api/v1/* paths | contract | `pytest backend/tests/contract/test_openapi_snapshot.py -x` | ❌ Wave 0 |
| API-09 | every legacy router has include_in_schema=False | unit | `pytest backend/tests/contract/test_routers_hidden.py -x` | ❌ Wave 0 |
| API-10 | 61st request in 60s returns 429 | integration | `pytest backend/tests/api_v1/test_rate_limit.py::test_60rpm -x` | ❌ Wave 0 |
| API-11 | /api/docs returns Swagger UI HTML | smoke | `curl -s http://localhost:8000/api/docs \| grep swagger-ui` | manual |
| API-12 | SDK contract: spec covers every SDK endpoint | contract | `pytest backend/tests/contract/test_openapi_contract.py -x` | ❌ Wave 0 |
| API-12 | SDK end-to-end against test server | E2E | `pytest bindwave-python/tests/test_e2e.py -x` | ❌ separate repo |

### 4.3 Sampling Rate
- **Per task commit:** `pytest backend/tests/api_v1 -x` (the new directory only, ~5s after warmup)
- **Per wave merge:** `pytest backend/tests -x` (full backend suite, must stay green so the regression rows above hold)
- **Phase gate:** Full backend suite green + `pytest bindwave-python/tests -x` green + manual smoke on Swagger UI before `/gsd:verify-work`

### 4.4 Wave 0 Gaps

- [ ] `backend/tests/api_v1/__init__.py` — new test directory marker
- [ ] `backend/tests/api_v1/conftest.py` — fixtures: synthetic api_key fixture, idempotency-key generator, X-Org-Id-bypass fixture
- [ ] `backend/tests/api_v1/test_api_keys.py` — API-01, API-03
- [ ] `backend/tests/api_v1/test_auth.py` — API-02
- [ ] `backend/tests/api_v1/test_idempotency.py` — API-04 (replay + body-mismatch + in-progress)
- [ ] `backend/tests/api_v1/test_pagination.py` + `test_cursor.py` — API-05
- [ ] `backend/tests/api_v1/test_jobs_get.py` — API-06
- [ ] `backend/tests/api_v1/test_errors.py` — API-07
- [ ] `backend/tests/api_v1/test_rate_limit.py` — API-08, API-10
- [ ] `backend/tests/contract/__init__.py` + `test_openapi_snapshot.py` + `test_openapi_contract.py` + `test_routers_hidden.py` + `_openapi_paths_snapshot.txt` + `_sdk_contract_v0_1_0.py` — API-09, API-12
- [ ] `bindwave-python/tests/conftest.py` + `test_client.py` + `test_jobs.py` + `test_pagination.py` + `test_e2e.py` — API-12 (separate repo)
- [ ] Add to `backend/requirements.txt`: nothing (existing httpx + asyncpg + redis cover all dependencies; bcrypt is NOT needed because RESEARCH §2.10 uses stdlib `hmac`+`hashlib`)
- [ ] Add to `bindwave-python/pyproject.toml`: `httpx>=0.27`, `pydantic>=2.0`, dev: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`

---

## 5. Landmines

Code-level traps a naive planner will trip over. Each tagged with file:line.

### 5.1 — bcrypt is NOT in requirements.txt (CONTEXT.md is wrong)
**Evidence:** `backend/requirements.txt` (29 lines, all enumerated above) has no bcrypt. Phase 1 auth (`backend/auth/router.py:23`) routes login/signup through Supabase's `create_client()` — passwords never touch the FastAPI backend. **Action:** Resolved in RESEARCH §2.10 — use stdlib `hmac`+`hashlib` (SHA-256 with pepper) instead of bcrypt. If the planner sticks with bcrypt, it must add `bcrypt==4.x` to `backend/requirements.txt` AND `backend/Dockerfile` rebuild.

### 5.2 — `get_active_org` raises 400 if `X-Org-Id` missing (`backend/auth/org_dependencies.py:46-50`)
**Trap:** `require_role(*allowed)` calls `get_active_org` as a `Depends`. If we try to reuse the dep stack as-is for API keys, the FIRST thing it does is raise 400 because API keys per D-01 do NOT send X-Org-Id. **Action:** The API-key dep `get_current_api_key` MUST be a parallel dep that returns the same `(org_id, role)` tuple WITHOUT going through `get_active_org`. The `require_role` factory MUST be refactored to take the tuple as a parameter, not via `Depends(get_active_org)`. Concretely, refactor `require_role` from:
```python
def require_role(*allowed):
    async def dep(active: tuple[str, OrgRole] = Depends(get_active_org)) -> str:
        ...
```
to a tuple-accepting helper, and have BOTH `get_active_org` (web) and `get_current_api_key` (API) feed it the tuple via a small adapter. **Or**: keep `require_role` web-only and ship a parallel `require_role_api(*allowed)` factory that depends on `get_current_api_key`. Recommend the second — less refactor risk for Phase 12 callers, and it makes the two surfaces visually distinct in handler signatures.

### 5.3 — CSRFMiddleware is registered app-wide (`backend/main.py:96-104`)
**Trap:** `CSRFMiddleware` is added to the app unconditionally (well, `if not settings.testing`). It checks for the `csrftoken_v2` cookie on EVERY mutating request. API-key requests will NOT have this cookie. The middleware's default behavior: reject the request. **Action:** CSRFMiddleware constructor accepts `exempt_urls` (regex list). Add `r"^/api/v1/"` to the exempt list. Verify against starlette-csrf 3.0.0 docs that this is the correct kwarg name. The DEFAULT alternative — running CSRFMiddleware AFTER a per-router auth dependency — does NOT work because middleware runs before deps. The exempt-list approach is the only viable path.

### 5.4 — Middleware ordering in `main.py:81-112`
**Current order (Starlette runs outer→inner):**
1. `StructuredLoggingMiddleware` (outermost, added LAST)
2. `SlowAPIMiddleware` (rate limit)
3. `CSRFMiddleware`
4. `CORSMiddleware` (innermost, added FIRST)

**Trap for Phase 13:** The new rate-limit logic (separate `api_v1_limiter` instance) must NOT register a second `SlowAPIMiddleware` — there's only one app.state.limiter slot per Starlette app. **Action:** Keep using the existing middleware but switch its `app.state.limiter` to a *dispatcher* that picks `api_v1_limiter` vs the existing `limiter` based on `request.url.path`. Or simpler: extend the existing `limiter` to accept multiple namespaced limit groups internally. The slowapi maintainer-recommended pattern is route-decorator-only (`@api_v1_limiter.limit("60/minute")`) WITHOUT a global default, which slowapi #33 confirms avoids the double-headers bug.

### 5.5 — `frontend/src/lib/api.ts:29-34` opt-out list (Plan 12-05)
**Trap:** The frontend opt-out list determines whether `X-Org-Id` is sent. Settings UI for API-key management uses the EXISTING web-flow endpoints (`/user/api-keys` per CONTEXT.md `:168`), NOT the `/api/v1/api-keys` self-management endpoints. The web-flow path DOES send X-Org-Id — and it MUST, because the user is operating in an org context to create keys. **Action:** Confirm the api-key web-management routes are NOT added to the opt-out list. Document this in the Phase 13 plan that touches `frontend/src/lib/api.ts`: it should remain UNCHANGED. (The orchestrator's brief mentions adding `/api/v1/api-keys` to the X-Org-Id opt-out list — that endpoint exists for SDK self-management, but the FRONTEND never calls `/api/v1/*`; it calls `/user/api-keys`. So `frontend/src/lib/api.ts` truly does not change.)

### 5.6 — `dispatch.py:55-94` co-write transaction risk
**Trap:** The proposed `INSERT api_key_idempotency` + `UPDATE jobs SET status='queued'` co-transaction (RESEARCH §2.9) crosses the existing dispatch.py boundary. dispatch.py acquires the pool itself (`async with pool.acquire() as conn`). The API-key handler must acquire the SAME connection and pass it into dispatch.py — but `launch_job(...)` takes a pool, not a connection. **Action:** Either (a) refactor `launch_job` to accept an optional `conn` and bypass `pool.acquire()` when supplied; or (b) move the idempotency INSERT into `launch_job` itself, gated by a new optional `idempotency_record` parameter. Recommend (a) — keeps the idempotency logic at the API layer where it belongs.

### 5.7 — `backend/jobs/service.py:101-122` cancel path resolves billing via JOIN
**Already-solved trap, but worth flagging:** cancel_job_by_id does NOT take org_id — it works by ID alone and resolves the org via `JOIN organizations o ON o.id = j.organization_id`. The API-key cancel endpoint can call this as-is. But: the router-level org check (`backend/jobs/router.py:589-595`) MUST be replicated in the API-key router so a key for org-A can't cancel a job in org-B. The check is a single `SELECT id FROM public.jobs WHERE id=$1 AND organization_id=$2`. Use the same query verbatim.

### 5.8 — `webhooks/router.py:40` is NOT a public route (D-15)
**Confirmation, not a trap:** `/webhooks/runpod` and `/webhooks/heartbeat` are SERVER-TO-SERVER endpoints called by Modal / RunPod. Putting them in the public OpenAPI spec would mislead integrators into thinking they're callable client-side. The D-15 flip correctly hides them.

### 5.9 — `main.py:140-141` debug route mounting
**Trap:** `debug_routes.py` is only mounted `if settings.debug or settings.testing`. If a planner adds `include_in_schema=False` to the debug router itself, the spec will still vary by environment (debug routes visible in dev, hidden in prod). **Action:** Acceptable — the published prod spec is the contract. Document in the snapshot test fixture: the snapshot is generated against `settings.debug=False, settings.testing=False`.

### 5.10 — Existing `/jobs/launch` rate limit is 5/minute (`backend/jobs/router.py:105`)
**Confirmation, not a trap:** The web-flow rate limits are MUCH tighter than the 60rpm budget for /api/v1/*. Two limiters with different budgets serving the same downstream service.py is the correct pattern; service.py is naturally idempotent because BILL-04 already prevents double-billing.

### 5.11 — `users.stripe_customer_id` drop migration NOT yet applied (`supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql`)
**Trap from STATE.md `:150`:** Phase 12's drop-column migration is in the repo but NOT applied. Phase 13 runs AFTER Phase 12 cuts over per the runbook. **Action:** Phase 13 plans MUST assume `users.stripe_customer_id` is dropped (post-runbook step 9). If Phase 13 lands in a sequence where Phase 12 hasn't fully cut over, the Stripe customer resolve path falls back to `organizations.stripe_customer_id` (RESEARCH §5.7).

### 5.12 — `frontend/src/pages/SettingsPage.tsx` tab order (D-09's "5th tab" is wrong — there are already 5)
**Trap from CONTEXT.md `:175`:** "current tabs are Account / Notifications / Billing / Privacy (Phase 10) — API Keys becomes the 5th." Actual current tabs (`SettingsPage.tsx:631-637`): **Account, Billing, Privacy, Usage, Notifications + Organization (conditional)**. That's 5 minimum, 6 with org. API Keys is the 6th (or 7th). Recommend inserting it between "Privacy" and "Usage" so security-related tabs are grouped. **Action:** When the planner writes the SettingsPage diff, count the existing tabs from the actual source, not the CONTEXT.md hint.

### 5.13 — `notifications` directory doesn't exist at `backend/jobs/notifications.py`
**Confirmation, not a trap:** `backend/jobs/notifications.py` IS present (imported by `dispatch.py`). The API surface for "GET /api/v1/jobs/{id} returns inline candidate list" reuses the webhook handler's serializer — which is in `webhooks/router.py`, not a separate serializer module. Either extract a shared serializer module (`backend/jobs/serialize.py`) in Wave 0 or duplicate the shape in the v1 router. Recommend the extract — cleaner test surface.

### 5.14 — `settings.organizations_enabled` is False by default (`config.py:149`)
**Trap from STATE.md:** Phase 12 mounts the org routers BEHIND the flag. The flip happens in the Phase 12 rollout runbook. **Action:** Phase 13 work depends on org-tenancy being LIVE. The Phase 13 plans MUST assume `settings.organizations_enabled = True` and the runbook step has fired in prod. If not, the `/api/v1/jobs` endpoint's `require_role_api(...)` dep will reject every call because `organization_memberships` will be empty for users created before the flag flip. Document the dep in PLAN-13-01's pre-flight checklist.

### 5.15 — `agent_session_ttl_seconds` Redis usage co-exists (`config.py:67`)
**Confirmation, not a trap:** Redis is used by (1) rate-limit middleware, (2) SSE connection counting, (3) arq job queue, (4) agent sessions, (5) pub/sub for SSE events. Adding a 6th use (per-API-key 60rpm bucket) is well within Upstash free-tier capacity. The 24h idempotency storage went to Postgres per RESEARCH §2.1, NOT Redis, specifically to avoid contention.

### 5.16 — `backend/billing/stripe_client.py:48-65` resolves the Stripe customer via `organizations` (Phase 12 cutover)
**Confirmation, not a trap:** API-submitted jobs flow through the same payment-method check + Stripe meter event recording as web-flow jobs because they share `service.py`. No new Stripe wiring is needed.

### 5.17 — `gpu_provider="modal"` per `config.py:78`, but `runpod_emergency` exists
**Confirmation, not a trap:** API-submitted jobs dispatch through the same `gpu.get_provider()` indirection. No provider-specific code on the API surface.

---

## 6. Plan-Split Recommendation

7 plans across 4 waves. Sequential within a wave; waves run gate-by-gate.

```
Wave 0 — Foundation (parallel-safe; can run in 2 plans concurrently)
├── PLAN 13-01: Schema + config + OpenAPI surface migration + test scaffold
└── PLAN 13-02: API-key auth dependency + bindwave-python repo bootstrap

Wave 1 — v1 jobs surface
├── PLAN 13-03: /api/v1/jobs router + idempotency + RFC 7807 handler + rate-limit pipeline
└── PLAN 13-04: /api/v1/api-keys self-management + bindwave-python jobs.py / api_keys.py

Wave 2 — SDK polish + frontend
├── PLAN 13-05: bindwave-python AsyncClient + auto-paginator + Job.wait_until_complete + tests
└── PLAN 13-06: SettingsPage 6th tab (API Keys) + last-used debouncing + revoke modal

Wave 3 — Verification + release
└── PLAN 13-07: ROADMAP SC 6 correction, OpenAPI snapshot lock-in, contract test, Phase 13 verification + bindwave 0.1.0 tag
```

### Plan boundaries (one paragraph each)

**PLAN 13-01 — Schema + config + OpenAPI surface migration + test scaffold (Wave 0)**
Ships: migration `20260607000001_api_keys.sql` (api_keys table from RESEARCH §2.2), migration `20260607000002_api_key_idempotency.sql` (idempotency table from §2.9), Pydantic settings additions (`api_key_pepper`, `api_key_pepper_prev`, `idempotency_ttl_hours=24`, `api_v1_rate_limit="60/minute"`, OpenAPI metadata block), `include_in_schema=False` on all 12 existing routers + `/health` (§2.8), backend/tests/api_v1/ + backend/tests/contract/ scaffolds with __init__.py, conftest.py fixtures (synthetic api_key fixture, idempotency-key generator, X-Org-Id-bypass fixture), the OpenAPI snapshot test fixture file at `_openapi_paths_snapshot.txt` (initially containing ONLY the new `/api/v1/*` paths). Requirements satisfied: API-09 schema half. Depends on: nothing. Risk: low (mechanical schema + flag flip). Parallel-safe with 13-02.

**PLAN 13-02 — API-key auth dep + bindwave-python repo bootstrap (Wave 0)**
Ships: `backend/auth/api_key_dependencies.py` with `get_current_api_key` (parses `Authorization: Bearer bw_…`, queries `api_keys` by prefix, verifies HMAC, checks `revoked_at IS NULL`, debounced `last_used_at` update, returns `(org_id, role)` tuple), `backend/auth/api_keys.py` (the `generate_api_key` + `verify_api_key` helpers from §2.10), `require_role_api(*allowed)` factory in the same file (§5.2), unit tests for the dep + helpers. ALSO ships the empty `bindwave-python` repo (separate GitHub repo) with the file scaffolding from RESEARCH §2.5 — `pyproject.toml`, `src/bindwave/__init__.py` re-exporting placeholder classes, `tests/conftest.py`, `.github/workflows/ci.yml` + `release.yml`, README/CHANGELOG/LICENSE. NO endpoint surface yet. Requirements satisfied: API-01 (helpers), API-02 (dep), API-03 (revoked check). Depends on: 13-01 schema. Parallel-safe with 13-01 (use a stub schema in tests).

**PLAN 13-03 — /api/v1/jobs router + idempotency + RFC 7807 + rate-limit pipeline (Wave 1)**
Ships: `backend/api/v1/__init__.py`, `backend/api/v1/_cursor.py` (§2.3), `backend/api/v1/_problem_details.py` (§2.7), `backend/api/v1/_idempotency.py` (§2.9 lifecycle helpers), `backend/api/v1/jobs.py` with the four endpoints (POST /jobs, GET /jobs, GET /jobs/{id}, POST /jobs/{id}/cancel), `backend/middleware/rate_limit.py` extended with `api_v1_limiter` instance (§2.4), `backend/jobs/serialize.py` extracted from webhook handler (§5.13), `backend/main.py` wiring (router include + exception handlers + dispatch-aware rate-limit selection — §5.4), CSRFMiddleware `exempt_urls=[r"^/api/v1/"]` (§5.3). Requirements satisfied: API-04, API-05, API-06, API-07, API-08, API-10. Depends on: 13-01 (schema + scaffolds), 13-02 (auth dep). Test scope: all of `backend/tests/api_v1/` from §4.4.

**PLAN 13-04 — /api/v1/api-keys self-management + bindwave-python jobs.py / api_keys.py (Wave 1)**
Ships: `backend/api/v1/api_keys.py` (GET /api/v1/api-keys list-self, POST /api/v1/api-keys/{id}/revoke — note these are SDK-side so a service can rotate its own keys; the WEB UI uses `/user/api-keys` per §5.5), `backend/user/api_keys.py` (the WEB endpoints for SettingsPage: GET list, POST create returns plaintext once, POST revoke), `bindwave-python/src/bindwave/jobs.py` and `api_keys.py` with the sync `Client` methods (`client.jobs.submit/get/list/cancel`, `client.api_keys.list/revoke`), httpx transport in `_client.py`, exception parsing in `_exceptions.py`, the README quickstart example. Requirements satisfied: API-01 (web endpoint), API-03 (web revoke). Depends on: 13-03 (the v1 surface + idempotency machinery). Test scope: `bindwave-python/tests/test_jobs.py` + `test_api_keys.py` with respx mocks.

**PLAN 13-05 — AsyncClient + auto-paginator + Job.wait_until_complete + SDK tests (Wave 2)**
Ships: `bindwave-python/src/bindwave/_client.py` AsyncClient class (D-10), `bindwave-python/src/bindwave/_cursor.py` `iter_all` auto-paginator (D-11), `Job.wait_until_complete(poll_every=30, timeout=None)` method on the typed Job model (D-11), `Job.download_results(dest_dir=)` helper (D-11), full pydantic types in `types/`, the `examples/batch_submit.py` + `examples/async_pipeline.py` files. Requirements satisfied: API-12 SDK surface complete. Depends on: 13-04. Test scope: SDK-only.

**PLAN 13-06 — SettingsPage 6th tab + last-used debouncing + revoke modal (Wave 2)**
Ships: `frontend/src/pages/SettingsPage.tsx` adds "API Keys" tab between "Privacy" and "Usage" (§5.12), `frontend/src/components/api-keys/ApiKeysTab.tsx` (create form + list + revoke modal), `frontend/src/components/api-keys/CreateKeyModal.tsx` (shows plaintext exactly once with copy-to-clipboard + "I have saved this key" confirmation gate per CONTEXT.md `:235`), `frontend/src/lib/api-keys.ts` (typed client for the web endpoints), backend debounced `last_used_at` updater (background task, predicate from §2.2). Requirements satisfied: Phase 13 SC 3 (manage in user settings). Depends on: 13-04. Test scope: Vitest specs for the new tab, manual smoke for the modal copy-once UX.

**PLAN 13-07 — ROADMAP correction + OpenAPI snapshot lock + contract test + release (Wave 3)**
Ships: ROADMAP.md edit changing Phase 13 SC 6 from `pip install kendrew` to `pip install bindwave` (D-09 + CONTEXT.md `:208` — same pattern as Phase 11 SC 6/8 correction), `backend/tests/contract/_openapi_paths_snapshot.txt` regenerated (snapshot tests now lock in the final post-D-15 surface), `backend/tests/contract/test_openapi_contract.py` (the SDK contract test from §2.6), `backend/tests/contract/_sdk_contract_v0_1_0.py` (the vendored SDK contract table), REQUIREMENTS.md updates (API-01..API-12 added, PLAT-V2-01 promoted to Phase 13 and marked Validated), `.planning/phases/13-public-api/13-VERIFICATION.md` with the 30+ must-haves, `bindwave-python` v0.1.0 git tag (triggers PyPI release workflow). Depends on: 13-03, 13-04, 13-05, 13-06. This plan is the verification + release coordinator.

### Dependency graph

```
13-01 ─┐
       ├─ 13-03 ─┬─ 13-04 ─┬─ 13-05 ─┐
13-02 ─┘         │         │         ├─ 13-07
                 │         └─ 13-06 ─┘
                 │
                 └─ (13-04 also depends on 13-03)
```

Wave 0 (13-01 + 13-02): parallel-safe, ≈ 1 wave merge.
Wave 1 (13-03, then 13-04): sequential, ≈ 1 wave merge each.
Wave 2 (13-05 + 13-06): parallel-safe after 13-04, ≈ 1 wave merge.
Wave 3 (13-07): solo, ≈ 1 wave merge.

Estimated wall-clock: ≈ 4 wave-merges × ~15 min average from STATE.md Phase 12 history.

---

## 7. Sources

### Primary (HIGH confidence)
- `backend/middleware/rate_limit.py` — current rate-limit middleware uses Redis + slowapi (line 58)
- `backend/auth/org_dependencies.py` — get_active_org returns (org_id, role) tuple, require_role factory (lines 31-93)
- `backend/main.py` — middleware stack ordering (lines 81-112), router registration (lines 115-135)
- `backend/jobs/router.py` — current web-flow surface (lines 33, 105, 355, 504, 561, 670)
- `backend/jobs/service.py` — cancel_job_by_id resolves billing via JOIN (lines 132-142)
- `backend/jobs/dispatch.py` — BILL-04 DB-first dispatch pattern (lines 55-94)
- `backend/billing/stripe_client.py` — get_or_create_customer is org-scoped post-Phase-12 (lines 27-65)
- `backend/config.py` — Pydantic Settings layout + existing dual-secret rotation pattern (lines 94-98)
- `backend/requirements.txt` — VERIFIED bcrypt is NOT present
- `frontend/src/lib/api.ts` — X-Org-Id opt-out list (lines 29-72)
- `frontend/src/pages/SettingsPage.tsx` — current tab structure (lines 631-637)
- `supabase/migrations/20260605000001_organizations.sql` — migration naming + index conventions (lines 27-87)
- `.planning/phases/13-public-api/13-CONTEXT.md` — D-01..D-16 locked decisions
- `.planning/STATE.md` — Phase 12 cutover + drop-column migration gating (lines 150-167)
- [VERIFIED: Anthropic SDK pyproject.toml] github.com/anthropics/anthropic-sdk-python/blob/main/pyproject.toml
- [VERIFIED: Anthropic SDK BaseClient architecture] deepwiki.com/anthropics/anthropic-sdk-python/4.2-synchronous-and-asynchronous-clients
- [VERIFIED: GitHub X-RateLimit-Reset unix epoch seconds] docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- [VERIFIED: slowapi headers_enabled + issue #33 double-headers bug] slowapi.readthedocs.io/en/latest/api/ + github.com/laurentS/slowapi/issues/33
- [VERIFIED: FastAPI exception handlers are app-scoped] github.com/fastapi/fastapi/discussions/8059
- [VERIFIED: FastAPI include_in_schema=False semantics] fastapi.tiangolo.com/advanced/path-operation-advanced-configuration/

### Secondary (MEDIUM confidence)
- [CITED: brandur.org/idempotency-keys] — Postgres-table idempotency reference implementation, 24h TTL pattern
- [CITED: docs.stripe.com/api/idempotent_requests] — Stripe idempotency semantics (24h, replay, body-mismatch error)
- [CITED: datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07] — IETF draft for Idempotency-Key header
- [CITED: stripe.com/blog/idempotency] — Stripe's design notes on idempotency keys including body-hash check
- [CITED: ssojet.com/compare-hashing-algorithms/hmac-sha256-vs-bcrypt] — bcrypt is for passwords; HMAC-SHA256 for API tokens
- [CITED: cybersierra.co/blog/bcrypt-performance-issues-api] — bcrypt-per-API-request DoS amplifier analysis

### Tertiary (LOW confidence — informational only)
- Stripe / OpenAI cursor encoding patterns — knowledge from prior research, not verified against latest docs in this session

---

## 8. Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | bcrypt is acceptable to swap for HMAC-SHA256 + pepper despite D-03 saying "bcrypt" | §2.10 | Planner must surface to user during plan-check; column name kept as `bcrypt_hash` for D-03 compatibility, with documenting COMMENT. If user insists on bcrypt, fall back to cost 10. |
| A2 | The 12 existing routers can all be hidden from OpenAPI without breaking any in-prod integration | §2.8, §5.4 | Low risk — nothing is currently consuming the spec because Phase 13 IS the API launch. But /docs is currently public + reachable; a fast prod check (curl /openapi.json before merging the flip) catches anything we missed. |
| A3 | The Phase 12 cutover runbook will fully complete before Phase 13 starts (organizations_enabled=True, drop-column migration applied) | §5.11, §5.14 | If Phase 13 starts before runbook step 9, plans must include a guard checklist; recommend the planner add this to PLAN 13-01's pre-flight. |
| A4 | Settings UI is the right home for API-key CRUD (not a separate /developer page) | §5.12 | Low risk — CONTEXT.md `:175` is explicit on the SettingsPage tab approach. |
| A5 | The "frontend never calls /api/v1/*" assumption holds — SDK + curl only | §5.5 | Low — D-01 implies it (API keys never need X-Org-Id), and the SettingsPage uses /user/api-keys per CONTEXT.md `:168`. |

---

## 9. Open Questions

1. **Idempotency-key reaper cron**
   - What we know: Need to delete rows older than 25h.
   - What's unclear: Is the existing arq scheduler the right home, or do we need a separate cron container?
   - Recommendation: Use arq's `cron_jobs` config (already in worker — `backend/worker/tasks.py` has the dispatch worker). Add a `cleanup_idempotency_keys` cron job. 0 net infra surface.

2. **Pepper rotation policy**
   - What we know: We have a current/prev pattern from `webhook_hmac_secret` precedent (`config.py:94-95`).
   - What's unclear: How long is the "grace window"? The webhook rotation is operator-driven; the API-key pepper rotation needs the same docs.
   - Recommendation: Add to `docs/deploy.md` Phase 11 D-10 rotation runbook — extend it to cover api_key_pepper too. Same 24h window suffices: every API key gets exercised regularly enough.

3. **bindwave-python release governance**
   - What we know: Tag-push triggers PyPI release.
   - What's unclear: Who has PyPI org membership on `bindwave`? Does Ranomics own the namespace?
   - Recommendation: PLAN 13-07 pre-flight: confirm PyPI namespace ownership. Reserve the name before tagging.

---

## 10. Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Postgres (Supabase) | api_keys + idempotency tables | ✓ | 17.x | — |
| Redis (Upstash) | rate-limit bucket | ✓ | — | — |
| Python stdlib `hmac`/`hashlib` | API-key hashing | ✓ | 3.12 | — |
| `httpx` 0.28.1 | bindwave-python transport | ✓ | 0.28.1 (already in backend reqs) | — |
| `pydantic` v2 | bindwave-python types | ✓ | — | — |
| GitHub Actions (release.yml) | bindwave-python PyPI publish | ✓ | — | — |
| PyPI namespace `bindwave` | SDK publish | ✗ (per A5/Open Q3) | — | Confirm ownership in PLAN 13-07 pre-flight |
| `respx` 0.22.0 | SDK test mocks | ✓ | 0.22.0 (already in backend reqs) | — |

**Missing dependencies with no fallback:** None (pending PyPI confirmation).
**Missing dependencies with fallback:** None.

---

## 11. Metadata

**Confidence breakdown:**
- Locked decisions D-01..D-16: HIGH (verified against CONTEXT.md verbatim).
- Idempotency storage backend (§2.1): HIGH (Postgres pattern is industry-standard reference).
- bcrypt swap (§2.10): MEDIUM-HIGH (high confidence in technical recommendation; LOW confidence in user acceptance — flagged via A1).
- OpenAPI surface migration (§2.8): HIGH (grep verified zero existing usage of `include_in_schema`).
- Plan-split: MEDIUM (boundaries are reasonable but the planner may collapse 13-04 + 13-05 if the SDK is small enough).
- Landmines: HIGH (every file:line cited was directly read in this session).

**Research date:** 2026-06-04
**Valid until:** 2026-07-04 (30 days; FastAPI / slowapi / Anthropic SDK are stable enough for a 30-day window).
