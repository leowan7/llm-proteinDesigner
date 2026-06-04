---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: In Progress
stopped_at: Phase 12 Plan 05 complete
last_updated: "2026-06-04T11:45:00.000Z"
progress:
  total_phases: 13
  completed_phases: 9
  total_plans: 61
  completed_plans: 60
  percent: 98
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-18)

**Core value:** A scientist should be able to go from "I want to design a binder for IL-6 receptor" to downloadable, scored PDB structures without writing a single config file.
**Current focus:** Phase 12 — teams-and-organizations

## Current Position

Phase: 12
Plan: 12-06 (Wave 3 — Playwright E2E spec exercising create-org → invite → accept → run-job → owner views billing; drop deprecated users.stripe_customer_id column; REQUIREMENTS.md ORG-01..ORG-08 traceability update; Phase 12 rollout runbook). 12-01 + 12-02 + 12-03 + 12-04 + 12-05 complete; final plan 12-06 in `.planning/phases/12-teams-and-organizations/`.

## Performance Metrics

**Velocity:**

- Total plans completed: 22
- Average duration: 14 min
- Total execution time: 0.33 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 07 | 5 | - | - |
| 08 | 4 | - | - |
| 10 | 6 | - | - |

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
| Phase 06-ui-improvements P04 | 387 | 2 tasks | 9 files |
| Phase 12-teams-and-organizations P01 | 5min | 2 tasks | 6 files |
| Phase 12-teams-and-organizations P02 | 13min | 2 tasks | 16 files |
| Phase 12-teams-and-organizations P03 | 19min | 2 tasks | 20 files |
| Phase 12-teams-and-organizations P04 | 6min | 2 tasks | 4 files |
| Phase 12-teams-and-organizations P05 | 24min | 2 tasks | 21 files |

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
- [Phase 06-ui-improvements]: ChatInput injectedValue prop pattern for prompt injection rather than lifting full text state
- [Phase 06-ui-improvements]: GreetingCard onPromptClick threaded through MessageList to avoid breaking MessageList props contract
- [Phase 12-teams-and-organizations]: RLS helpers use LANGUAGE plpgsql (not sql) — Postgres inlines SQL functions during planning, dropping SECURITY DEFINER context and triggering infinite recursion in RLS predicates (research §14.1)
- [Phase 12-teams-and-organizations]: Last-owner invariant is DB-enforced via BEFORE UPDATE OR DELETE trigger on organization_memberships — application-level checks race under concurrent DELETEs
- [Phase 12-teams-and-organizations]: Stripe customer_id MOVED (not copied) from public.users to auto-created personal org so existing metered subscriptions stay attached to same Stripe customer
- [Phase 12-teams-and-organizations]: users.stripe_customer_id is DEPRECATED via COMMENT but NOT dropped in plan 12-01; drop deferred to plan 12-06 (20260606000001) so backend rollback is safe within 24h verification window
- [Phase 12-teams-and-organizations]: test_rls_jobs_org.py uses set_config('request.jwt.claims', value, true) instead of literal SET LOCAL — asyncpg cannot bind parameters into SET LOCAL with dotted GUC names; behavior equivalent
- [Phase 12-teams-and-organizations]: get_active_org enforces X-Org-Id header presence (400 if missing); routes that legitimately have no active-org context (GET /organizations/mine, POST /organizations, POST /invitations/accept) use only get_current_user not get_active_org
- [Phase 12-teams-and-organizations]: require_role(*allowed) is a factory returning an inner FastAPI dep that consumes get_active_org and returns just org_id on success; handlers want the id not the (org_id, role) tuple
- [Phase 12-teams-and-organizations]: POST /organizations uses set_config('request.jwt.claims', $1, true) on the connection before fetchval'ing the SECURITY DEFINER RPC so auth.uid() resolves correctly from the service_role pool
- [Phase 12-teams-and-organizations]: settings.organizations_enabled default-False; main.py conditional include_router so single-tenant routes stay unchanged until Plan 12-04 flips the flag
- [Phase 12-teams-and-organizations]: Pydantic v2 form Annotated[str, StringConstraints(...)] over Field(strip_whitespace=True) — the Field-arg form is deprecated in Pydantic v2 and warns on every test
- [Phase 12-teams-and-organizations]: Tests build isolated FastAPI sub-apps per test (FastAPI() + include_router + dependency_overrides) rather than mounting on main.app — avoids depending on global flag state at import time
- [Phase 12-teams-and-organizations]: Webhook handler routes billing via JOIN through jobs.organization_id (not via users.stripe_customer_id) — service-role pool bypasses RLS in the unauth webhook context, and the JOIN gives the correct org-scoped customer for both personal and team orgs
- [Phase 12-teams-and-organizations]: jobs/service.cancel_job_by_id is also a cutover surface (not in plan's enumerated files) — billing block rewritten via the same org JOIN pattern; cancel runs from both user and admin paths so neither can rely on is_member_of()
- [Phase 12-teams-and-organizations]: /user/usage owner sees all org jobs, scientist sees only created_by_user_id=self, viewer 403 — no use case for read-only members to see org spend
- [Phase 12-teams-and-organizations]: Full-design pilot eligibility flipped from user_id-scoped to organization_id-scoped — any org-completed pilot qualifies any org member, matches org-shared-jobs design
- [Phase 12-teams-and-organizations]: Download endpoint reads user_id from the job row for the S3 prefix (immutable storage path) but gates access by org_id — separates audit trail from access control
- [Phase 12-teams-and-organizations]: Single-tenant existing tests still pass under cutover via app.dependency_overrides[get_active_org] = (org_id, role) tuple — feature flag only governs main.py mount, but the routers themselves now unconditionally depend on get_active_org
- [Phase 12-teams-and-organizations]: Stamp script idempotency check is keyed only on metadata.organization_id, not the full 4-key payload — kendrew_org_name and migrated_from_user_v1 can legitimately drift between runs (renames, re-runs on different dates); organization_id is the ground truth
- [Phase 12-teams-and-organizations]: stamp_stripe_org_metadata.py never creates Stripe customers — only stamps metadata on existing ones; net-new team orgs lazily create their first customer via billing/stripe_client.get_or_create_customer on first billing interaction
- [Phase 12-teams-and-organizations]: Stamp/verify scripts use single-line SQL strings (not Python implicit-concatenation) so acceptance-criteria substring greps match the literal SELECT phrase
- [Phase 12-teams-and-organizations]: --test-mode is the live/test guard — uses a separately-named STRIPE_TEST_SECRET_KEY env var instead of STRIPE_SECRET_KEY so an operator cannot accidentally hit live Stripe by misreading the help text
- [Phase 12-teams-and-organizations]: frontend useOrgContext() returns a safe empty fallback ({orgs:[], activeOrg:null, role:null}) when no <OrgProvider> is mounted — single-tenant + Vitest-scaffold compatible (no breaking changes to Plan 09 + Plan 10 specs that render pages without the full layout chain)
- [Phase 12-teams-and-organizations]: X-Org-Id header opt-out is an explicit list (4 prefix matches + POST /organizations exact match) in api.ts, not an allowlist — minimises blast radius when new routes ship without touching api.ts
- [Phase 12-teams-and-organizations]: OrganizationSwitcher is hidden whenever orgs.length <= 1 (covers both solo users + single-tenant deployments where the feature flag is off and /organizations/mine returns 404 → orgs=[])
- [Phase 12-teams-and-organizations]: setActiveOrg writes localStorage BEFORE reload so post-reload OrgProvider.refresh() picks up the new value via resolveActiveOrgId(); AcceptInvitation page additionally pre-seeds localStorage and navigate("/jobs") as a fallback for the public route case where setActiveOrg's no-op fallback would otherwise leave the user staring at "Joined!"

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 2: UniProt → PDB resolution returns many-to-many matches; wizard UX for structure selection step not yet designed — must be scoped before Phase 2 implementation begins
- Phase 2: Claude Agent SDK session resumption behavior at context window limits is unvalidated — decide on summarization vs. truncation policy before Phase 2 implementation
- Phase 4: Stripe billing policy for zero-output BindCraft runs and partial job refunds must be drafted before Phase 3 billing integration (affects Phase 3 copy and ToS)

## Session Continuity

Last session: 2026-06-04T11:45:00.000Z
Stopped at: Completed 12-05-PLAN.md (Wave 2 frontend org context + switcher + invitation accept page + members/invitations/settings tabs + owner-gated billing + launched-by column + 4 Vitest specs)
Resume file: .planning/phases/12-teams-and-organizations/12-06-PLAN.md
