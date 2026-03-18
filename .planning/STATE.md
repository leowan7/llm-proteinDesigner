---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: unknown
stopped_at: Completed 01-foundation 01-02-PLAN.md
last_updated: "2026-03-18T21:28:56.266Z"
progress:
  total_phases: 4
  completed_phases: 0
  total_plans: 4
  completed_plans: 2
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-18)

**Core value:** A scientist should be able to go from "I want to design a binder for IL-6 receptor" to downloadable, scored PDB structures without writing a single config file.
**Current focus:** Phase 01 — foundation

## Current Position

Phase: 01 (foundation) — EXECUTING
Plan: 1 of 4

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: —
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*
| Phase 01-foundation P01 | 4 | 2 tasks | 12 files |
| Phase 01-foundation P02 | 7min | 2 tasks | 13 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: Claude as LLM backend (Anthropic SDK 0.43.x + Instructor 1.x); pending final confirmation
- Roadmap: Abstract GPU provider interface (ModalProvider + RunPodProvider behind GPUProvider ABC); pending final confirmation
- Roadmap: Guided wizard over full parameter abstraction; pending final confirmation
- Roadmap: Pay-per-job billing via Stripe Billing Meters API (not legacy Usage Records); pending final confirmation
- [Phase 01-foundation]: PyJWT over python-jose: python-jose unmaintained with known CVEs; PyJWT 2.x is the current standard for HS256 JWT validation
- [Phase 01-foundation]: Supabase CLI owns Postgres on port 54322; docker-compose.yml contains no Postgres service to avoid port conflicts
- [Phase 01-foundation]: enable_confirmations = true enforces AUTH-02 email verification; database_url and testing fields in Pydantic Settings support Docker networking and CSRF test bypass
- [Phase 01-foundation]: CSRF middleware env-gated at import time: settings.testing must be set before importing main.py in tests to prevent CSRFMiddleware registration
- [Phase 01-foundation]: Refresh token cookie scoped to path=/auth/refresh, access token at path=/ — minimizes refresh token exposure surface
- [Phase 01-foundation]: exchange-token endpoint validates recovery JWT before setting HTTP-only cookie — prevents arbitrary tokens being stored

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 2: UniProt → PDB resolution returns many-to-many matches; wizard UX for structure selection step not yet designed — must be scoped before Phase 2 implementation begins
- Phase 2: Claude Agent SDK session resumption behavior at context window limits is unvalidated — decide on summarization vs. truncation policy before Phase 2 implementation
- Phase 4: Stripe billing policy for zero-output BindCraft runs and partial job refunds must be drafted before Phase 3 billing integration (affects Phase 3 copy and ToS)

## Session Continuity

Last session: 2026-03-18T21:28:56.262Z
Stopped at: Completed 01-foundation 01-02-PLAN.md
Resume file: None
