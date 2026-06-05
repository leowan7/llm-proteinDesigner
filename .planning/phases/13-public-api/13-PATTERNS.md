# Phase 13: Public API - Pattern Map

**Mapped:** 2026-06-04
**Files analyzed:** 28 new + 6 modified = 34
**Analogs found:** 32 / 34 (94%)
**Grouping:** Wave 0 / 1 / 2 / 3 per RESEARCH.md §6

---

## Top-Line Summary

Phase 13 is overwhelmingly mirror-pattern work. Almost every new backend file has a same-shape analog in the existing FastAPI codebase:

| Concern | Existing analog | New file mirrors |
|---|---|---|
| Auth dep returning identity tuple | `backend/auth/dependencies.py::get_current_user` (cookie -> JWT decode -> `sub`) | `backend/auth/api_key_dependencies.py::get_current_api_key` (header -> prefix lookup -> HMAC verify -> `(org_id, role)`) |
| Slow-hash + dual-secret rotation | `backend/webhooks/router.py:51-94` (HMAC-SHA256 with `_secret` + `_secret_prev`) | `backend/auth/api_keys.py::verify_api_key` (HMAC with `api_key_pepper` + `_prev`) |
| Router scaffold | `backend/jobs/router.py:1-35` (imports + `APIRouter(prefix=..., tags=...)`) | `backend/api/v1/jobs.py` (same shape + `include_in_schema=True` left default) |
| Cancel ownership-check pattern | `backend/jobs/router.py:514-548` (ownership SELECT then delegate to service) | `backend/api/v1/jobs.py::cancel_job` (org-scoped SELECT then delegate to `cancel_job_by_id`) |
| Keyset pagination | `backend/jobs/router.py:336-421::list_jobs` (`before` cursor, ISO timestamp, `has_more`) | `backend/api/v1/jobs.py::list_jobs` (opaque base64 cursor on `(created_at, id)`) |
| Slowapi + Redis limiter | `backend/middleware/rate_limit.py:55-60` | Second `api_v1_limiter` in same file |
| Migration shape | `supabase/migrations/20260420000001_job_tier_and_budget.sql` (ALTER + indexes + COMMENT) | `supabase/migrations/20260607000001_api_keys.sql` (CREATE TABLE + partial indexes + COMMENT) |
| Pydantic settings + dual rotation | `backend/config.py:94-95` (`webhook_hmac_secret` + `_prev`) | New `api_key_pepper` + `_prev` + `api_v1_rate_limit` |
| Pytest auth-dep override pattern | `backend/tests/jobs/test_cancel.py:21-25` (`app.dependency_overrides[get_current_user]`) | All `backend/tests/api_v1/*.py` (override `get_current_api_key`) |
| Frontend tab page | `frontend/src/pages/SettingsPage.tsx:461-555` (`Tabs` + `TabsList` + `TabsContent`) | `frontend/src/components/api-keys/ApiKeysTab.tsx` |
| Frontend lib API client | `frontend/src/lib/user.ts:1-100` (`api()` wrapper + typed interfaces) | `frontend/src/lib/api-keys.ts` |

Two files have no direct analog and must be designed from first principles using research-cited industry references:

- `backend/api/v1/idempotency.py` (3-state Postgres idempotency lifecycle - reference: Brandur + Stripe blog cited in RESEARCH §2.9)
- `bindwave-python/*` (entire SDK - reference: Anthropic SDK layout cited in RESEARCH §2.5)

---

## File Classification

| Path | Status | Role | Data Flow | Closest Analog | Match |
|---|---|---|---|---|---|
| `backend/main.py` | modify | app entry / router wiring | request-response | self (lines 81-128) | exact |
| `backend/middleware/rate_limit.py` | modify | middleware / limiter factory | request-response | self (lines 55-60) | exact |
| `backend/auth/api_key_dependencies.py` | new | FastAPI dep / authn | request-response | `backend/auth/dependencies.py:9-44` | exact role, different transport |
| `backend/auth/api_keys.py` | new | crypto util (HMAC + token mint) | one-shot transform | `backend/webhooks/router.py:51-94` | exact role |
| `backend/auth/org_dependencies.py` | modify | role guard factory | request-response | self (RESEARCH §5.2) | refactor in-place |
| `backend/api/v1/__init__.py` | new | router aggregator | n/a | `backend/jobs/__init__.py` | exact |
| `backend/api/v1/jobs.py` | new | router / 4 endpoints | request-response (CRUD-style) | `backend/jobs/router.py:103-548` | exact role, different auth dep |
| `backend/api/v1/api_keys.py` | new | router / 2 endpoints | request-response | `backend/jobs/router.py:514-548` (ownership-check shape) | role-match |
| `backend/api/v1/cursor.py` | new | encode/decode util | one-shot transform | `backend/jobs/router.py:374-385` (timestamp cursor parsing) | role-match |
| `backend/api/v1/idempotency.py` | new | DB-backed 3-state store | transaction-coordinated CRUD | none in codebase | RESEARCH §2.9 |
| `backend/api/v1/errors.py` | new | exception handler / formatter | request-response (error tail) | `backend/middleware/rate_limit.py:74` (`add_exception_handler` registration) | role-match |
| `backend/jobs/serialize.py` | new | shared serializer (extracted) | one-shot transform | `backend/webhooks/router.py:222-246` (results JSON build) | direct extract |
| `backend/jobs/dispatch.py` | modify | dispatch service | DB-then-enqueue | self (lines 16-63) - add optional `conn` param | exact, parameter-add |
| `backend/user/api_keys.py` | new | router / web-flow CRUD | request-response | `backend/user/router.py:96-110` + `backend/auth/router.py:81-150` | exact (signup mirrors mint-once flow) |
| `supabase/migrations/20260607000001_api_keys.sql` | new | migration / table | n/a | `supabase/migrations/20260420000001_job_tier_and_budget.sql` | role-match |
| `supabase/migrations/20260607000002_api_key_idempotency.sql` | new | migration / table | n/a | same as above | role-match |
| `backend/middleware/csrf.py` (call site `main.py:96-104`) | modify | middleware config | request-response | self (RESEARCH §5.3) | parameter add |
| `backend/tests/api_v1/__init__.py` | new | test package marker | n/a | `backend/tests/jobs/__init__.py` | exact |
| `backend/tests/api_v1/conftest.py` | new | pytest fixtures | n/a | `backend/tests/conftest.py` | exact role |
| `backend/tests/api_v1/test_api_keys.py` | new | unit test | request-response | `backend/tests/jobs/test_cancel.py:82-120` | exact role |
| `backend/tests/api_v1/test_auth.py` | new | unit test | request-response | same as above | exact role |
| `backend/tests/api_v1/test_idempotency.py` | new | integration test | request-response (state machine) | `backend/tests/jobs/test_cancel.py:36-67` (multi-conn pool mock) | role-match |
| `backend/tests/api_v1/test_pagination.py` | new | integration test | request-response | `backend/tests/jobs/test_cancel.py:82-120` | exact role |
| `backend/tests/api_v1/test_cursor.py` | new | unit test | one-shot transform | `backend/tests/middleware/test_rate_limit.py:1-86` (pure-function tests) | exact role |
| `backend/tests/api_v1/test_jobs_get.py` | new | integration test | request-response | `backend/tests/jobs/test_cancel.py:82-120` | exact role |
| `backend/tests/api_v1/test_errors.py` | new | integration test | request-response | same as above | exact role |
| `backend/tests/api_v1/test_rate_limit.py` | new | integration test | request-response | `backend/tests/middleware/test_rate_limit.py` | exact role |
| `backend/tests/contract/test_openapi_snapshot.py` | new | contract test | one-shot read | none (industry pattern) | RESEARCH §2.12 |
| `backend/tests/contract/test_openapi_contract.py` | new | contract test | one-shot read | none (industry pattern) | RESEARCH §2.6 |
| `backend/tests/contract/test_routers_hidden.py` | new | unit test | one-shot read | `backend/tests/middleware/test_rate_limit.py` (pure-state-inspection style) | role-match |
| `backend/tests/contract/_openapi_paths_snapshot.txt` | new | fixture | n/a | none | RESEARCH §2.12 |
| `backend/tests/contract/_sdk_contract_v0_1_0.py` | new | fixture | n/a | none | RESEARCH §2.6 |
| `bindwave-python/pyproject.toml` | new | build config | n/a | Anthropic SDK upstream | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/__init__.py` | new | re-export | n/a | Anthropic SDK upstream | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_client.py` | new | base + sync HTTP client | request-response | Anthropic SDK `BaseClient[HttpxClientT]` | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_async_client.py` | new | async HTTP client | request-response | same | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/jobs.py` | new | endpoint methods | request-response | Anthropic SDK resource modules | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/api_keys.py` | new | endpoint methods | request-response | same | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_exceptions.py` | new | exception hierarchy | one-shot transform | Anthropic SDK exception tree | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_pagination.py` | new | cursor iterator | streaming | Anthropic SDK paginators | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_idempotency.py` | new | uuid4 helper | one-shot transform | trivial | RESEARCH §2.5 |
| `bindwave-python/.github/workflows/ci.yml` | new | CI workflow | n/a | Anthropic SDK release.yml | RESEARCH §2.5 |
| `bindwave-python/.github/workflows/release.yml` | new | release workflow | n/a | same | RESEARCH §2.5 |
| `frontend/src/pages/SettingsPage.tsx` | modify | tab page | request-response | self (lines 461-555) | exact, tab-insertion |
| `frontend/src/components/api-keys/ApiKeysTab.tsx` | new | UI component | request-response | `frontend/src/components/legal/PrivacyTab.tsx` (sibling tab pattern) | exact role |
| `frontend/src/components/api-keys/CreateApiKeyModal.tsx` | new | UI component | request-response | `frontend/src/components/ui/dialog.tsx` consumers + auth modal patterns | role-match |
| `frontend/src/lib/api-keys.ts` | new | typed HTTP client | request-response | `frontend/src/lib/user.ts:1-100` | exact role |

