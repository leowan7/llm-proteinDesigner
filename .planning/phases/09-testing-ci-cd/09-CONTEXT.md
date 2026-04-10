# Phase 9: Testing & CI/CD - Context

**Gathered:** 2026-04-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Automated test coverage across backend and frontend, a CI pipeline that blocks broken PRs, and a post-deploy smoke test. No new features — this phase ensures what's built works reliably and regressions are caught before production.

</domain>

<decisions>
## Implementation Decisions

### Backend Test Coverage
- **D-01:** Coverage target >80% line coverage across all backend modules (agent, billing, jobs, PDB, auth, admin, analysis, webhooks, middleware).
- **D-02:** Integration tests hit a real Supabase test instance in CI — no mocked databases for integration tests. Unit tests may use mocks for external services (Stripe, RunPod, Anthropic).
- **D-03:** pytest is the test runner (already configured with `asyncio_mode = auto`). Existing ~20 test files across 7 modules form the baseline.
- **D-04:** Coverage gaps to fill: sessions module (Phase 6), settings/usage endpoints (Phase 6), admin endpoints (Phase 7), analysis tools (Phase 8 — some tests exist), webhooks, middleware (rate limiting, logging), worker tasks (cleanup, stale detection).

### Frontend Testing
- **D-05:** Vitest for frontend unit tests. Native Vite integration, same config as build.
- **D-06:** Playwright for E2E tests covering 4 core flows:
  1. Login + auth (signup, login, session persistence, password reset)
  2. Chat + job launch (message, agent response, wizard, structure card, review card, launch)
  3. Job status + results (job page load, SSE status, results display, download)
  4. Settings + billing (tabs load, Stripe portal link, usage data)
- **D-07:** Frontend unit tests cover: API client functions, utility functions, component rendering (smoke tests for key components like ChatPage, JobPage, SettingsPage).

### CI Pipeline Design
- **D-08:** GitHub Actions workflow runs on every PR to main. Four gates — all must pass to merge:
  1. Backend tests (pytest with real DB)
  2. Frontend tests (Vitest unit + Playwright E2E)
  3. Lint + type check (eslint with jsx-a11y plugin, TypeScript --noEmit, ruff for Python)
  4. Coverage threshold (fail if backend drops below 80%)
- **D-09:** Docker image builds run in CI via GitHub Actions, push to ghcr.io on merge to main. Existing Docker workflow YAMLs (5 tool-specific files) are the starting point.
- **D-10:** CI needs a Supabase test instance — configure via GitHub Secrets (SUPABASE_URL, SUPABASE_SERVICE_KEY for test env).

### Pre-Deploy Smoke Test
- **D-11:** Post-deploy smoke test verifies 4 things against production:
  1. Health endpoint returns 200 (existing /health deep check: DB + Redis + R2)
  2. Auth flow works (login with test account, verify token)
  3. Frontend loads (Playwright hits production URL, verifies app renders)
  4. API response time <2s on health endpoint
- **D-12:** Manual rollback on smoke test failure — alert via Sentry, human decides via Railway/Vercel deploy history. No automated rollback.
- **D-13:** Smoke test runs as a separate GitHub Actions workflow triggered after deploy completes (workflow_run trigger or manual dispatch).

### Claude's Discretion
- Exact pytest fixture structure for real DB integration tests
- Vitest config details and test file organization
- Playwright test helpers and page object patterns
- GitHub Actions caching strategy (pip, node_modules, Playwright browsers)
- Coverage report format and where to publish (PR comment vs artifact)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Test Infrastructure
- `backend/tests/conftest.py` — Existing pytest conftest with shared fixtures
- `backend/pytest.ini` — pytest config (asyncio_mode = auto)
- `frontend/src/lib/api.test.ts` — Only existing frontend test file

### CI Configuration
- `.github/workflows/` — Existing Docker build workflows (5 tool-specific YAMLs)

### Phase 5 Context (production hardening decisions)
- `.planning/phases/05-production-hardening/05-CONTEXT.md` — Rate limiting, health check, Sentry, structured logging decisions that affect what to test

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **pytest with asyncio_mode=auto**: Already configured, async tests work out of the box
- **20+ backend test files**: Established patterns for mocking Supabase, Stripe, RunPod
- **conftest.py**: Shared fixtures (likely DB connection, auth mocking)
- **GitHub Actions**: 5 Docker build workflows already exist — can extend with test workflows

### Established Patterns
- **Backend tests**: Each module has its own test subdirectory (tests/agent/, tests/billing/, etc.)
- **FastAPI test client**: Uses `app.dependency_overrides` for mocking (Phase 3 decision)
- **Separate mock objects**: Router and worker DB pool mocks must be separate (Phase 3 lesson)

### Integration Points
- **CI workflow**: New `.github/workflows/test.yml` for PR checks, separate from Docker builds
- **Supabase in CI**: Need test project or local Supabase CLI in GitHub Actions
- **Playwright**: Needs both backend and frontend running — test compose or process management
- **Coverage**: pytest-cov for backend, vitest coverage for frontend

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Follow established patterns from existing test files.

</specifics>

<deferred>
## Deferred Ideas

- **Load testing** — Stress testing GPU job submission, SSE connections under load. Not needed pre-launch with limited user base.
- **Visual regression testing** — Screenshot comparison for UI changes. Premature before design system stabilizes.
- **Contract testing** — API contract tests between frontend and backend. Adds complexity, TypeScript types provide sufficient coverage at current scale.

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 09-testing-ci-cd*
*Context gathered: 2026-04-10*
