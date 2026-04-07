---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: Ready to execute
stopped_at: Completed 05-05-PLAN.md
last_updated: "2026-04-07T03:30:50.525Z"
progress:
  total_phases: 13
  completed_phases: 3
  total_plans: 26
  completed_plans: 19
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-18)

**Core value:** A scientist should be able to go from "I want to design a binder for IL-6 receptor" to downloadable, scored PDB structures without writing a single config file.
**Current focus:** Phase 04 — pipeline-validation

## Current Position

Phase: 04 (pipeline-validation) — EXECUTING
Plan: 6 of 7

## Performance Metrics

**Velocity:**

- Total plans completed: 1
- Average duration: 15 min
- Total execution time: 0.25 hours

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
| Phase 01-foundation P03 | 11 | 2 tasks | 28 files |
| Phase 02-agent-and-structure-input P01 | 5min | 2 tasks | 14 files |
| Phase 02-agent-and-structure-input P03 | 5min | 2 tasks | 7 files |
| Phase 02-agent-and-structure-input P02 | 6min | 2 tasks | 8 files |
| Phase 02-agent-and-structure-input P04 | 6min | 2 tasks | 18 files |
| Phase 03-job-execution-frontend-and-billing P01 | 15min | 3 tasks | 18 files |
| Phase 03 P02 | 8 | 2 tasks | 5 files |
| Phase 03-job-execution-frontend-and-billing P03 | 35 | 2 tasks | 14 files |
| Phase 03-job-execution-frontend-and-billing P04 | 4min | 2 tasks | 11 files |
| Phase 04-pipeline-validation P01 | 4min | 2 tasks | 13 files |
| Phase 05-production-hardening P02 | 3min | 1 tasks | 4 files |
| Phase 05 P01 | 6min | 2 tasks | 9 files |
| Phase 05 P03 | 4min | 2 tasks | 5 files |
| Phase 05 P04 | 4min | 2 tasks | 6 files |
| Phase 05 P05 | 5min | 2 tasks | 10 files |

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
- [Phase 01-foundation]: shadcn 4.x requires Tailwind v4 (not v3); font/color config moved to CSS @theme inline; tailwind.config.ts kept as reference doc
- [Phase 01-foundation]: form.tsx created manually since shadcn 4.x add form produced no output; follows canonical shadcn react-hook-form pattern
- [Phase 02-agent-and-structure-input]: Renamed backend/pdb/ to backend/pdb_utils/ — 'pdb' shadows stdlib debugger module causing pytest INTERNALERROR; all downstream plans must use pdb_utils import path
- [Phase 02-agent-and-structure-input]: WIZARD_PARAMS: 3 rfdiffusion params, 4 bindcraft params, 3 boltzgen params — essential inputs only, advanced params deferred to v2
- [Phase 02-agent-and-structure-input]: pdb_utils imports in tools.py guarded with try/except ImportError — Plan 02-02 runs in same wave; guard prevents ImportError if 02-03 completes first
- [Phase 02-agent-and-structure-input]: Anthropic SDK messages.create is synchronous inside async SSE generator — acceptable since tool I/O is the dominant latency source
- [Phase 02-agent-and-structure-input]: pdb_utils.* import path used throughout (not pdb.*) — directory renamed in Plan 02-01 to avoid shadowing Python stdlib debugger module
- [Phase 02-agent-and-structure-input]: BioPython DisorderedAtom handles altloc implicitly (highest occupancy default) — no explicit altloc normalization pass required
- [Phase 02-agent-and-structure-input]: resolve_pdb_for_uniprot propagates 404 as HTTPStatusError; router converts to 404 HTTPException with user-friendly message
- [Phase 02-agent-and-structure-input]: shadcn AI chat components unavailable for base-nova style via CLI — created manual equivalents (MessageList wraps ScrollArea, ChatInput wraps Textarea with drag handlers)
- [Phase 02-agent-and-structure-input]: SSE client uses fetch + ReadableStream (not EventSource) — EventSource is GET-only, agent endpoint requires POST body with session_id and message
- [Phase 02-agent-and-structure-input]: Inline markdown renderer is regex-based (no library) — agent responses use only bold, inline code, and bullets; full parser not warranted
- [Phase 02-agent-and-structure-input]: CSRF token read from csrftoken cookie and sent as X-CSRFToken header in all POST requests from SSE agent client — CSRFMiddleware blocks unauthenticated state-changing requests
- [Phase 02-agent-and-structure-input]: RFantibody added as fourth tool option alongside RFdiffusion, BindCraft, BoltzGen — accurate representation of Ranomics production toolset; system prompt grounded in published tool capabilities
- [Phase 02-agent-and-structure-input]: StructurePreviewCard renders defensively with optional chaining on all fields — agent streams tool results incrementally, cards must handle partial data without crashing
- [Phase 03-job-execution-frontend-and-billing]: GPUProvider ABC defines 4 abstract methods (submit_job, get_status, cancel_job, get_results); get_results on RunPod re-fetches status endpoint (output embedded in status response, no separate results endpoint)
- [Phase 03-job-execution-frontend-and-billing]: Stripe Billing Meters API value field must be str(gpu_seconds) not int — API rejects integer values; enforced in stripe_client.py and documented in test scaffold
- [Phase 03-job-execution-frontend-and-billing]: estimate_cost_range scales by max(1, num_designs/10) — batch sizes up to 10 run concurrently; beyond 10 cost scales linearly
- [Phase 03]: Billing router uses _resolve_stripe_customer helper to avoid duplicating pool acquisition and email lookup across 3 auth-protected endpoints
- [Phase 03]: Estimate endpoint is unauthenticated (informational); all payment-mutating endpoints require get_current_user
- [Phase 03-job-execution-frontend-and-billing]: FastAPI Depends() requires app.dependency_overrides for test mocking — unittest.mock.patch does not intercept dependency injection
- [Phase 03-job-execution-frontend-and-billing]: Router and worker DB pool mocks must be separate objects — shared side_effect iterators are consumed by both callers causing StopIteration
- [Phase 03-job-execution-frontend-and-billing]: SSE subscription uses AbortController.abort() for cleanup on unmount — avoids leaked streams when user navigates away from JobPage
- [Phase 03-job-execution-frontend-and-billing]: BindCraftZeroOutputCard uses no destructive colors — zero-output is expected BindCraft behavior, not failure; distinct from JobFailureCard
- [Phase 03-job-execution-frontend-and-billing]: JobPage re-fetches full job on terminal SSE event — ensures candidates and billing data are present when rendering results section
- [Phase 04-pipeline-validation]: ToolPipeline ABC with generate_config + parse_results + timeout/expiry properties: each tool encapsulates its own config format and output parsing
- [Phase 04-pipeline-validation]: Presigned URL expiry defaults to 1.5x execution timeout (min 7200s); BindCraft overrides to 21600s (6hr) for its 4hr runtime
- [Phase 04-pipeline-validation]: PXDesign basic preset only in v1 -- extended mode requires MSA preparation, deferred to future release
- [Phase 04-pipeline-validation]: RunPod executionTimeout policy sent per-job via optional policy field on GPUJobSubmission dataclass
- [Phase 05-production-hardening]: Stripe idempotency key format gpu_usage_{job_id} -- scoped per job, 24hr dedup window
- [Phase 05-production-hardening]: Malformed webhook timestamps skip replay check rather than rejecting -- backward compatibility
- [Phase 05]: Rate limit key extracts user_id from access_token cookie (decode without verify) for per-user limits, falls back to client IP
- [Phase 05]: Sentry APM disabled (traces_sample_rate=0.0) for v1 -- error tracking only
- [Phase 05]: Heartbeat URL derived from webhook URL via string replace; stale billing capped at threshold
- [Phase 05]: On-demand upload URLs replace pre-generated URLs; job token (token_urlsafe(32)) authenticates container-to-backend uploads
- [Phase 05]: Sentry APM disabled (tracesSampleRate=0) for v1 -- error tracking only
- [Phase 05]: SSE counter uses shared Redis key with 5-min TTL safety net across agent and jobs routers

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 2: UniProt → PDB resolution returns many-to-many matches; wizard UX for structure selection step not yet designed — must be scoped before Phase 2 implementation begins
- Phase 2: Claude Agent SDK session resumption behavior at context window limits is unvalidated — decide on summarization vs. truncation policy before Phase 2 implementation
- Phase 4: Stripe billing policy for zero-output BindCraft runs and partial job refunds must be drafted before Phase 3 billing integration (affects Phase 3 copy and ToS)

## Session Continuity

Last session: 2026-04-07T03:30:50.519Z
Stopped at: Completed 05-05-PLAN.md
Resume file: None