---

# WAVE 0 - Foundation

## `supabase/migrations/20260607000001_api_keys.sql` (new, migration, n/a)

**Analog:** `supabase/migrations/20260420000001_job_tier_and_budget.sql` (whole file)

**Excerpt to mirror** (the analog, full):
```sql
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS job_tier text NOT NULL DEFAULT 'pilot'
        CHECK (job_tier IN ('pilot', 'full_design')),
    ADD COLUMN IF NOT EXISTS total_budget_hours int NOT NULL DEFAULT 4
        CHECK (total_budget_hours > 0 AND total_budget_hours <= 96),
    ...

CREATE INDEX IF NOT EXISTS idx_jobs_user_tier_tool_status
    ON public.jobs (user_id, job_tier, status)
    WHERE status = 'complete';

COMMENT ON COLUMN public.jobs.job_tier IS
    'Job tier: pilot (validation run) or full_design (real campaign). See Modal migration plan.';
```

Plus the foundational `gen_random_uuid()` PK pattern from `supabase/migrations/20260318000000_init.sql:10-19`:
```sql
CREATE TABLE IF NOT EXISTS public.jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ...
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Adaptation:**
- New CREATE TABLE (not ALTER) - this is brand-new surface
- Use `gen_random_uuid()` PK, `ON DELETE CASCADE` to org table per RESEARCH §2.2
- Mirror the `CREATE INDEX IF NOT EXISTS ... WHERE revoked_at IS NULL` partial-index pattern from the analog's `WHERE status = 'complete'`
- Use `COMMENT ON COLUMN` to document the `bcrypt_hash` column actually holds HMAC-SHA256 (RESEARCH §2.10 - column name retained for D-03 compatibility)
- Schema: see RESEARCH §2.2 lines 53-68 verbatim

---

## `supabase/migrations/20260607000002_api_key_idempotency.sql` (new)

**Analog:** Same file as above (table-creation shape).

**Adaptation:** Identical migration shape; columns per RESEARCH §2.9 lines 315-326 (3-state lifecycle: pending|completed). Composite PK `(api_key_id, idempotency_key)`. Add `CREATE INDEX idx_api_key_idem_created ON ... (created_at)` to support the reaper cron.

---

## `backend/config.py` (modify - add settings block)

**Analog:** `backend/config.py:94-100` - the dual-secret rotation pattern.

**Excerpt to mirror:**
```python
# Dual-secret webhook rotation (Phase 11 D-10, amended 2026-04-24).
# Single shared secret covers both Modal and RunPod webhook HMAC - see
# .planning/phases/11-deployment/11-CONTEXT.md §D-10.
# Backend tries webhook_hmac_secret first, falls back to webhook_hmac_secret_prev
# during rotation grace windows. Rotation runbook in docs/deploy.md (Plan 11-05).
webhook_hmac_secret: str = ""
webhook_hmac_secret_prev: str = ""
```

**Adaptation:** Add directly below, identical shape:
```python
# Phase 13: API-key hashing secrets (HMAC-SHA256 pepper). Same dual-secret
# rotation pattern as webhook_hmac_secret. Backend tries api_key_pepper first,
# falls back to api_key_pepper_prev during rotation. Rotation runbook in
# docs/deploy.md - extend the Phase 11 D-10 section.
api_key_pepper: str = ""
api_key_pepper_prev: str = ""

# Phase 13: per-API-key rate limit (slowapi format). Applied by api_v1_limiter
# only - the existing global limiter keeps its rate_limit_default budget.
api_v1_rate_limit: str = "60/minute"

# Phase 13: idempotency-key TTL. Rows older than this are swept by an arq cron.
# 25h gives a 1h buffer over the documented 24h replay window.
idempotency_ttl_hours: int = 25
```

---

## `backend/main.py` (modify - register v1 router + flip include_in_schema + exception handlers)

**Analog:** Self at lines 74-128. Specifically:
- Lines 74-78: `app = FastAPI(title=..., version=..., lifespan=...)`
- Lines 115-128: router registration block

**Excerpt to mirror** (lines 115-128):
```python
# Routers
app.include_router(auth_router)
app.include_router(pdb_router)
app.include_router(agent_router)
app.include_router(billing_router)
app.include_router(webhooks_router)
app.include_router(jobs_router)
app.include_router(sessions_router)
app.include_router(user_router)
app.include_router(admin_router)

# Phase 11 SC 8: synthetic-error endpoint for Sentry verification (dev only).
if settings.debug or settings.testing:
    from debug_routes import router as debug_router
    app.include_router(debug_router)
