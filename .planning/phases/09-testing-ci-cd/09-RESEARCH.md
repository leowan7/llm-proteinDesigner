# Phase 9: Testing & CI/CD - Research

**Researched:** 2026-04-10
**Domain:** pytest / pytest-cov / Vitest / Playwright / GitHub Actions
**Confidence:** HIGH

## Summary

The existing backend test infrastructure is more mature than it may appear from the module list. Twenty-plus test files across 7 subdirectories (agent, billing, jobs, admin, gpu, pdb, and one top-level auth file) already establish consistent patterns: `app.dependency_overrides` for FastAPI DI mocking, `respx` for HTTP mocking, `AsyncMock`/`MagicMock` for DB pool mocking, and `asyncio_mode = auto` in pytest.ini so every async test just works. The frontend has exactly one test file (`api.test.ts`). Playwright is already installed (`v1.59.1`) and vitest is at `v4.1.4`. The five existing GitHub Actions workflows (Docker builds) provide a solid structural template.

The four coverage gaps identified in D-04 map to concrete, untested modules: `sessions/router.py` (6 endpoints), `user/router.py` (3 endpoints), `webhooks/router.py` (the RunPod completion handler + heartbeat), `middleware/rate_limit.py` and `middleware/logging.py`, and `worker/tasks.py` + `worker/cleanup.py`. All follow patterns already established in the codebase — adding tests for them is a mechanical application of existing conventions.

`pytest-cov` is not yet in `requirements.txt` and must be added. The `supabase` CLI is not in PATH on this machine, so CI integration tests need the `supabase/cli` GitHub Action rather than a pre-installed binary.

**Primary recommendation:** Extend the existing test suite with the five gap modules, add `pytest-cov` to requirements, configure a single `.github/workflows/test.yml`, and set up Playwright with a `playwright.config.ts` using a `baseURL` pointed at the local dev server.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Backend Test Coverage**
- D-01: Coverage target >80% line coverage across all backend modules (agent, billing, jobs, PDB, auth, admin, analysis, webhooks, middleware).
- D-02: Integration tests hit a real Supabase test instance in CI — no mocked databases for integration tests. Unit tests may use mocks for external services (Stripe, RunPod, Anthropic).
- D-03: pytest is the test runner (already configured with asyncio_mode = auto). Existing ~20 test files across 7 modules form the baseline.
- D-04: Coverage gaps to fill: sessions module (Phase 6), settings/usage endpoints (Phase 6), admin endpoints (Phase 7), analysis tools (Phase 8 — some tests exist), webhooks, middleware (rate limiting, logging), worker tasks (cleanup, stale detection).

**Frontend Testing**
- D-05: Vitest for frontend unit tests. Native Vite integration, same config as build.
- D-06: Playwright for E2E tests covering 4 core flows: Login+auth, Chat+job launch, Job status+results, Settings+billing.
- D-07: Frontend unit tests cover: API client functions, utility functions, component rendering (smoke tests for ChatPage, JobPage, SettingsPage).

**CI Pipeline Design**
- D-08: GitHub Actions on every PR to main. Four gates: backend tests (pytest with real DB), frontend tests (Vitest + Playwright E2E), lint+type check (eslint jsx-a11y, tsc --noEmit, ruff), coverage threshold (backend <80% fails).
- D-09: Docker image builds run in CI via GitHub Actions, push to ghcr.io on merge to main. Existing 5 Docker workflow YAMLs are the starting point.
- D-10: CI needs GitHub Secrets: SUPABASE_URL and SUPABASE_SERVICE_KEY for the test env.

**Pre-Deploy Smoke Test**
- D-11: Post-deploy smoke test checks: health endpoint 200, auth flow works, frontend loads, API response <2s.
- D-12: Manual rollback — alert via Sentry, human decides via Railway/Vercel deploy history. No automated rollback.
- D-13: Separate GitHub Actions workflow triggered after deploy completes (workflow_run or manual dispatch).

