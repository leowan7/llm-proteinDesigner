# Phase 9: Testing & CI/CD - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-10
**Phase:** 09-testing-ci-cd
**Areas discussed:** Backend test coverage, Frontend testing, CI pipeline design, Pre-deploy smoke test

---

## Backend Test Coverage

### Coverage target

| Option | Description | Selected |
|--------|-------------|----------|
| >80% line coverage (Recommended) | Industry standard, all modules | ✓ |
| >60% (pragmatic) | Critical paths only | |
| >90% (thorough) | Near-complete, diminishing returns | |

**User's choice:** >80% line coverage

### DB strategy for integration tests

| Option | Description | Selected |
|--------|-------------|----------|
| Real DB for integration (Recommended) | Supabase test instance in CI | ✓ |
| Mocks only | Faster but mock/prod divergence risk | |
| Hybrid | Unit=mocks, integration=real DB | |

**User's choice:** Real DB for integration
**Notes:** User previously got burned by mock/prod divergence (feedback memory)

---

## Frontend Testing

### E2E flows for Playwright

| Option | Description | Selected |
|--------|-------------|----------|
| Login + auth flow | Signup, login, session, password reset | ✓ |
| Chat + job launch | Message, wizard, structure card, launch | ✓ |
| Job status + results | SSE status, results display, download | ✓ |
| Settings + billing | Tabs, Stripe portal, usage data | ✓ |

**User's choice:** All four flows

### Frontend unit test runner

| Option | Description | Selected |
|--------|-------------|----------|
| Vitest (Recommended) | Native Vite integration | ✓ |
| Jest | More setup needed for Vite/ESM | |

**User's choice:** Vitest

---

## CI Pipeline Design

### PR gates

| Option | Description | Selected |
|--------|-------------|----------|
| Backend tests pass | pytest with real DB | ✓ |
| Frontend tests pass | Vitest + Playwright | ✓ |
| Lint + type check | eslint, TypeScript, ruff | ✓ |
| Coverage threshold | Fail if <80% | ✓ |

**User's choice:** All four gates

### Docker CI

| Option | Description | Selected |
|--------|-------------|----------|
| CI builds + push to GHCR (Recommended) | GitHub Actions on merge to main | ✓ |
| Local builds only | Manual workflow | |
| CI builds on tag only | Release-triggered | |

**User's choice:** CI builds + push to GHCR

---

## Pre-Deploy Smoke Test

### Smoke test checks

| Option | Description | Selected |
|--------|-------------|----------|
| Health endpoint returns 200 | Existing deep health check | ✓ |
| Auth flow works | Login with test account | ✓ |
| Frontend loads | Playwright verifies render | ✓ |
| API response time <2s | Latency check | ✓ |

**User's choice:** All four checks

### Rollback strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Manual rollback (Recommended) | Alert, human decides via deploy history | ✓ |
| Automatic rollback | CI auto-reverts on failure | |
| Alert only | Notify, investigate manually | |

**User's choice:** Manual rollback

---

## Claude's Discretion

- pytest fixture structure for real DB integration
- Vitest config and test file organization
- Playwright page object patterns
- GitHub Actions caching strategy
- Coverage report format

## Deferred Ideas

- Load testing — not needed pre-launch
- Visual regression testing — premature before design system stabilizes
- Contract testing — TypeScript types suffice at current scale