```

And the `@app.get("/health")` decoration on lines 134-163.

**Adaptation:**
- Add `from api.v1 import router as api_v1_router` import
- Add `app.include_router(api_v1_router)` at end of router block (still discoverable but its routes are tagged `["api_v1"]`)
- Register two exception handlers AFTER `setup_rate_limiting(app)` (per RESEARCH §2.7 lines 274-275):
  ```python
  from api.v1.errors import http_exception_handler, validation_exception_handler
  from fastapi.exceptions import HTTPException, RequestValidationError
  app.add_exception_handler(HTTPException, http_exception_handler)
  app.add_exception_handler(RequestValidationError, validation_exception_handler)
  ```
- Update `app = FastAPI(title="Kendrew.AI", ...)` to `title="Bindwave"` AND add OpenAPI metadata block (Phase 13 also renames per Phase 12 cutover):
  ```python
  app = FastAPI(
      title="Bindwave Public API",
      version="0.1.0",
      docs_url="/api/docs",
      openapi_url="/api/openapi.json",
      lifespan=lifespan,
  )
  ```
- Flip `include_in_schema=False` on the bare `/health` route: `@app.get("/health", include_in_schema=False)` (RESEARCH §2.8 - "/health is internal").

---

## All 12 legacy routers (modify - add `include_in_schema=False`)

**Analog:** Each router file's `APIRouter(prefix=..., tags=[...])` call. Example from `backend/jobs/router.py:32`:
```python
router = APIRouter(prefix="/jobs", tags=["jobs"])
```

**Adaptation - identical one-line edit on each of these 12 files** (per RESEARCH §2.8 table):

| File:line | Current | New |
|---|---|---|
| `backend/auth/router.py:21` | `APIRouter(prefix="/auth", tags=["auth"])` | `APIRouter(prefix="/auth", tags=["auth"], include_in_schema=False)` |
| `backend/agent/router.py:44` | `APIRouter(prefix="/agent", tags=["agent"])` | add `, include_in_schema=False` |
| `backend/admin/router.py:32` | `APIRouter(prefix="/admin", tags=["admin"])` | add `, include_in_schema=False` |
| `backend/billing/router.py:24` | `APIRouter(prefix="/billing", tags=["billing"])` | add `, include_in_schema=False` |
| `backend/debug_routes.py:11` | `APIRouter(prefix="/debug", tags=["debug"])` | add `, include_in_schema=False` |
| `backend/jobs/router.py:32` | `APIRouter(prefix="/jobs", tags=["jobs"])` | add `, include_in_schema=False` |
| `backend/organizations/router.py:41` (Phase 12) | `/organizations` router | add `, include_in_schema=False` |
| `backend/organizations/router.py:42` (Phase 12) | `/invitations` router | add `, include_in_schema=False` |
| `backend/pdb_utils/router.py:25` | `APIRouter(prefix="/pdb", tags=["pdb"])` | add `, include_in_schema=False` |
| `backend/sessions/router.py:32` | `APIRouter(prefix="/sessions", tags=["sessions"])` | add `, include_in_schema=False` |
| `backend/user/router.py:30` | `APIRouter(prefix="/user", tags=["user"])` | add `, include_in_schema=False` |
| `backend/webhooks/router.py:40` | `APIRouter(prefix="/webhooks", tags=["webhooks"])` | add `, include_in_schema=False` |

Note: this is 12 mechanical edits + the `/health` route flip in `main.py`.

---

## `backend/tests/contract/_openapi_paths_snapshot.txt` (new, fixture)

**Analog:** None in codebase. Industry pattern (Jest/Vitest snapshot test).

**Content shape** (verbatim per RESEARCH §2.12):
- One path per line, alphabetically sorted
- Initial snapshot lists ONLY `/api/v1/*` paths (because Wave 0 also flips `include_in_schema=False` on all legacy routers in the same wave)
- Example entries to seed:
```
/api/v1/api-keys
/api/v1/api-keys/{key_id}/revoke
/api/v1/jobs
/api/v1/jobs/{job_id}
/api/v1/jobs/{job_id}/cancel
```

---

## `backend/tests/contract/test_openapi_snapshot.py` (new, contract test)

**Analog:** None in codebase. Snapshot pattern from RESEARCH §2.12 lines 413-421.

**Code to write** (verbatim from RESEARCH §2.12):
```python
def test_openapi_paths_match_snapshot():
    spec = app.openapi()
    paths = sorted(spec["paths"].keys())
    with open("backend/tests/contract/_openapi_paths_snapshot.txt") as f:
        expected = [l.strip() for l in f if l.strip()]
    assert paths == expected, "OpenAPI surface changed - review with the team and update the snapshot file deliberately"
```

---

## `backend/tests/contract/test_routers_hidden.py` (new)

**Analog:** `backend/tests/middleware/test_rate_limit.py:56-85` (pure-state-inspection style):
```python
def test_rate_limit_key_with_jwt_cookie():
    user_id = "user-uuid-12345"
    token = _make_jwt(user_id)
    request = _make_request(cookies={"access_token": token})

    key = get_rate_limit_key(request)

    assert key == f"user:{user_id}"
```

**Adaptation:** Iterate `app.routes`, assert any route whose path does not start with `/api/v1/` has `route.include_in_schema is False`.

---

## `backend/tests/api_v1/__init__.py` (new, package marker)

**Analog:** `backend/tests/jobs/__init__.py` (empty file).

---

## `backend/tests/api_v1/conftest.py` (new, fixtures)

**Analog:** `backend/tests/conftest.py:31-84`

**Excerpt to mirror** (lines 31-42):
```python
@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Async test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

**Adaptation:** Add fixtures specific to v1:
```python
@pytest.fixture
def synthetic_api_key():
    """Returns (plaintext, hash, row_dict) tuple. Plaintext begins 'bw_test_'."""
    from auth.api_keys import generate_api_key
    plaintext, prefix, h = generate_api_key(env="test")
    return plaintext, prefix, h, {
        "id": "00000000-0000-0000-0000-000000000001",
        "organization_id": "00000000-0000-0000-0000-000000000002",
        "role_at_creation": "owner",
        "bcrypt_hash": h,
        "prefix": prefix,
        "revoked_at": None,
    }


@pytest.fixture
def idempotency_key() -> str:
    import uuid
    return uuid.uuid4().hex


@pytest.fixture
def override_api_key(synthetic_api_key):
    """Bypass get_current_api_key by injecting the synthetic key's (org_id, role)."""
    from auth.api_key_dependencies import get_current_api_key
    _, _, _, row = synthetic_api_key

    async def _dep():
        return (row["organization_id"], "owner")

    app.dependency_overrides[get_current_api_key] = _dep
    yield
    app.dependency_overrides.pop(get_current_api_key, None)
```

The shape mirrors `backend/tests/jobs/test_cancel.py:21-25`:
```python
def _override_user(user_id: str = "user-abc"):
    """Return a FastAPI dependency override that returns a fixed user ID."""
    async def _dep():
        return user_id
    return _dep
```

---

# WAVE 0 (parallel) - PLAN 13-02 = API-key auth + SDK skeleton

## `backend/auth/api_keys.py` (new, crypto util / token mint)

**Analog:** `backend/webhooks/router.py:51-94` (HMAC-SHA256 + dual-secret rotation pattern)

**Excerpt to mirror** (lines 51-94):
```python
def validate_webhook_signature(
    body: bytes,
    signature: str | None,
    current_secret: str,
    prev_secret: str | None = None,
) -> str:
    """Validate HMAC-SHA256 signature against the current secret, then _PREV.
    ...
    """
    if not current_secret and not prev_secret:
        return "dev-skip"

    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    for label, secret in (("current", current_secret), ("prev", prev_secret)):
        if not secret:
            continue
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            if label == "prev":
                logger.warning(
                    "Webhook signed with PREV secret - rotation window active"
                )
            return label

    raise HTTPException(status_code=401, detail="Invalid signature")
```

**Adaptation:** Two helpers (verbatim from RESEARCH §2.10 lines 374-389):
```python
import hmac
import hashlib
import secrets

from config import settings


def generate_api_key(env: str = "live") -> tuple[str, str, str]:
    """Returns (plaintext, prefix, hash).

    Args:
        env: "live" or "test" - drives the second token segment so tokens
             from test fixtures are unambiguously distinguishable in logs.

    Returns:
        Three-tuple:
          plaintext - "bw_<env>_<32 urlsafe chars>" - shown to user EXACTLY ONCE
          prefix    - first 12 chars, stored in DB for fast prefix lookup
          hash      - HMAC-SHA256 hex digest, peppered with settings.api_key_pepper
    """
    suffix = secrets.token_urlsafe(24)
    plaintext = f"bw_{env}_{suffix}"
    prefix = plaintext[:12]
    h = hmac.new(
        settings.api_key_pepper.encode(),
        plaintext.encode(),
        hashlib.sha256,
    ).hexdigest()
    return plaintext, prefix, h


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    """Constant-time verify using current pepper, fallback to prev for rotation."""
    for secret in (settings.api_key_pepper, settings.api_key_pepper_prev):
        if not secret:
            continue
        h = hmac.new(secret.encode(), plaintext.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(h, stored_hash):
            return True
    return False
```

The dual-pepper rotation mirrors webhooks `validate_webhook_signature` for-loop verbatim.

---

## `backend/auth/api_key_dependencies.py` (new, FastAPI dep)

**Analog:** `backend/auth/dependencies.py:9-44`

**Excerpt to mirror** (verbatim):
```python
async def get_current_user(access_token: str | None = Cookie(default=None)) -> str:
    """
    FastAPI dependency that validates a Supabase JWT from an HTTP-only cookie.
    ...
    """
    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = await jwks_verifier.verify(access_token)
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
```

**Adaptation:**
- Swap `Cookie` -> `Header(alias="Authorization")`
- Replace JWT verify with: parse `Bearer ` prefix, take next 12 chars as `prefix`, SELECT row by `prefix`, run `verify_api_key(plaintext, row['bcrypt_hash'])`, check `revoked_at IS NULL`, fire-and-forget debounced `last_used_at` UPDATE per RESEARCH §2.2.
- Return `(org_id, role)` tuple (not `user_id` str) so it composes with `require_role_api`.

```python
from typing import Annotated
from fastapi import Header, HTTPException, status, BackgroundTasks
from db.connection import get_db_pool
from auth.api_keys import verify_api_key


async def get_current_api_key(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    background_tasks: BackgroundTasks = None,
) -> tuple[str, str]:
    """Validate a bearer API key. Returns (org_id, role) tuple matching
    get_active_org's contract so require_role_api can compose."""
    if not authorization or not authorization.startswith("Bearer bw_"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    plaintext = authorization.removeprefix("Bearer ").strip()
    prefix = plaintext[:12]

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, organization_id, role_at_creation, bcrypt_hash, last_used_at
               FROM public.api_keys
               WHERE prefix = $1 AND revoked_at IS NULL""",
            prefix,
        )
    if not row or not verify_api_key(plaintext, row["bcrypt_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # Debounced last_used_at update (RESEARCH §2.2)
    if background_tasks is not None:
        background_tasks.add_task(_maybe_touch_last_used, row["id"], row["last_used_at"])

    return (str(row["organization_id"]), row["role_at_creation"])


async def _maybe_touch_last_used(api_key_id: str, last_used_at) -> None:
    """1-minute debounce per RESEARCH §2.2."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE public.api_keys
               SET last_used_at = now()
               WHERE id = $1
                 AND (last_used_at IS NULL OR last_used_at < now() - INTERVAL '1 minute')""",
            api_key_id,
        )


def require_role_api(*allowed: str):
    """Parallel to require_role (web flow). Returns the org_id; rejects 403 if role
    not in allowed.

    RESEARCH §5.2: keep this separate from require_role to avoid forcing
    get_active_org through the API-key path (which lacks X-Org-Id).
    """
    from fastapi import Depends

    async def dep(identity: tuple[str, str] = Depends(get_current_api_key)) -> str:
        org_id, role = identity
        if role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role not permitted")
        return org_id

    return dep
```

---

## `bindwave-python/pyproject.toml` (new, SDK build config)

**Analog:** None in this codebase. Reference: Anthropic SDK pyproject (cited in RESEARCH §2.5 line 137).

**Adaptation:** Hatchling backend, `httpx>=0.27` + `pydantic>=2.0` as runtime deps; dev deps `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`. See RESEARCH §2.5 file-layout block lines 141-173 for the full directory shape.

Key blocks to include:
- `[build-system]` requires `hatchling`
- `[project]` name=`bindwave`, version=`0.1.0`, dynamic readme via `hatch-fancy-pypi-readme`
- `[tool.ruff]` line-length 100
- `[tool.mypy]` strict = true
- `[tool.pytest.ini_options]` asyncio_mode = `auto`

---

## `bindwave-python/src/bindwave/__init__.py` (new, re-exports)

**Analog:** None directly; mirrors D-10 public surface contract verbatim.

**Code shape:**
```python
"""bindwave - Bindwave Public API Python SDK."""

from bindwave._client import Client
from bindwave._async_client import AsyncClient
from bindwave._exceptions import (
    BindwaveError,
    BindwaveAuthError,
    BindwaveRateLimitError,
    BindwaveValidationError,
    BindwaveJobError,
    BindwaveAPIError,
)
from bindwave.types.job import Job, JobStatus, Candidate
from bindwave.types.api_key import ApiKey

__version__ = "0.1.0"
__all__ = [
    "Client",
    "AsyncClient",
    "BindwaveError",
    "BindwaveAuthError",
    "BindwaveRateLimitError",
    "BindwaveValidationError",
    "BindwaveJobError",
    "BindwaveAPIError",
    "Job",
    "JobStatus",
    "Candidate",
    "ApiKey",
]
```

---

## `bindwave-python/.github/workflows/ci.yml` (new)

**Analog:** None in this monorepo (separate-repo SDK). Industry pattern (Anthropic SDK).

**Code shape** (skeleton):
```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install hatch
      - run: hatch run test
      - run: hatch run ruff check src tests
      - run: hatch run mypy src
```

---

## `bindwave-python/.github/workflows/release.yml` (new)

**Adaptation:** Triggered by `on: push: tags: ['v*']`. Builds via `hatch build`, publishes via `twine upload` using `PYPI_API_TOKEN` repository secret.

---

# WAVE 1 - v1 surface

## `backend/api/v1/__init__.py` (new)

**Analog:** `backend/jobs/__init__.py` (empty/marker module).

**Code shape:**
```python
"""/api/v1/* router - the public API surface (Phase 13)."""

from fastapi import APIRouter
from api.v1.jobs import router as jobs_router
from api.v1.api_keys import router as api_keys_router

router = APIRouter(prefix="/api/v1", tags=["api_v1"])
router.include_router(jobs_router)
router.include_router(api_keys_router)
```

The aggregate prefix means the inner routers omit `/api/v1` from their own prefix.

---

## `backend/api/v1/cursor.py` (new, util)

**Analog:** `backend/jobs/router.py:374-385` (timestamp-parsing pattern for `before` query param):
```python
before_dt: datetime.datetime | None = None
if before is not None:
    try:
        before_dt = datetime.datetime.fromisoformat(before)
        if before_dt.tzinfo is None:
            before_dt = before_dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid 'before' cursor '{before}'. Expected ISO 8601 timestamp.",
        )
```

**Adaptation:** RESEARCH §2.3 lines 90-104 verbatim:
```python
import base64
import json
from datetime import datetime


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

---

## `backend/api/v1/idempotency.py` (new, DB-backed state machine)

**Analog:** None in codebase. RESEARCH §2.9 is the design reference.

Closest shape-analog for the `INSERT ... ON CONFLICT DO NOTHING` + co-transactional UPDATE: `backend/auth/router.py:128-141`:
```python
if update_result == "UPDATE 0":
    # Trigger race - wait briefly, then upsert.
    await asyncio.sleep(0.2)
    await conn.execute(
        """INSERT INTO public.users (id, email, tos_accepted_at, tos_version)
           VALUES ($1, $2, now(), $3)
           ON CONFLICT (id) DO UPDATE
               SET tos_accepted_at = EXCLUDED.tos_accepted_at,
                   tos_version     = EXCLUDED.tos_version,
                   updated_at      = now()""",
        new_user_id,
        body.email,
        body.tos_version,
    )
```

**Adaptation:** 4 functions per RESEARCH §2.9:
```python
import hashlib
import json
import asyncpg
from typing import Any


def canonicalize_body(body: dict) -> str:
    """Stable string for hashing. Sorted keys, no whitespace."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def hash_body(body: dict) -> str:
    return hashlib.sha256(canonicalize_body(body).encode()).hexdigest()


async def try_begin(
    conn: asyncpg.Connection,
    api_key_id: str,
    idempotency_key: str,
    body_hash: str,
) -> dict | None:
    """Try to claim the idempotency slot.

    Returns None on success (caller proceeds to dispatch the job).
    Returns existing row dict on conflict (caller routes to replay/409/422).
    """
    inserted = await conn.fetchrow(
        """INSERT INTO public.api_key_idempotency
               (api_key_id, idempotency_key, request_body_hash, status)
           VALUES ($1, $2, $3, 'pending')
           ON CONFLICT (api_key_id, idempotency_key) DO NOTHING
           RETURNING status""",
        api_key_id, idempotency_key, body_hash,
    )
    if inserted is not None:
        return None
    # Conflict - read existing
    return await conn.fetchrow(
        """SELECT status, request_body_hash, response_status, response_body
           FROM public.api_key_idempotency
           WHERE api_key_id = $1 AND idempotency_key = $2""",
        api_key_id, idempotency_key,
    )


async def mark_complete(
    conn: asyncpg.Connection,
    api_key_id: str,
    idempotency_key: str,
    response_status: int,
    response_body: dict,
) -> None:
    await conn.execute(
        """UPDATE public.api_key_idempotency
           SET status = 'completed',
               response_status = $3,
               response_body = $4::jsonb,
               completed_at = now()
           WHERE api_key_id = $1 AND idempotency_key = $2""",
        api_key_id, idempotency_key, response_status, json.dumps(response_body),
    )
```

Decision tree at the router (RESEARCH §2.9 lifecycle steps 1-5):
1. `existing = await try_begin(...)`. If `None` -> dispatch + `mark_complete`.
2. Else if `existing["status"] == "pending"` -> return 409.
3. Else if `existing["request_body_hash"] != hash_body(body)` -> return 422.
4. Else -> replay: return `JSONResponse(existing["response_body"], status_code=existing["response_status"], headers={"X-Idempotency-Replay": "1"})`.

---

## `backend/api/v1/errors.py` (new, RFC 7807 handler)

**Analog:** `backend/middleware/rate_limit.py:74` for registration pattern:
```python
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**Code** (verbatim per RESEARCH §2.7 lines 230-273):
```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError, HTTPException
from fastapi.responses import JSONResponse

PROBLEM_TYPE_BASE = "https://bindwave.com/errors/"

# Slugify status codes for the type URL. Extend as new HTTPException details appear.
_TYPE_SLUGS = {
    400: "bad-request",
    401: "unauthorized",
    402: "payment-required",
    403: "forbidden",
    404: "not-found",
    409: "conflict",
    422: "unprocessable-entity",
    429: "too-many-requests",
    500: "internal-server-error",
}
_TITLES = {
    400: "Bad request", 401: "Unauthorized", 402: "Payment required",
    403: "Forbidden", 404: "Not found", 409: "Conflict",
    422: "Unprocessable entity", 429: "Too many requests",
    500: "Internal server error",
}


def _slug_for_status(status_code: int) -> str:
    return _TYPE_SLUGS.get(status_code, "error")


def _title_for_status(status_code: int) -> str:
    return _TITLES.get(status_code, "Error")


async def http_exception_handler(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/api/v1/"):
        from fastapi.exception_handlers import http_exception_handler as default
        return await default(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_TYPE_BASE}{_slug_for_status(exc.status_code)}",
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
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        },
    )
```

---

## `backend/middleware/rate_limit.py` (modify - add `api_v1_limiter`)

**Analog:** Self at lines 55-60 (the existing limiter constructor):
```python
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.redis_url,
    enabled=settings.rate_limit_enabled and not settings.testing,
)
```

**Adaptation:** Append a second `Limiter` instance (RESEARCH §2.4 lines 118-126):
```python
def get_api_key_id(request: Request) -> str:
    """Key func for api_v1_limiter - reads api_keys.id from request.state.

    The get_current_api_key dep is expected to set request.state.api_key_id
    after a successful verify. If missing (e.g. unauthenticated request hit
    a v1 path), fall back to IP so we still rate-limit anon traffic.
    """
    api_key_id = getattr(request.state, "api_key_id", None)
    if api_key_id:
        return f"apikey:{api_key_id}"
    return f"ip:{request.client.host}"


api_v1_limiter = Limiter(
    key_func=get_api_key_id,
    storage_uri=settings.redis_url,
    headers_enabled=True,                              # RESEARCH §2.4 - emit X-RateLimit-*
    enabled=settings.rate_limit_enabled and not settings.testing,
)
```

**CRITICAL** per RESEARCH §5.4: do NOT add a second `SlowAPIMiddleware`. The `app.state.limiter` slot is single. The new limiter is applied via the route decorator `@api_v1_limiter.limit("60/minute")` only - it does NOT need its own middleware because it inherits the existing `SlowAPIMiddleware` lifecycle.

---

## `backend/api/v1/jobs.py` (new, 4 endpoints)

**Analog 1** (router scaffold): `backend/jobs/router.py:1-35`:
```python
from agent.jobspec import JobSpec
from auth.dependencies import get_current_user
from middleware.rate_limit import limiter
from billing.stripe_client import check_payment_method, get_or_create_customer
from config import settings
from db.connection import get_db_pool
from jobs.dispatch import launch_job
from jobs.service import cancel_job_by_id, TOOL_IMAGES

router = APIRouter(prefix="/jobs", tags=["jobs"])
```

**Adaptation:** swap auth dep and limiter:
```python
from auth.api_key_dependencies import get_current_api_key, require_role_api
from middleware.rate_limit import api_v1_limiter
from jobs.serialize import serialize_job_with_candidates  # Wave 0 extract (RESEARCH §5.13)

router = APIRouter(prefix="/jobs", tags=["api_v1_jobs"])
```

**Analog 2** (POST endpoint): `backend/jobs/router.py:103-235::launch_job_endpoint`:
```python
@router.post("/launch")
@limiter.limit("5/minute")
async def launch_job_endpoint(
    request: Request,
    body: LaunchRequest,
    user_id: str = Depends(get_current_user),
):
    """BILL-04 / JOB-01: Payment gate check then job dispatch."""
    ...
    pool = await get_db_pool()
    # Fetch job row to validate ownership and get job_spec.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT job_spec FROM public.jobs WHERE id = $1 AND user_id = $2",
            body.job_id, user_id,
        )
    ...
    await launch_job(job_id=body.job_id, job_spec=job_spec, ...)
    return {"job_id": body.job_id, "status": "queued", ...}
```

**Adaptation for `POST /api/v1/jobs`:**
- Replace `get_current_user` -> `require_role_api("owner", "admin", "member")` (returns `org_id`)
- Replace per-user-ownership SELECT -> per-org-ownership SELECT
- Wrap in idempotency machinery from `api/v1/idempotency.py` per RESEARCH §2.9 lifecycle - `Idempotency-Key` header required (return 400 if missing per API-04)
- Body schema is the SDK contract: `{"tool": str, "parameters": dict}` plus optional `name`
- Wrap in `async with pool.acquire() as conn: async with conn.transaction():` then co-write idempotency INSERT + jobs INSERT
- Set `request.state.api_key_id` from the dep so `api_v1_limiter` keys on it
- Apply `@api_v1_limiter.limit(settings.api_v1_rate_limit)` decorator

**Analog 3** (GET list): `backend/jobs/router.py:336-421::list_jobs`:
```python
@router.get("/")
async def list_jobs(
    limit: int = Query(default=25, ge=1, le=100),
    status: str | None = Query(default=None),
    before: str | None = Query(default=None),
    user_id: str = Depends(get_current_user),
):
    """Return paginated job history for the current user.

    Supports keyset pagination via the ``before`` cursor (ISO timestamp) and
    optional status filtering."""
    ...
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, tool, status, name, created_at, completed_at,
                      gpu_cost_usd, results->>'candidate_count' AS candidate_count,
                      session_id
               FROM public.jobs
               WHERE user_id = $1
                 AND ($2::text IS NULL OR status = $2)
                 AND ($3::timestamptz IS NULL OR created_at < $3)
               ORDER BY created_at DESC
               LIMIT $4""",
            user_id, status, before_dt, limit,
        )
    ...
    return {"jobs": jobs, "has_more": len(rows) == limit}
```

**Adaptation for `GET /api/v1/jobs`:**
- Swap user-scoped `WHERE user_id = $1` -> org-scoped `WHERE organization_id = $1`
- Swap `before: str` ISO -> `cursor: str` opaque, decode via `cursor.decode_cursor`; on decode failure return 400 problem+json
- Order tiebreaker: `ORDER BY created_at DESC, id DESC` (RESEARCH §2.3)
- Compute next_cursor from last row: `encode_cursor(last["created_at"], last["id"])` when `len(rows) == limit`, else `None`
- Response shape (SDK contract): `{"data": [...jobs...], "next_cursor": str | None}` not `{"jobs": [...], "has_more": bool}` (per API-05 spec)
- Add filters: `tool`, `created_after`, `created_before` per API-05

**Analog 4** (GET single + inline candidates): `backend/jobs/router.py` does not have this exact endpoint - candidates are JSONB on `jobs.results`. Combined source needed: `backend/webhooks/router.py:234-246` (candidate persistence shape) for what to read:
```python
candidates = output.get("candidates", [])
if candidates:
    async with pool.acquire() as conn:
        for c in candidates:
            await conn.execute(
                """INSERT INTO public.job_candidates (job_id, rank, pdb_key, scores)
                   VALUES ($1, $2, $3, $4::jsonb)""",
                row["id"],
                c["rank"],
                c["pdb_key"],
                json.dumps(c.get("scores", {})),
            )
```

Plus presigned URL pattern from `backend/storage/client.py:49-64`:
```python
def generate_presigned_get_url(key: str, expires_in: int = 3600) -> str:
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": key},
        ExpiresIn=expires_in,
    )
```

**Adaptation for `GET /api/v1/jobs/{job_id}`:**
- Delegate inline serialization to `backend/jobs/serialize.py::serialize_job_with_candidates(job_id, expires_in=24*3600)` (Wave 0 extract per RESEARCH §5.13)
- 24h presigned URLs per API-06 (`expires_in=86400`)

**Analog 5** (POST cancel): `backend/jobs/router.py:514-548::cancel_job`:
```python
@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, user_id: str = Depends(get_current_user)):
    """Cancel a running job."""
    pool = await get_db_pool()

    # Ownership check - user can only cancel their own jobs.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id FROM public.jobs
               WHERE id = $1 AND user_id = $2 AND status IN ('running', 'queued')""",
            job_id, user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="No running job found")

    # Delegate business logic (RunPod cancel + billing + DB + SSE) to shared service.
    result = await cancel_job_by_id(job_id, pool)

    return {"status": result["status"], "gpu_seconds": result["gpu_seconds"], "gpu_cost_usd": result["gpu_cost_usd"]}
```

**Adaptation:** swap `user_id` for `org_id` from `require_role_api(...)`; ownership query becomes `WHERE id = $1 AND organization_id = $2 AND status IN ('running', 'queued')` (RESEARCH §5.7 confirms `cancel_job_by_id` is org-blind so we keep the router-level check); delegate to identical `cancel_job_by_id(job_id, pool)`.

---

## `backend/jobs/dispatch.py` (modify - accept optional `conn`)

**Analog:** Self, lines 16-63 (the existing `launch_job` function, full body shown earlier).

**Adaptation per RESEARCH §5.6:** Add optional `conn: asyncpg.Connection | None = None`. When supplied, use it directly instead of `pool.acquire()`. This lets `api/v1/jobs.py` open ONE transaction and co-write idempotency row + jobs UPDATE.

```python
async def launch_job(
    job_id: str,
    job_spec: JobSpec,
    user_id: str | None,
    pool: asyncpg.Pool,
    job_tier: str = "pilot",
    total_budget_hours: int = 4,
    conn: asyncpg.Connection | None = None,   # NEW: for v1 idempotency co-transaction
    organization_id: str | None = None,        # NEW: post-Phase-12 org tenancy
) -> None:
    # ... existing docstring ...

    # 1. DB write first - BILL-04 compliance.
    async def _do_update(c: asyncpg.Connection) -> None:
        await c.execute(
            """UPDATE public.jobs
               SET status = 'queued',
                   job_spec = $1::jsonb,
                   job_tier = $4,
                   total_budget_hours = $5,
                   updated_at = NOW()
               WHERE id = $2 AND (user_id = $3 OR organization_id = $6)""",
            job_spec.model_dump_json(), job_id, user_id, job_tier, total_budget_hours,
            organization_id,
        )

    if conn is not None:
        await _do_update(conn)
    else:
        async with pool.acquire() as c:
            await _do_update(c)

    # 2. Enqueue arq task after DB write succeeds (unchanged)
    arq_pool = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
    await arq_pool.enqueue_job("run_job", job_id=job_id)
    await arq_pool.aclose()
```

---

## `backend/jobs/serialize.py` (new, extracted serializer)

**Analog:** `backend/webhooks/router.py:222-246` (candidate persistence + payload-build) PLUS `backend/storage/client.py:49-64` (presigned URLs).

**Excerpt to mirror** (webhooks 222-246):
```python
# Build results payload for completed jobs.
results_json: str | None = None
output: dict = {}
if internal_status == "complete":
    output = payload.get("output", {})
    candidate_count: int = output.get("candidate_count", 0)
    zero_output = candidate_count == 0
    results_json = json.dumps({
        "candidate_count": candidate_count,
        "next_steps": output.get("next_steps", ""),
        "zero_output": zero_output,
    })

    # Persist individual candidate rows.
    candidates = output.get("candidates", [])
    if candidates:
        async with pool.acquire() as conn:
            for c in candidates:
                await conn.execute(
                    """INSERT INTO public.job_candidates (job_id, rank, pdb_key, scores)
                       VALUES ($1, $2, $3, $4::jsonb)""",
                    row["id"], c["rank"], c["pdb_key"], json.dumps(c.get("scores", {})),
                )
```

**Adaptation:** invert it (read from DB rather than build for write); return a dict, not write to DB. Single function `serialize_job_with_candidates(job_id, pool, expires_in=86400)`:
```python
import json
from db.connection import get_db_pool
from storage.client import generate_presigned_get_url


async def serialize_job_with_candidates(
    job_id: str,
    pool,
    expires_in: int = 86400,
) -> dict | None:
    """Read job + candidates from DB, return inline dict with 24h presigned URLs.

    Returns None if no job row matches.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, tool, status, name, created_at, completed_at,
                      results, organization_id, gpu_cost_usd
               FROM public.jobs
               WHERE id = $1""",
            job_id,
        )
        if not row:
            return None
        candidates = await conn.fetch(
            """SELECT rank, pdb_key, scores
               FROM public.job_candidates
               WHERE job_id = $1
               ORDER BY rank""",
            job_id,
        )
    return {
        "id": str(row["id"]),
        "tool": row["tool"],
        "status": row["status"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "organization_id": str(row["organization_id"]) if row["organization_id"] else None,
        "gpu_cost_usd": float(row["gpu_cost_usd"]) if row["gpu_cost_usd"] else None,
        "candidates": [
            {
                "rank": c["rank"],
                "pdb_key": c["pdb_key"],
                "scores": json.loads(c["scores"]) if isinstance(c["scores"], str) else c["scores"],
                "download_url": generate_presigned_get_url(c["pdb_key"], expires_in=expires_in),
            }
            for c in candidates
        ],
    }
```

---

## `backend/middleware/csrf.py` call site (modify `main.py:96-104`)

**Analog:** Self, `main.py:96-104`:
```python
app.add_middleware(
    CSRFMiddleware,
    secret=settings.csrf_secret,
    sensitive_cookies={"access_token", "refresh_token"},
    cookie_name="csrftoken_v2",
    cookie_samesite="lax",
    cookie_secure=settings.cookie_secure,
    cookie_domain=settings.csrf_cookie_domain or None,
)
```

**Adaptation per RESEARCH §5.3:** add `exempt_urls=[r"^/api/v1/"]` kwarg.
```python
app.add_middleware(
    CSRFMiddleware,
    secret=settings.csrf_secret,
    sensitive_cookies={"access_token", "refresh_token"},
    cookie_name="csrftoken_v2",
    cookie_samesite="lax",
    cookie_secure=settings.cookie_secure,
    cookie_domain=settings.csrf_cookie_domain or None,
    exempt_urls=[r"^/api/v1/"],    # Phase 13 - API-key auth, no cookies
)
```

Verify against starlette-csrf 3.0.0 docs that the kwarg name is `exempt_urls` (not `exclude_urls`).

---

# WAVE 1 (continued) - PLAN 13-04 = self-management + SDK methods

## `backend/api/v1/api_keys.py` (new, SDK-side self-management)

**Analog:** `backend/jobs/router.py:514-548::cancel_job` (the ownership-check + delegate-to-service shape):
```python
@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, user_id: str = Depends(get_current_user)):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id FROM public.jobs
               WHERE id = $1 AND user_id = $2 AND status IN ('running', 'queued')""",
            job_id, user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="No running job found")
    result = await cancel_job_by_id(job_id, pool)
    return {...}
```

**Adaptation:** 2 endpoints (list + revoke), org-scoped:
```python
from fastapi import APIRouter, Depends, HTTPException, status
from auth.api_key_dependencies import require_role_api
from db.connection import get_db_pool

router = APIRouter(prefix="/api-keys", tags=["api_v1_api_keys"])


@router.get("/")
async def list_keys(org_id: str = Depends(require_role_api("owner", "admin", "member"))):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, prefix, created_at, last_used_at
               FROM public.api_keys
               WHERE organization_id = $1 AND revoked_at IS NULL
               ORDER BY created_at DESC""",
            org_id,
        )
    return {"data": [
        {"id": str(r["id"]), "name": r["name"], "prefix": r["prefix"],
         "created_at": r["created_at"].isoformat(),
         "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None}
        for r in rows
    ]}


@router.post("/{key_id}/revoke")
async def revoke_key(
    key_id: str,
    org_id: str = Depends(require_role_api("owner", "admin")),
):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE public.api_keys
               SET revoked_at = now()
               WHERE id = $1 AND organization_id = $2 AND revoked_at IS NULL""",
            key_id, org_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="API key not found")
    return {"id": key_id, "revoked_at": "now"}
```

---

## `backend/user/api_keys.py` (new, web-flow CRUD - what SettingsPage uses)

**Analog:** `backend/auth/router.py:81-150::signup` (the "create + return plaintext exactly once" pattern):
```python
@router.post("/signup")
@limiter.limit("3/minute;10/hour")
async def signup(request: Request, body: SignUpRequest, response: Response):
    ...
    try:
        supabase = _get_supabase()
        result = supabase.auth.sign_up({"email": body.email, "password": body.password})
        if result.user is None:
            raise HTTPException(status_code=400, detail="Signup failed")
    ...
    return {"message": "Account created. Check your email for a verification link."}
```

**Adaptation:** 3 endpoints on `/user/api-keys` (these are WEB-flow per RESEARCH §5.5):
```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from auth.org_dependencies import require_role
from auth.api_keys import generate_api_key
from db.connection import get_db_pool

router = APIRouter(prefix="/user/api-keys", tags=["user_api_keys"])
# IMPORTANT: this router is /user-prefixed (web flow), NOT /api/v1.
# It uses the WEB auth path (cookie + X-Org-Id + require_role) per RESEARCH §5.5.


class CreateKeyRequest(BaseModel):
    name: str


@router.get("/")
async def list_my_keys(org_id: str = Depends(require_role("owner", "admin", "member"))):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, prefix, created_at, last_used_at, role_at_creation
               FROM public.api_keys
               WHERE organization_id = $1 AND revoked_at IS NULL
               ORDER BY created_at DESC""",
            org_id,
        )
    return [{"id": str(r["id"]), "name": r["name"], "prefix": r["prefix"],
             "role": r["role_at_creation"],
             "created_at": r["created_at"].isoformat(),
             "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None}
            for r in rows]


@router.post("/")
async def create_key(
    body: CreateKeyRequest,
    org_role: tuple[str, str] = Depends(...),     # Use the web dep that returns BOTH org_id and role
):
    # API-01: return plaintext EXACTLY ONCE. After this response,
    # only the prefix is queryable.
    org_id, role = org_role
    plaintext, prefix, h = generate_api_key(env="live")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO public.api_keys
                   (organization_id, created_by_user_id, name, prefix, bcrypt_hash, role_at_creation)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id, created_at""",
            org_id, ...,  # user_id from cookie auth
            body.name, prefix, h, role,
        )
    return {
        "id": str(row["id"]),
        "name": body.name,
        "prefix": prefix,
        "plaintext": plaintext,  # SHOWN ONCE. Not returned by GET.
        "created_at": row["created_at"].isoformat(),
    }


@router.post("/{key_id}/revoke")
async def revoke_my_key(
    key_id: str,
    org_id: str = Depends(require_role("owner", "admin")),
):
    # Same shape as api/v1/api_keys.py revoke endpoint.
    ...
```

---

## `bindwave-python/src/bindwave/_client.py` + `_async_client.py` + `jobs.py` + `api_keys.py` (new)

**Analog:** Anthropic SDK (RESEARCH §2.5 lines 136-185). No in-codebase analog.

**Adaptation patterns** (verbatim from RESEARCH §2.5):
- `BaseClient[HttpxClientT]` generic shared by sync `Client` and `AsyncClient`
- Default `base_url = "https://api.bindwave.com/api/v1"` overridable via constructor
- Auth header: `Authorization: Bearer {api_key}`, NO `X-Org-Id` (D-01)
- Reads `BINDWAVE_API_KEY` env var if no `api_key=` constructor kwarg
- 5xx + 429 auto-retry with exponential backoff capped at 5 attempts (D-05 expectation)
- Reads `Retry-After` header on 429 to schedule next attempt
- `Idempotency-Key`: SDK auto-generates `uuid.uuid4().hex` if caller doesn't supply (so user never sees a 400)
- Exception parsing reads `application/problem+json` and routes by `type` URL slug to concrete exception class

`jobs.py` is a thin facade class `JobsResource(self._client)` exposing `.submit(...)`, `.get(...)`, `.list(...)`, `.cancel(...)`, `.iter_all(...)` (the last is deferred to Wave 2 / Plan 13-05).

---

# WAVE 1/2 - Tests

## `backend/tests/api_v1/test_api_keys.py` (new, unit)

**Analog:** `backend/tests/jobs/test_cancel.py:82-120`:
```python
class TestJobCancellation:
    @pytest.mark.anyio
    async def test_cancel_updates_db_status(self):
        ...
        app.dependency_overrides[get_current_user] = _override_user("user-abc")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=router_pool),
                patch("jobs.service.get_provider", return_value=mock_provider),
                ...
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/jobs/job-cancel-test/cancel",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
```

**Adaptation:** Override `get_current_api_key` instead of `get_current_user`; use the `synthetic_api_key` + `override_api_key` fixtures from conftest. Test the `generate_api_key`/`verify_api_key` helpers as plain unit tests (no HTTP).

**Key test cases per API-01/API-03:**
- `test_create_returns_plaintext` - POST returns `plaintext` field; GET on same row does NOT include it
- `test_revoked_key_rejects` - hand-set `revoked_at` to now, dep raises 401
- `test_generate_api_key_format` - plaintext matches `bw_live_*`; prefix is first 12 chars
- `test_verify_api_key_constant_time` - mismatched hash returns False; uses `hmac.compare_digest`
- `test_pepper_rotation` - hash from `_prev` still verifies when current is set to a new pepper

---

## `backend/tests/api_v1/test_idempotency.py` (new, integration)

**Analog:** `backend/tests/jobs/test_cancel.py:36-67` (multi-acquire pool mock pattern):
```python
def _make_router_pool(job_row, cust_row, owner_row=None):
    """Build the router+service shared pool mock.

    acquire() call sequence:
      1. router ownership check - fetchrow SELECT id WHERE id=? AND user_id=?
      2. service job fetch - fetchrow full job row
      3. service UPDATE gpu_cost_usd - returns "UPDATE 1"
      4. service customer fetch - fetchrow stripe_customer_id
    """
    ...
    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=[
        _make_ctx(owner_conn),
        _make_ctx(job_conn),
        _make_ctx(exec_conn),
        _make_ctx(cust_conn),
    ])
    return pool
```

**Adaptation:** Per RESEARCH §4.2, three test cases against the lifecycle state machine:

1. `test_replay` - INSERT row with `status='completed'`, response_body=`{...}`; second POST with same key returns byte-identical body + `X-Idempotency-Replay: 1` header
2. `test_body_mismatch_returns_422` - INSERT row with body_hash=`H1`; POST with key + body that hashes to `H2` returns 422 problem+json
3. `test_pending_returns_409` - INSERT row with `status='pending'`; POST with same key returns 409 problem+json

---

## `backend/tests/api_v1/test_pagination.py` + `test_cursor.py` (new)

**`test_cursor.py` analog:** `backend/tests/middleware/test_rate_limit.py:56-100` (pure-function unit tests, no HTTP):
```python
def test_rate_limit_key_with_jwt_cookie():
    user_id = "user-uuid-12345"
    token = _make_jwt(user_id)
    request = _make_request(cookies={"access_token": token})

    key = get_rate_limit_key(request)

    assert key == f"user:{user_id}"


def test_rate_limit_key_invalid_jwt():
    """A malformed JWT cookie falls back to 'ip:{client_host}'."""
    ...
```

**Adaptation:** Same shape, no HTTP, just functional assertions:
```python
from datetime import datetime, timezone
from api.v1.cursor import encode_cursor, decode_cursor


def test_round_trip():
    dt = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    id_ = "abc-123"
    token = encode_cursor(dt, id_)
    out_dt, out_id = decode_cursor(token)
    assert out_dt == dt and out_id == id_


def test_garbage_input():
    assert decode_cursor("not-base64-!!!") is None
    assert decode_cursor("") is None
    assert decode_cursor("YWJjMTIz") is None  # Valid base64 but not our JSON shape
```

---

## `backend/tests/api_v1/test_rate_limit.py` (new)

**Analog:** `backend/tests/middleware/test_rate_limit.py:1-86` (full file). Same shape; tests the `api_v1_limiter.key_func` (`get_api_key_id`) + the 60rpm budget header round-trip.

**Key cases per API-08/API-10:**
- `test_60rpm` - hit endpoint 61 times rapid-fire; the 61st returns 429 + `Retry-After: <seconds>` header
- `test_headers_on_200` - any 2xx response includes `X-RateLimit-Limit: 60`, `X-RateLimit-Remaining: <int>`, `X-RateLimit-Reset: <unix-epoch>`
- `test_headers_on_429` - 429 response includes all four headers (plus `Retry-After`)

---

## `backend/tests/api_v1/test_errors.py` (new)

**Analog:** Same multi-conn pool mock pattern from `backend/tests/jobs/test_cancel.py`.

**Key cases per API-07:**
- `test_problem_json` - a `raise HTTPException(404)` in an `/api/v1/*` route returns `Content-Type: application/problem+json` and body has `type`, `title`, `status`, `detail`, `instance` keys
- `test_validation_error_problem_json` - bad request body to POST /api/v1/jobs returns 422 problem+json with `errors[]` array
- regression: a `raise HTTPException(404)` in `/jobs/launch` (web flow) returns `Content-Type: application/json` with `{"detail": "..."}` shape (RESEARCH §2.7 fall-through path)

---

## `backend/tests/contract/test_openapi_contract.py` (new)

**Analog:** None. Verbatim from RESEARCH §2.6 lines 198-216:
```python
from main import app

SDK_CONTRACT_V1 = [
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

---

# WAVE 2 - SDK polish + frontend

## `bindwave-python/src/bindwave/_pagination.py` (new, auto-paginator)

**Analog:** None in this codebase. Anthropic SDK paginator pattern.

**Adaptation:** Generator function that loops calling `client.jobs.list(cursor=...)` until `next_cursor` is `None`:
```python
def iter_all(client, **filters):
    cursor = None
    while True:
        page = client.jobs.list(cursor=cursor, **filters)
        for item in page.data:
            yield item
        cursor = page.next_cursor
        if cursor is None:
            return
```

Plus async variant for `AsyncClient`.

---

## `frontend/src/pages/SettingsPage.tsx` (modify - add 6th tab)

**Analog:** Self, lines 461-555.

**Excerpt to mirror** (lines 466-472, 502-553):
```typescript
const VALID_SETTINGS_TABS = [
  "account",
  "billing",
  "privacy",
  "usage",
  "notifications",
] as const;

...

  return (
    <div className="max-w-[640px] mx-auto px-6 py-8">
      <h1 className="font-display text-[28px] font-semibold mb-6">Settings</h1>
      ...
      <Tabs defaultValue={initialTab}>
        <TabsList>
          <TabsTrigger value="account">Account</TabsTrigger>
          <TabsTrigger value="billing">Billing</TabsTrigger>
          <TabsTrigger value="privacy">Privacy</TabsTrigger>
          <TabsTrigger value="usage">Usage</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
        </TabsList>
        ...
        <TabsContent value="privacy">
          <PrivacyTab initialSettings={settings} onChanged={loadSettings} />
        </TabsContent>
        ...
      </Tabs>
```

**Adaptation per RESEARCH §5.12:**
- Insert `"api-keys"` into `VALID_SETTINGS_TABS` between `"privacy"` and `"usage"`
- Add `<TabsTrigger value="api-keys">API Keys</TabsTrigger>` in matching position
- Add corresponding `<TabsContent value="api-keys"><ApiKeysTab ... /></TabsContent>` block
- Import the new component: `import { ApiKeysTab } from "@/components/api-keys/ApiKeysTab"`

CRITICAL: count from the actual source (5 existing tabs), not the CONTEXT.md "5th tab" hint which was wrong (RESEARCH §5.12 confirms there are 5 existing - api-keys is the 6th).

---

## `frontend/src/components/api-keys/ApiKeysTab.tsx` (new)

**Analog:** `frontend/src/components/legal/PrivacyTab.tsx` (sibling tab) + the existing tab body shape in `SettingsPage.tsx:68-130::AccountTab`:
```typescript
interface AccountTabProps {
  initialSettings: UserSettings | null;
  onSaved: () => void;
}

function AccountTab({ initialSettings, onSaved }: AccountTabProps) {
  const [displayName, setDisplayName] = useState(initialSettings?.display_name ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState(false);
  ...

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await updateSettings({ display_name: displayName });
      ...
    } catch {
      setSaveError("Changes could not be saved. Check your connection and try again.");
    }
  }

  return (
    <div className="space-y-6 pt-4">
      ...
      <Button onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save changes"}
      </Button>
    </div>
  );
}
```

**Adaptation:** Same `useState`/`useCallback`/`useEffect` shape; key list with revoke modal; "Create new key" button opens `CreateApiKeyModal`; same `space-y-6 pt-4` layout class.

---

## `frontend/src/components/api-keys/CreateApiKeyModal.tsx` (new)

**Analog:** `frontend/src/components/ui/dialog.tsx` as the base primitive (already shadcn-style); modal usage patterns from `frontend/src/components/legal/ReAcceptanceModal.tsx`.

**Adaptation:** Plaintext-once display modal:
- 2-stage flow: stage 1 = name input + Create button; stage 2 = display plaintext with copy-to-clipboard + "I have saved this key" confirmation gate (CONTEXT.md `:235`)
- Cannot dismiss stage 2 without checking the "I have saved this key" checkbox (UX constraint per Phase 13 SC 3)
- Closes via `onClose()` callback only after confirmation

---

## `frontend/src/lib/api-keys.ts` (new, typed client)

**Analog:** `frontend/src/lib/user.ts:1-100`

**Excerpt to mirror** (lines 1-30, 67-83):
```typescript
import { api } from "./api";

export interface UserSettings {
  email: string;
  display_name: string;
  ...
}

export async function getSettings(): Promise<UserSettings> {
  return api<UserSettings>("/user/settings", { method: "GET" });
}

export async function updateSettings(data: {...}): Promise<void> {
  await api("/user/settings", { method: "PUT", body: data });
}
```

**Adaptation:**
```typescript
import { api } from "./api";

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;       // "bw_live_XXXX"
  role: string;
  created_at: string;
  last_used_at: string | null;
}

export interface CreatedApiKey extends ApiKey {
  plaintext: string;    // SHOWN ONCE
}

export async function listApiKeys(): Promise<ApiKey[]> {
  return api<ApiKey[]>("/user/api-keys", { method: "GET" });
}

export async function createApiKey(name: string): Promise<CreatedApiKey> {
  return api<CreatedApiKey>("/user/api-keys", { method: "POST", body: { name } });
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await api(`/user/api-keys/${keyId}/revoke`, { method: "POST" });
}
```

---

# WAVE 3 - Verification

## `backend/tests/contract/_sdk_contract_v0_1_0.py` (new)

**Analog:** None. RESEARCH §2.6 reference. Vendored frozen contract list.

**Shape:**
```python
"""Frozen SDK contract for bindwave-python 0.1.0.

This file is the source of truth for what endpoints the SDK calls.
The contract test in test_openapi_contract.py reads this list and asserts
the FastAPI OpenAPI spec covers every entry.

DO NOT EDIT without bumping bindwave-python version.
"""

SDK_CONTRACT_V0_1_0 = [
    {"method": "POST", "path": "/api/v1/jobs",
     "req_fields": ["tool", "parameters"], "resp_fields": ["id", "status"],
     "status": 201, "since": "0.1.0"},
    {"method": "GET", "path": "/api/v1/jobs/{job_id}",
     "req_fields": [], "resp_fields": ["id", "status", "candidates"],
     "status": 200, "since": "0.1.0"},
    {"method": "GET", "path": "/api/v1/jobs",
     "req_fields": [], "resp_fields": ["data", "next_cursor"],
     "status": 200, "since": "0.1.0"},
    {"method": "POST", "path": "/api/v1/jobs/{job_id}/cancel",
     "req_fields": [], "resp_fields": ["id", "status"],
     "status": 200, "since": "0.1.0"},
    {"method": "GET", "path": "/api/v1/api-keys",
     "req_fields": [], "resp_fields": ["data"],
     "status": 200, "since": "0.1.0"},
    {"method": "POST", "path": "/api/v1/api-keys/{key_id}/revoke",
     "req_fields": [], "resp_fields": ["id", "revoked_at"],
     "status": 200, "since": "0.1.0"},
]
```

---

# Shared Patterns

## Pattern S1 - asyncpg pool acquire context

**Source:** Everywhere in `backend/` - canonical examples at `backend/jobs/router.py:135-141`, `backend/auth/router.py:117-126`, `backend/jobs/service.py:62-68`:
```python
pool = await get_db_pool()
async with pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ... WHERE ...", arg1, arg2)
```

**Apply to:** Every backend file that touches Postgres. Co-transactional flows MUST `async with conn.transaction():` (RESEARCH §5.6 demand for idempotency + jobs co-write).

---

## Pattern S2 - HTTPException with named status

**Source:** Used everywhere. `backend/jobs/router.py:163-166`:
```python
raise HTTPException(
    status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
    detail="payment_required",
)
```

**Apply to:** All `/api/v1/*` raises. The RFC 7807 handler in `backend/api/v1/errors.py` converts these into problem+json automatically based on `request.url.path.startswith("/api/v1/")`.

---

## Pattern S3 - asyncio.to_thread for sync-API blocking calls

**Source:** `backend/jobs/notifications.py:54-63`:
```python
async def _send_email_safely(params, purpose: str) -> None:
    if not settings.resend_api_key:
        logger.info("Skipping %s email: RESEND_API_KEY not configured", purpose)
        return
    try:
        await asyncio.to_thread(resend.Emails.send, params)
    except Exception as exc:
        logger.warning("Failed to send %s email (to=%s): %s",
                       purpose, params.get("to"), exc)
```

**Apply to:** any sync HTTP/SDK call invoked from FastAPI handler. Specifically:
- Boto3 `generate_presigned_url` in `serialize.py` could block - wrap if measured as hot
- Stripe SDK calls (already wrapped elsewhere)

---

## Pattern S4 - slowapi `@limiter.limit("N/period")` decorator

**Source:** `backend/auth/router.py:82`, `backend/jobs/router.py:104`:
```python
@router.post("/launch")
@limiter.limit("5/minute")
async def launch_job_endpoint(request: Request, ...):
```

**Apply to:** Every endpoint in `backend/api/v1/jobs.py` and `backend/api/v1/api_keys.py`:
```python
@router.post("/")
@api_v1_limiter.limit(settings.api_v1_rate_limit)
async def submit_job(request: Request, ...):
```

Note: the `request: Request` positional arg is required by slowapi to extract the key (RESEARCH §2.4 + `backend/auth/router.py:278` comment).

---

## Pattern S5 - app-level exception handler registration

**Source:** `backend/middleware/rate_limit.py:74`:
```python
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**Apply to:** `backend/main.py` after `setup_rate_limiting(app)` - register the two RFC 7807 handlers from `api/v1/errors.py`. This is the only registration site - sub-routers cannot have their own exception handlers per RESEARCH §2.7.

---

## Pattern S6 - dual-secret rotation grace window

**Source:** `backend/webhooks/router.py:83-94` + `backend/config.py:94-95`:
```python
for label, secret in (("current", current_secret), ("prev", prev_secret)):
    if not secret:
        continue
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, signature):
        if label == "prev":
            logger.warning("Webhook signed with PREV secret - rotation window active")
        return label
```

**Apply to:** `backend/auth/api_keys.py::verify_api_key` - same shape; if current pepper fails, try prev; log WARNING on prev match. Pepper rotation runbook extends the existing webhook rotation doc per RESEARCH Open Q2.

---

## Pattern S7 - Pydantic Settings env-loaded

**Source:** `backend/config.py:7-167`. Settings class with `Config: env_file=("../.env.local", ".env.local")`. Add new fields as top-level attrs with type hint + default.

**Apply to:** New settings:
- `api_key_pepper: str = ""`
- `api_key_pepper_prev: str = ""`
- `api_v1_rate_limit: str = "60/minute"`
- `idempotency_ttl_hours: int = 25`

---

## Pattern S8 - migration COMMENT ON COLUMN

**Source:** `supabase/migrations/20260420000001_job_tier_and_budget.sql:33-40`:
```sql
COMMENT ON COLUMN public.jobs.job_tier IS
    'Job tier: pilot (validation run) or full_design (real campaign). See Modal migration plan.';
```

**Apply to:** Every new column in `20260607000001_api_keys.sql` and `20260607000002_api_key_idempotency.sql`. Specifically include a long COMMENT on `bcrypt_hash` documenting it actually holds HMAC-SHA256 (RESEARCH §2.10 lines 393-397).

---

## Pattern S9 - pytest dependency_overrides + httpx ASGITransport

**Source:** `backend/tests/jobs/test_cancel.py:100-117`:
```python
app.dependency_overrides[get_current_user] = _override_user("user-abc")
try:
    with (
        patch("jobs.router.get_db_pool", return_value=router_pool),
        ...
    ):
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/jobs/job-cancel-test/cancel",
                cookies={"access_token": "fake-token"},
            )
finally:
    app.dependency_overrides.pop(get_current_user, None)
```

**Apply to:** Every integration test in `backend/tests/api_v1/`. Swap `get_current_user` -> `get_current_api_key`. Use `headers={"Authorization": "Bearer bw_test_xxx"}` instead of `cookies=`.

---

# No Analog Found

| File | Role | Reason | Reference |
|---|---|---|---|
| `backend/api/v1/idempotency.py` | DB-backed 3-state store | No existing idempotency machinery in codebase | RESEARCH §2.9 (Brandur + Stripe pattern) |
| `backend/tests/contract/test_openapi_snapshot.py` | snapshot test | No existing snapshot tests in backend | RESEARCH §2.12 |
| `backend/tests/contract/test_openapi_contract.py` | contract test | No existing contract tests | RESEARCH §2.6 |
| `bindwave-python/pyproject.toml` | SDK build config | Separate repo, new ecosystem | RESEARCH §2.5 (Anthropic SDK) |
| `bindwave-python/src/bindwave/_client.py` | HTTP client base | Separate repo | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_async_client.py` | async HTTP client | Separate repo | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_pagination.py` | cursor iterator | Separate repo | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_exceptions.py` | exception hierarchy | Separate repo | RESEARCH §2.5 |
| `bindwave-python/src/bindwave/_idempotency.py` | uuid4 helper | Separate repo, trivial | RESEARCH §2.5 |
| `bindwave-python/.github/workflows/ci.yml` | CI | Separate repo | RESEARCH §2.5 |
| `bindwave-python/.github/workflows/release.yml` | release | Separate repo | RESEARCH §2.5 |
| `bindwave-python/README.md` | doc | Separate repo, 15-line quickstart per D-09 | RESEARCH §2.5 |
| `bindwave-python/CHANGELOG.md` | doc | Separate repo, Keep-a-Changelog | RESEARCH §2.5 |

---

# Metadata

**Analog search scope:** `backend/` (all subdirs), `frontend/src/`, `supabase/migrations/`, `backend/tests/` (existing 30+ test files).

**Files scanned:** 27 backend files read (auth/, jobs/, middleware/, webhooks/, user/, storage/, config, main, conftest), 4 frontend files (SettingsPage.tsx, user.ts, api.ts, SettingsPage tab area), 3 migrations (init, legal_compliance, job_tier_and_budget), 5 test files.

**Pattern extraction date:** 2026-06-04

**Key observation:** Phase 13 is unusually well-anchored - 32 of 34 files have a same-shape analog already in the codebase. The two genuinely new patterns (RFC 7807 problem+json error handler + Postgres 3-state idempotency lifecycle) are well-documented industry patterns with RESEARCH-cited references. The bindwave-python SDK is the largest "no analog" surface but follows Anthropic SDK layout verbatim per D-12.

**Phase 12 dependency warning:** Several adaptations assume Phase 12 organizations + `organization_id` columns are landed (RESEARCH §5.11, §5.14). If Phase 13 runs before Phase 12 fully cuts over, the org-scoped SELECTs in Wave 1 plans will fail. Planner MUST add a pre-flight checklist to PLAN 13-01 confirming `settings.organizations_enabled=True` and the drop-column migration has fired.

---

## PATTERN MAPPING COMPLETE

**Phase:** 13 - public-api
**Files classified:** 34 (28 new, 6 modified)
**Analogs found:** 32 / 34 (94%)

### Coverage
- Files with exact analog: 26
- Files with role-match analog: 6
- Files with no analog (industry-pattern references only): 2 backend + 12 SDK = 14
  (Note: 12 of the 14 "no analog" files are the new `bindwave-python` repo, which mirrors Anthropic SDK layout verbatim per RESEARCH §2.5)

### Key Patterns Identified
- All `/api/v1/*` endpoints mirror `backend/jobs/router.py` shape; swap `Depends(get_current_user)` -> `Depends(require_role_api(...))` and `@limiter.limit(...)` -> `@api_v1_limiter.limit(settings.api_v1_rate_limit)`
- HMAC-SHA256 with pepper + dual-secret rotation mirrors the existing webhook signature pattern (`backend/webhooks/router.py:51-94`) verbatim
- The `INSERT ... ON CONFLICT DO NOTHING` idempotency claim pattern has a partial precedent in `backend/auth/router.py:128-141` (signup race handling)
- Migration shape (CREATE TABLE + partial indexes + COMMENT ON COLUMN) is well-established at `supabase/migrations/20260420000001_job_tier_and_budget.sql`
- Test scaffolding fully mirrors `backend/tests/jobs/test_cancel.py` (dependency_overrides + ASGITransport + multi-acquire pool mocks)
- RFC 7807 problem+json handler is the one "build from spec" pattern - registered app-level with `request.url.path.startswith("/api/v1/")` gate per RESEARCH §2.7

### File Created
`C:\Users\lab\Documents\Claude_projects\llm-proteinDesigner\.planning\phases\13-public-api\13-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. Planner can reference analog patterns by file:line in PLAN-13-01..07.md actions.