### Claude's Discretion
- Exact pytest fixture structure for real DB integration tests
- Vitest config details and test file organization
- Playwright test helpers and page object patterns
- GitHub Actions caching strategy (pip, node_modules, Playwright browsers)
- Coverage report format and where to publish (PR comment vs artifact)

### Deferred Ideas (OUT OF SCOPE)
- Load testing
- Visual regression testing
- Contract testing
</user_constraints>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 8.3.5 (pinned) | Backend test runner | Already installed and configured |
| pytest-asyncio | 0.24.0 (pinned) | Async test support | Already installed, asyncio_mode=auto |
| pytest-cov | 6.1.0 | Coverage measurement + enforcement | Missing from requirements.txt — must add |
| httpx | 0.28.1 (pinned) | FastAPI async test client | Already installed, used in conftest.py |
| respx | 0.22.0 (pinned) | HTTP mock for httpx | Already installed, used in agent tests |
| vitest | 4.1.4 | Frontend unit tests | Already in package.json devDependencies |
| @testing-library/react | 16.3.2 | Component rendering tests | Already in devDependencies |
| @playwright/test | 1.59.1 | E2E browser automation | Already installed (confirmed via npx) |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| @vitest/coverage-v8 | latest | Frontend coverage via V8 | Add with `vitest run --coverage` |
| @testing-library/jest-dom | 6.9.1 | DOM assertion matchers | Already in devDependencies |
| ruff | existing | Python linter | Already used in the project |
| supabase/cli GitHub Action | latest | Supabase in CI without local CLI | CI only — CLI not in PATH locally |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pytest-cov | coverage.py directly | pytest-cov integrates with pytest flags; coverage.py requires separate run step |
| @playwright/test page objects | Playwright fixtures only | Page objects improve maintainability for 4+ flows but add upfront setup cost |
| Supabase local CLI in CI | Hosted test project | Local CLI is free, reproducible, and avoids managing a persistent test Supabase project |

**Installation (additions only — existing packages already pinned):**
```bash
# Backend — add to requirements.txt
pytest-cov==6.1.0

# Frontend — add to package.json devDependencies
npm install -D @vitest/coverage-v8 @playwright/test
npx playwright install --with-deps chromium
```

**Version verification:** [VERIFIED: npm registry] `@playwright/test@1.59.1` confirmed. [VERIFIED: npm registry] `vitest@4.1.4` confirmed. [ASSUMED] `pytest-cov==6.1.0` is the current release — verify with `pip index versions pytest-cov` before pinning.

---

## Architecture Patterns

### Recommended Project Structure
```
backend/
├── tests/
│   ├── conftest.py          # Shared fixtures (existing — extend for DB integration)
│   ├── fixtures/            # PDB files, JSON payloads (existing)
│   ├── agent/               # Existing
│   ├── billing/             # Existing
│   ├── jobs/                # Existing
│   ├── admin/               # Existing
│   ├── gpu/                 # Existing
│   ├── pdb/                 # Existing
│   ├── sessions/            # NEW — test_router.py, test_queries.py
│   ├── user/                # NEW — test_router.py
│   ├── webhooks/            # NEW — test_router.py
│   ├── middleware/          # NEW — test_rate_limit.py, test_logging.py
│   └── worker/              # NEW — test_tasks.py, test_cleanup.py
│
frontend/
├── src/
│   ├── lib/
│   │   └── api.test.ts      # Existing — extend with more API client tests
│   ├── components/
│   │   └── *.test.tsx       # NEW — smoke tests per component
│   └── pages/
│       └── *.test.tsx       # NEW — smoke tests for ChatPage, JobPage, SettingsPage
├── e2e/                     # NEW — Playwright tests
│   ├── auth.spec.ts
│   ├── chat.spec.ts
│   ├── jobs.spec.ts
│   └── billing.spec.ts
└── playwright.config.ts     # NEW

.github/
└── workflows/
    ├── test.yml             # NEW — PR gate (all 4 checks)
    ├── smoke.yml            # NEW — post-deploy smoke test
    └── docker-*.yml         # Existing (5 files)
```

### Pattern 1: Backend Unit Test with Mocked DB Pool
**What:** Override FastAPI DI to inject a mock asyncpg pool; test router logic without real DB.
**When to use:** All endpoint tests for sessions, user, webhooks, middleware — wherever a real DB is unnecessary for the specific assertion.

```python
# Source: established pattern in backend/tests/admin/test_router.py
import os
os.environ.setdefault("TESTING", "true")

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

# Disable rate limiting (no Redis in unit tests)
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False

def _make_ctx(conn):
    """Wrap mock asyncpg connection in an async context manager."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

### Pattern 2: Backend Integration Test with Real Supabase
**What:** Use the real Supabase test instance (local or CI-hosted). Seed via the existing `test@example.com` / `Password123!` account.
**When to use:** Auth flow tests, session CRUD tests where the DB schema matters.

```python
# Source: established pattern in backend/tests/conftest.py
# conftest.py already loads .env.local and sets SUPABASE_URL to 127.0.0.1:54321
# For CI, SUPABASE_URL and SUPABASE_SERVICE_KEY are GitHub Secrets

# Integration test: no dependency_overrides, real DB pool
@pytest.mark.asyncio
async def test_create_session_persists(client):
    # login first to get auth cookie
    resp = await client.post("/auth/login", json={
        "email": "test@example.com",
        "password": "Password123!"
    })
    assert resp.status_code == 200
    # now hit the real endpoint
    resp = await client.post("/sessions", json={"title": "Test session"})
    assert resp.status_code == 201
```

### Pattern 3: Vitest Component Smoke Test
**What:** Render a component with `@testing-library/react`, assert it doesn't crash and renders key elements.
**When to use:** ChatPage, JobPage, SettingsPage — verify render without testing every interaction.

```typescript
// Source: [ASSUMED] — standard @testing-library/react pattern
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect } from "vitest";
import SettingsPage from "../pages/SettingsPage";

describe("SettingsPage", () => {
  it("renders without crashing", () => {
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    // Assert a landmark element is present
    expect(screen.getByRole("main")).toBeInTheDocument();
  });
});
```

### Pattern 4: Playwright E2E with Page Objects
**What:** Encapsulate page-specific selectors and actions in a class. Keeps spec files readable.
**When to use:** All 4 E2E flows. Each flow gets a lean page object.

```typescript
// Source: [CITED: playwright.dev/docs/pom]
// e2e/pages/LoginPage.ts
import { Page } from "@playwright/test";

export class LoginPage {
  constructor(private page: Page) {}

  async login(email: string, password: string) {
    await this.page.goto("/login");
    await this.page.fill('[name="email"]', email);
    await this.page.fill('[name="password"]', password);
    await this.page.click('button[type="submit"]');
    await this.page.waitForURL("/");
  }
}
```

### Pattern 5: GitHub Actions Test Workflow
**What:** Single `test.yml` with four jobs — backend, frontend-unit, e2e, lint. All must pass to merge.
**When to use:** Every PR to main.

```yaml
# Source: [ASSUMED] — standard GitHub Actions pattern + existing docker-*.yml templates
name: Test Suite
on:
  pull_request:
    branches: [main, master]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      # Supabase via CLI action — no local binary needed
    steps:
      - uses: actions/checkout@v4
      - uses: supabase/setup-cli@v1
        with:
          version: latest
      - run: supabase start
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: pip-${{ hashFiles('backend/requirements.txt') }}
      - run: pip install -r backend/requirements.txt
      - run: pytest backend/tests/ --cov=backend --cov-fail-under=80 --cov-report=xml
        working-directory: backend

  frontend-unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - uses: actions/cache@v4
        with:
          path: frontend/node_modules
          key: node-${{ hashFiles('frontend/package-lock.json') }}
      - run: npm ci
        working-directory: frontend
      - run: npm run test
        working-directory: frontend

  e2e:
    runs-on: ubuntu-latest
    needs: [backend, frontend-unit]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: npm ci && npx playwright install --with-deps chromium
        working-directory: frontend
      - uses: supabase/setup-cli@v1
        with: { version: latest }
      - run: supabase start
      # Start backend and frontend, then run Playwright
      - run: |
          cd backend && uvicorn main:app --port 8000 &
          cd frontend && npm run dev &
          npx playwright test
        working-directory: frontend

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install ruff && ruff check backend/
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: npm ci && npx tsc --noEmit && npx eslint .
        working-directory: frontend
```

### Pattern 6: Playwright Config
```typescript
// Source: [CITED: playwright.dev/docs/test-configuration]
// frontend/playwright.config.ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
});
```

### Anti-Patterns to Avoid

- **Shared iterator side_effect between router and worker mocks:** The existing codebase (Phase 3 decision) explicitly notes that router and worker DB pool mocks must be separate objects. A single `side_effect` list consumed by two callers raises `StopIteration`.
- **Patching `get_db_pool` at the wrong import path:** Always patch at the router's import path (`admin.router.get_db_pool`), not at the definition site (`db.connection.get_db_pool`). Otherwise the override has no effect.
- **Starting middleware before setting `TESTING=true`:** `os.environ["TESTING"] = "true"` must be set before importing `main.py`; CSRF middleware registration happens at import time.
- **Rate limiter connecting to Redis in unit tests:** The limiter attempts Redis connection on every request. Disable with `limiter.enabled = False` at the top of each test file that doesn't need rate limiting asserted.
- **Using `EventSource` in Playwright E2E for SSE:** SSE endpoint requires POST body; use `page.route()` interception or a dedicated SSE mock rather than asserting real streaming in Playwright (slow + flaky).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Coverage threshold enforcement | Custom coverage check script | `pytest --cov-fail-under=80` | Built into pytest-cov; returns exit code 2 on failure |
| GitHub Actions caching | Custom cache logic | `actions/cache@v4` with hash key on requirements.txt / package-lock.json | Standard, handles invalidation correctly |
| Supabase in CI | Custom Postgres + Supabase API mocks | `supabase/setup-cli@v1` action + `supabase start` | Provisions the real Supabase stack (auth, DB, realtime) in ~30s |
| Playwright browser process management | `concurrently` / custom shell scripts | `webServer` in `playwright.config.ts` | Playwright starts the dev server, waits for readiness, tears it down automatically |
| Coverage PR comments | Custom GitHub API calls | `codecov/codecov-action` or GitHub Actions artifact upload | Artifacts require zero tokens; Codecov free tier adds PR annotations |

**Key insight:** The test infrastructure tooling in this ecosystem is well-standardized. Every "I'll write a script for that" impulse should be redirected to check if `pytest-cov`, `playwright`, or a GitHub Actions action already handles it.

---

## Common Pitfalls

### Pitfall 1: supabase CLI Not in PATH
**What goes wrong:** `supabase start` fails in CI with "command not found" when CI runner doesn't have Supabase CLI pre-installed.
**Why it happens:** Supabase CLI is not a standard GitHub Actions runner package. It is locally confirmed not in PATH on this machine.
**How to avoid:** Always use `supabase/setup-cli@v1` action before any `supabase` command in CI. Locally, developers run `supabase start` manually before integration tests.
**Warning signs:** `ModuleNotFoundError` for Supabase-connected test fixtures; 127 exit code on `supabase` commands.

### Pitfall 2: pytest-cov Not Installed
**What goes wrong:** `pytest --cov` raises `ModuleNotFoundError: No module named 'pytest_cov'`.
**Why it happens:** `pytest-cov` is NOT in `backend/requirements.txt`. Confirmed by grepping requirements.txt.
**How to avoid:** Add `pytest-cov==6.1.0` to requirements.txt in Wave 0.
**Warning signs:** CI job fails immediately on the coverage flag before any tests run.

### Pitfall 3: asyncpg Pool Acquired Twice in One Request Path
**What goes wrong:** Tests for the webhooks router or worker tasks fail with `StopIteration` or `MagicMock` not callable errors when the DB pool is acquired in multiple places (e.g., webhook handler calls both `update_job_status` and `record_gpu_usage`).
**Why it happens:** The `side_effect` list on a single `MagicMock` is consumed sequentially. If the code acquires the pool twice, the mock runs out of responses.
**How to avoid:** Create separate `MagicMock` objects for each DB acquisition in the request path, or use `return_value` with a single connection mock that returns predictable responses on each `.fetchrow()` call.
**Warning signs:** Tests pass individually but fail when run together; `StopIteration` in test output.

### Pitfall 4: Playwright E2E Flakiness on SSE/Real-time Features
**What goes wrong:** E2E tests for job status updates (SSE) are flaky because `page.waitForSelector()` races with the server-sent event stream.
**Why it happens:** SSE delivery timing is non-deterministic in a test environment. Playwright's default timeout (30s) is often sufficient but retries inflate CI time.
**How to avoid:** Use `page.waitForResponse()` to assert the SSE connection is established, then poll for the UI state change with `expect(locator).toBeVisible({ timeout: 10000 })`. Set `retries: 2` in `playwright.config.ts` for CI.
**Warning signs:** Tests pass locally but fail intermittently in CI; test duration variance > 2x.

### Pitfall 5: Vitest jsdom Missing CSS / Tailwind
**What goes wrong:** Component tests fail with `TypeError: Cannot read properties of undefined` when a component uses CSS custom properties injected by Tailwind.
**Why it happens:** jsdom does not process CSS. Tailwind v4 injects variables via `@theme inline` which jsdom ignores.
**How to avoid:** Component smoke tests should only assert structure (elements present, text content), never assert computed CSS values. For styled interaction tests, use Playwright instead.
**Warning signs:** `getComputedStyle` returning empty strings; snapshot tests capturing un-styled output.

### Pitfall 6: Coverage Counting Compiled/Generated Files
**What goes wrong:** Coverage report counts auto-generated or vendored files, inflating or deflating coverage numbers.
**Why it happens:** pytest-cov scans all Python files under the `--cov` source path unless explicitly excluded.
**How to avoid:** Add a `.coveragerc` or `[tool.coverage]` in `pytest.ini` to omit `__pycache__`, `tests/`, and any generated files. Recommended omit pattern: `*/tests/*,*/__pycache__/*`.

---

## Code Examples

### pytest-cov Configuration in pytest.ini
```ini
; Source: [CITED: pytest-cov.readthedocs.io/en/latest/config.html]
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
addopts = --cov=. --cov-report=term-missing --cov-fail-under=80
[coverage:run]
omit =
    tests/*
    */migrations/*
    conftest.py
[coverage:report]
exclude_lines =
    pragma: no cover
    if __name__ == .__main__.:
```

### Webhook Router Test Pattern
```python
# Source: [ASSUMED] — follows admin/test_router.py pattern
import hashlib, hmac, json, os, time
os.environ.setdefault("TESTING", "true")

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport
from main import app
from middleware.rate_limit import limiter as _limiter
_limiter.enabled = False

WEBHOOK_SECRET = "test-secret"

def _make_signature(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_webhook_completed_job(client, monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_SECRET", WEBHOOK_SECRET)
    payload = {
        "id": "job-uuid-1234",
        "pod_id": "pod-abc",
        "status": "COMPLETED",
        "output": {"candidates": [], "count": 0},
        "timestamp": str(int(time.time()))
    }
    body = json.dumps(payload).encode()
    sig = _make_signature(body)
    with patch("webhooks.router.get_db_pool") as mock_pool, \
         patch("webhooks.router.record_gpu_usage") as mock_billing, \
         patch("webhooks.router.RunPodProvider") as mock_runpod:
        # ... configure mocks and assert
        resp = await client.post(
            "/webhooks/runpod",
            content=body,
            headers={"X-Signature": sig, "Content-Type": "application/json"}
        )
    assert resp.status_code == 200
```

### Playwright Auth Flow
```typescript
// Source: [CITED: playwright.dev/docs/writing-tests]
// e2e/auth.spec.ts
import { test, expect } from "@playwright/test";
import { LoginPage } from "./pages/LoginPage";

test.describe("Auth flow", () => {
  test("login persists across refresh", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.login("test@example.com", "Password123!");
    await expect(page).toHaveURL("/");
    await page.reload();
    await expect(page).toHaveURL("/");
    // Session persisted — not redirected to /login
  });
});
```

### GitHub Actions Caching for Playwright Browsers
```yaml
# Source: [CITED: playwright.dev/docs/ci#caching-browsers]
- name: Cache Playwright browsers
  uses: actions/cache@v4
  with:
    path: ~/.cache/ms-playwright
    key: playwright-${{ runner.os }}-${{ hashFiles('frontend/package-lock.json') }}
- name: Install Playwright browsers
  run: npx playwright install --with-deps chromium
  working-directory: frontend
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pytest-asyncio` with explicit `@pytest.mark.asyncio` on every test | `asyncio_mode = auto` in pytest.ini | pytest-asyncio 0.21+ | Already configured — no marks needed |
| `aiohttp.TestClient` | `httpx.AsyncClient(transport=ASGITransport(...))` | FastAPI recommendation shift | Already used in conftest.py |
| `jest` for React unit tests | `vitest` | 2023 — Vite-native testing | Already in package.json |
| `selenium` for E2E | `playwright` | 2021-2022 | Already installed at 1.59.1 |
| Codecov for coverage | GitHub Actions artifacts + `--cov-report=xml` | — | Artifacts require no token; Codecov is optional enhancement |

**Deprecated/outdated:**
- `anyio_backend` fixture: Present in current conftest.py but not strictly required with `asyncio_mode = auto`. Keep for backward compatibility.
- `pytest-anyio` marks: Some tests use `@pytest.mark.anyio` (visible in `test_session.py`). This is compatible with current config but new tests should use plain `async def test_...` without a mark since `asyncio_mode = auto` handles it.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | pytest-cov 6.1.0 is the current stable release | Standard Stack | Wrong version pinned; use `pip index versions pytest-cov` to verify |
| A2 | `supabase/setup-cli@v1` GitHub Action provisions a full local Supabase stack suitable for integration tests | Architecture Patterns | If it only installs the CLI binary (not starts the stack), an explicit `supabase start` step is still required — likely fine |
| A3 | The E2E test account `test@example.com` / `Password123!` will be available in the CI Supabase instance | Architecture Patterns | If the test instance isn't seeded, E2E auth tests fail; seed.sql must be run as part of CI setup |
| A4 | `@vitest/coverage-v8` works with Vite 8.0 and vitest 4.1 without additional config | Standard Stack | V8 coverage is the default provider for vitest >=1.0; should be fine |

---

## Open Questions

1. **Supabase test instance in CI — local CLI or hosted project?**
   - What we know: D-10 says "configure via GitHub Secrets (SUPABASE_URL, SUPABASE_SERVICE_KEY for test env)" which implies a hosted test project could also be used
   - What's unclear: Whether to spin up `supabase start` per CI run (free, isolated, slower ~30s setup) or point at a persistent Supabase test project (faster, but requires managing test data)
   - Recommendation: Use `supabase/setup-cli@v1` + `supabase start` for isolation. The SUPABASE_URL secret then overrides the default `127.0.0.1:54321` in conftest.py. This matches D-02 intent ("real Supabase test instance").

2. **Coverage threshold scope — per module or aggregate?**
   - What we know: D-01 says ">80% line coverage across all backend modules" which could mean per-module or aggregate
   - What's unclear: `--cov-fail-under=80` enforces the aggregate; a single undertested module won't fail CI if others compensate
   - Recommendation: Enforce aggregate threshold (80%) via `--cov-fail-under`. Do not enforce per-module in CI — add this as a follow-up if specific modules are found to drag coverage down.

3. **E2E test user credentials in CI**
   - What we know: The test account `test@example.com` is seeded via `seed.sql` for local dev
   - What's unclear: Whether a `seed.sql` file exists in the supabase directory and whether `supabase/setup-cli` runs it automatically
   - Recommendation: Wave 0 task should verify `supabase/seed.sql` exists and add it if missing.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| pytest | Backend tests | Yes | 8.3.5 (pinned) | — |
| pytest-asyncio | Backend async tests | Yes | 0.24.0 (pinned) | — |
| pytest-cov | Coverage enforcement | No | — | Add to requirements.txt |
| httpx | Test client | Yes | 0.28.1 (pinned) | — |
| respx | HTTP mocking | Yes | 0.22.0 (pinned) | — |
| vitest | Frontend unit tests | Yes | 4.1.4 | — |
| @playwright/test | E2E tests | Yes | 1.59.1 | — |
| supabase CLI | Integration tests locally | No (not in PATH) | — | Use `supabase/setup-cli@v1` in CI; run manually locally |
| Node.js | Frontend tooling | Yes | (npx confirmed) | — |
| @vitest/coverage-v8 | Frontend coverage | Not confirmed | — | Add to devDependencies |

**Missing dependencies with no fallback:**
- `pytest-cov` — required for D-08 coverage gate; blocks CI setup until added

**Missing dependencies with fallback:**
- Supabase CLI locally — developers must run `supabase start` manually; CI uses GitHub Action

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework (backend) | pytest 8.3.5 + pytest-asyncio 0.24.0 |
| Framework (frontend unit) | vitest 4.1.4 |
| Framework (E2E) | @playwright/test 1.59.1 |
| Config file (backend) | `backend/pytest.ini` |
| Config file (frontend) | `frontend/vite.config.ts` (test block already present) |
| Config file (E2E) | `frontend/playwright.config.ts` (Wave 0 — must create) |
| Quick run command (backend) | `cd backend && pytest tests/ -x -q` |
| Full suite command (backend) | `cd backend && pytest tests/ --cov=. --cov-fail-under=80` |
| Quick run command (frontend) | `cd frontend && npm run test` |
| Full suite command (E2E) | `cd frontend && npx playwright test` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| D-01 | >80% line coverage | coverage | `pytest --cov-fail-under=80` | Wave 0 (config) |
| D-02 | Integration tests hit real Supabase | integration | `pytest tests/sessions/ tests/user/` | Wave 0 (new files) |
| D-03 | pytest baseline runs | unit | `pytest tests/ -x` | Yes — existing 20 files |
| D-04 sessions | Sessions CRUD endpoints | unit | `pytest tests/sessions/` | Wave 0 |
| D-04 user | Settings/usage endpoints | unit | `pytest tests/user/` | Wave 0 |
| D-04 webhooks | RunPod webhook handler | unit | `pytest tests/webhooks/` | Wave 0 |
| D-04 middleware | Rate limit + logging | unit | `pytest tests/middleware/` | Wave 0 |
| D-04 worker | Cleanup + stale detection | unit | `pytest tests/worker/` | Wave 0 |
| D-05 | Vitest unit tests run | unit | `npm run test` | Partial (api.test.ts) |
| D-06 | Playwright E2E 4 flows | e2e | `npx playwright test` | Wave 0 |
| D-07 | Component smoke tests | unit | `npm run test` | Wave 0 |
| D-08 CI | All gates pass on PR | CI | GitHub Actions | Wave 0 |
| D-11 smoke | Post-deploy health check | smoke | `.github/workflows/smoke.yml` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q` (backend) or `npm run test` (frontend)
- **Per wave merge:** Full suite with coverage: `pytest tests/ --cov=. --cov-fail-under=80`
- **Phase gate:** Full suite green + Playwright E2E passing before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `backend/requirements.txt` — add `pytest-cov==6.1.0`
- [ ] `backend/pytest.ini` — add coverage configuration
- [ ] `frontend/playwright.config.ts` — Playwright config with baseURL and webServer
- [ ] `frontend/e2e/` — directory + placeholder spec files
- [ ] `supabase/seed.sql` — verify test user exists for E2E auth
- [ ] `backend/tests/sessions/__init__.py` + `test_router.py` — sessions coverage
- [ ] `backend/tests/user/__init__.py` + `test_router.py` — user settings/usage coverage
- [ ] `backend/tests/webhooks/__init__.py` + `test_router.py` — webhook handler coverage
- [ ] `backend/tests/middleware/__init__.py` + `test_rate_limit.py` + `test_logging.py` — middleware coverage
- [ ] `backend/tests/worker/__init__.py` + `test_tasks.py` + `test_cleanup.py` — worker coverage
- [ ] `.github/workflows/test.yml` — PR gate workflow
- [ ] `.github/workflows/smoke.yml` — post-deploy smoke test workflow

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Tests assert auth dependency blocks unauthenticated access to all protected endpoints |
| V3 Session Management | yes | Tests verify session isolation (different user_ids cannot read each other's sessions) |
| V4 Access Control | yes | Tests verify admin endpoints require admin role; user endpoints check ownership |
| V5 Input Validation | yes | Webhook tests verify HMAC signature rejection for tampered payloads |
| V6 Cryptography | no | No new crypto code — existing PyJWT and HMAC patterns are tested indirectly |

### Known Threat Patterns for Test Suite

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Webhook replay attack | Spoofing | Test that payloads with timestamps older than 5 min are rejected (D-10, Phase 5 decision) |
| Privilege escalation via admin endpoints | Elevation of Privilege | Test that non-admin users receive 403 from `/admin/*` endpoints |
| Session ID enumeration | Information Disclosure | Test that accessing another user's session returns 404 (not 403 — avoids confirming existence) |
| Coverage inflation via mocks | — | Integration tests (D-02) must not mock DB — use real Supabase to catch schema drift |

---

## Sources

### Primary (HIGH confidence)
- Existing codebase — `backend/tests/` (20+ files), `backend/conftest.py`, `backend/pytest.ini`, `frontend/vite.config.ts`, `frontend/package.json`, `.github/workflows/docker-rfdiffusion.yml` — [VERIFIED: direct file read]
- `backend/requirements.txt` — confirmed pytest-cov is absent [VERIFIED: direct file read]
- `npx playwright --version` — confirmed 1.59.1 [VERIFIED: CLI output]

### Secondary (MEDIUM confidence)
- [CITED: playwright.dev/docs/pom] — Page Object Model pattern
- [CITED: playwright.dev/docs/test-configuration] — `playwright.config.ts` structure
- [CITED: playwright.dev/docs/ci#caching-browsers] — Playwright browser caching in CI
- [CITED: pytest-cov.readthedocs.io/en/latest/config.html] — pytest.ini coverage config

### Tertiary (LOW confidence / ASSUMED)
- pytest-cov 6.1.0 as current version — [ASSUMED] — verify with `pip index versions pytest-cov`
- `supabase/setup-cli@v1` action behavior — [ASSUMED] — review action README before implementing
- @vitest/coverage-v8 Vite 8 compatibility — [ASSUMED] — standard vitest docs recommend V8 provider

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all tools verified via direct file reads and CLI checks
- Architecture: HIGH — patterns directly sourced from existing codebase conventions
- Pitfalls: HIGH — several sourced from explicit STATE.md Phase 3 decisions; others from codebase inspection
- CI config: MEDIUM — structural template from existing workflows; supabase/setup-cli action behavior assumed

**Research date:** 2026-04-10
**Valid until:** 2026-05-10 (stable tooling — 30 days)
