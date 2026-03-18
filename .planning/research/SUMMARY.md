# Project Research Summary

**Project:** LLM Protein Designer
**Domain:** Scientific SaaS — LLM-agent-driven protein design with async GPU job dispatch and pay-per-job billing
**Researched:** 2026-03-18
**Confidence:** MEDIUM-HIGH

## Executive Summary

This product is a natural-language-to-protein-design platform that wraps Ranomics' operational knowledge of RFdiffusion, BindCraft, and Boltzgen into a conversational agent that takes a scientist's design intent and converts it into a validated, dispatched GPU job with ranked output structures. The closest competitor is Tamarind Bio, which offers form-based no-code access to similar tools. The differentiation is the Claude-powered parameter wizard with Ranomics expert defaults encoded, automated tool selection with transparent rationale, and post-job guidance — none of which Tamarind provides. The core thesis is: scientists go from natural language goal to downloadable ranked PDB structures without writing a config file.

The recommended architecture is a fully async Python stack (FastAPI + Taskiq + PostgreSQL + Redis) with a Next.js frontend, Supabase Auth, Modal as the primary GPU provider, and Stripe Billing Meters for pay-per-job revenue. The Claude agent layer uses the Anthropic Python SDK with Instructor for structured outputs; it manages a multi-turn wizard session and emits a validated `JobSpec` that a separate Job Service dispatches. These two concerns — agent conversation and job execution — must be strictly decoupled: the agent produces a spec, the job service executes it. GPU containers (one per tool, Docker images deployed to Modal/RunPod) write outputs directly to Cloudflare R2; the frontend receives presigned URLs only.

The dominant risk category is billing integrity: GPU jobs run for 30 minutes to 2 hours, produce probabilistic output, and cost real money before the user sees any results. Three failure modes account for most of this risk: (1) jobs dispatching on invalid or edge-case PDB input before pre-flight validation runs, (2) stuck GPU containers billing indefinitely without a watchdog, and (3) non-idempotent dispatch retries creating double-billed jobs. All three must be designed into the system before any external user access. A secondary risk is agent trust: if the tool selection rationale is opaque or the wizard does not surface what was understood from the user's request, scientists will distrust the platform and abandon it before it delivers value.

## Key Findings

### Recommended Stack

The backend stack centers on FastAPI 0.115.x with SQLAlchemy 2.0 (AsyncSession + asyncpg) and Taskiq 0.11.x as the async task queue. Taskiq is the correct choice over Celery because the entire stack is async; Celery's synchronous worker model requires `asyncio.run()` inside tasks, which is wasteful and error-prone in a FastAPI codebase. Redis 7.x serves dual duty as the Taskiq broker and as an SSE pub/sub bus and agent session cache. For GPU dispatch, Modal 0.73.x is the primary provider (Python-native, decorator-based, no container management overhead); RunPod is the secondary target behind a `GPUProvider` abstract interface. Both must be swappable via env var without changing calling code.

The Claude integration uses the Anthropic Python SDK 0.43.x with `AsyncAnthropic` for all agent calls and Instructor 1.x for structured outputs. Instructor wraps the SDK to return validated Pydantic models directly from tool calls — this eliminates manual JSON parsing from wizard responses. The current production model is `claude-sonnet-4-6`; do not use any 3.x model. Object storage is Cloudflare R2 over AWS S3 (S3-compatible, zero egress fees — critical when users download multi-MB PDB files). Stripe Billing Meters API handles pay-per-job metering; the legacy Usage Records API was removed in Stripe API version `2025-03-31.basil` and must not be used.

**Core technologies:**
- FastAPI 0.115.x: async API backend — native async, Pydantic v2, automatic OpenAPI
- PostgreSQL 16+ / SQLAlchemy 2.0 / asyncpg: relational job store with JSONB for flexible params
- Redis 7.x: Taskiq broker, SSE pub/sub, agent session cache
- Taskiq 0.11.x: async task queue built for asyncio — no Celery
- Next.js 15.x: frontend — SSR for auth/marketing, SPA behavior for dashboard and 3D viewer
- Supabase Auth: JWT issuance + OAuth; row-level security at the database layer
- Anthropic SDK 0.43.x + Instructor 1.x: Claude agent with structured Pydantic output
- Modal 0.73.x: primary GPU provider; RunPod 1.7.x as secondary via abstract interface
- Cloudflare R2: object storage for PDB inputs and outputs — no egress fees
- Stripe Billing Meters API: pay-per-job usage metering at job completion
- Mol* (molstar) 4.x: in-browser 3D structure viewer — RCSB standard, handles large complexes

### Expected Features

The MVP core thesis is: "scientists can go from natural language goal to downloadable ranked PDB structures without writing a config file." Every feature in v1 must serve that thesis directly.

**Must have (table stakes) — missing any of these ends the trial:**
- User authentication (email/password + Google OAuth) — no auth = no trust for proprietary targets
- PDB file upload and accession ID fetch (RCSB / UniProt) — primary structure input paths
- Job status monitoring with milestone stages — users will not sit watching a blank dashboard
- Email notification on job completion — 30-90 minute jobs require async communication
- Design scoring display (pLDDT, ipTM, interface score, ranked table) — required to interpret results
- Interactive 3D structure viewer (Mol*) — scientists will not trust output they cannot inspect
- PDB download (individual and batch zip) — core deliverable
- Job history list — basic navigation for multi-job users
- Pay-per-job billing with Stripe — revenue model

**Should have (differentiators against Tamarind Bio):**
- Natural language goal intake via Claude agent — the core differentiator; no competitor does this
- Automated tool selection (RFdiffusion / BindCraft / Boltzgen) with explicit rationale — builds trust
- Agent-guided parameter wizard with Ranomics expert defaults — prevents costly parameter mistakes
- Cost estimate shown before dispatch with user confirmation gate — required for billing trust
- Post-job next-step guidance from agent — positions Ranomics expertise, not just compute
- Conversation history within a job — follow-up questions in context of completed job

**Defer to v1.x after validation:**
- Ranked design report export (PDF) — users will request this once sharing results with teams
- Expert hotspot inference / SASA-based suggestion — high value but adds SASA integration complexity
- Job parameter replay (copy settings from past job) — natural once history is used regularly

**Defer to v2+:**
- Team / shared workspaces — adds multi-tenant complexity before PMF is established
- REST API for power users — when biopharma IT teams want pipeline integration
- Self-hosted GPU / bring-your-own-cluster — enterprise-only, out of scope for v1

**Anti-features to explicitly reject:**
- Real-time streaming log output — adds WebSocket complexity across provider abstraction for low diagnostic value; use milestone stages instead
- Sequence-only input without structure — cannot define hotspots or interface geometry from sequence alone; link users to AlphaFold Server
- Auto-retry failed jobs — silent retries accumulate GPU cost and mask configuration errors

### Architecture Approach

The system is a four-layer architecture: Next.js frontend (chat UI, job monitor, Mol* viewer) communicates only with the FastAPI backend via REST and SSE. The FastAPI backend is partitioned into an Agent Service (Claude sessions, wizard, produces `JobSpec`) and a Job Service (dispatch, status polling, presigned URLs) — these two are strictly decoupled by a `JobSpec` Pydantic model boundary. The Job Service dispatches to GPU containers through a `GPUProvider` abstract interface, never calling Modal or RunPod directly. GPU containers are stateless, write outputs to R2, and exit. A background polling loop (separate asyncio task or Taskiq worker) polls the GPU provider every 30-60 seconds and publishes status changes to Redis pub/sub channels; SSE endpoints subscribe to Redis and push to the browser. Files never proxy through the backend — the browser downloads PDB files directly from R2 via time-limited presigned URLs.

**Major components:**
1. Agent Service — manages Claude sessions via session_id, runs the wizard, emits validated `JobSpec`; never dispatches jobs
2. Job Service — sole writer to the jobs table; dispatches via `GPUProvider` interface; manages SSE, polling, and presigned URL generation
3. GPUProvider ABC — abstract interface with `submit()`, `get_status()`, `cancel()`; `ModalProvider` and `RunPodProvider` as concrete implementations; `MockProvider` for testing
4. Background Poller — asyncio loop polling all QUEUED/RUNNING jobs every 30-60s; publishes to Redis; never called from HTTP handlers
5. GPU Containers — one Docker image per tool (RFdiffusion, BindCraft, Boltzgen); write to R2 via boto3; stateless, no DB knowledge
6. Redis — SSE pub/sub bus per `job:{job_id}:status` channel; agent session cache (conversation history keyed by session_id with TTL); not source of truth
7. PostgreSQL (jobs table) — single authoritative store for job records: status, cost, params, user ownership, provider_job_id

The build order is dependency-driven: data layer first, then auth, then MockProvider (this is the critical path enabler — it unblocks agent and job service development simultaneously), then agent service, then job + SSE, then frontend, then billing, then RunPodProvider as the second concrete implementation.

### Critical Pitfalls

1. **No pre-flight PDB validation before GPU dispatch** — Jobs dispatch on malformed PDBs (multi-model NMR, insertion codes, HETATM-only, MSE residues, wrong chain count) and fail silently inside the GPU container after the billing clock has started. Prevention: synchronous pre-flight validation using Biopython with `QUIET=False` (capture all warnings as gates), normalize PDB on ingest (extract MODEL 1, first altloc, convert MSE to MET, strip HETATM), validate hotspot residue indices against normalized structure. Must be built and tested against real edge-case PDB files before GPU integration.

2. **Stuck GPU containers with no watchdog** — Jobs hang on Modal or RunPod (CUDA context alive, no progress, no error). GPU meter runs. The application shows "running." Hours pass. Prevention: heartbeat from container to DB every 5 minutes; watchdog marks job as suspected-hung if no heartbeat for 10 minutes and sends explicit pod-kill to the provider. Hard wallclock timeouts per tool (RFdiffusion: 90 min max, BindCraft: 3 hrs max). Never assume process exit equals billing stop — call the provider's pod-kill API explicitly.

3. **Non-idempotent job dispatch creating double charges** — Network blip during dispatch causes retry; provider already started first job; application tracks only second job ID; first job runs to completion billing Ranomics but not the user. Prevention: assign application-generated UUID as idempotency key before any provider API call; store `dispatching` state in DB before calling provider; on retry, check whether a provider job already exists for this `job_id` before dispatching again.

4. **Billing users for zero-output BindCraft runs without upfront disclosure** — BindCraft applies strict quality filters (ipTM, pAE, SASA, interface energy thresholds); it is routine for well-configured runs to produce zero passing designs. Users conflate "job finished" with "I got results." Prevention: explicit wizard disclosure before every BindCraft dispatch; on zero-output completion, surface the specific rejection breakdown ("92% failed ipTM threshold, 8% failed interface area"); define and publish refund policy before any public access.

5. **NL quantity ambiguity causing unexpected costs** — "Design lots of binders" gets interpreted as `num_designs=1000` → ~$100 charge the user did not anticipate. Prevention: never let the agent resolve quantity terms silently; the wizard must ask with anchored cost examples ("[10 designs ~$1 | 100 designs ~$10 | 500 designs ~$48]"); show actual estimated cost from real parameters before dispatch; hard per-job cost caps per account tier.

6. **Opaque agent decisions eroding user trust** — Agent selects tool and sets defaults without showing reasoning; users cannot diagnose bad jobs and stop trusting the platform. Prevention: every tool selection must include a specific rationale paragraph shown before wizard starts; show a plain-language summary of what the agent understood ("Target = IL-6R chain A, Goal = de novo binder, no motif constraints — correct?") with a correction opportunity; never default parameters silently.

7. **PDB coordinates sent to Claude API exposing proprietary structures** — Sending raw PDB file content to Anthropic's API processes proprietary target structures as API input. Prevention: send only metadata to the LLM (chain IDs, residue count, resolution, sequence length); never pass PDB coordinates to any Claude API call; confirm Anthropic data processing terms before handling client structures.

## Implications for Roadmap

Based on the combined architecture build order, feature dependencies, and pitfall-to-phase mapping, eight sequential phases are recommended.

### Phase 1: Data Layer and Infrastructure Foundation
**Rationale:** All subsequent components depend on the database schema, object storage layout, and local dev environment. Setting up Alembic migrations now prevents painful schema rewrites later. Per-user S3 prefixes must be established before any PDB file handling.
**Delivers:** PostgreSQL schema (users, jobs tables), Redis in Docker Compose, Cloudflare R2 bucket with `{user_id}/{job_id}/` key structure, Alembic migration setup
**Uses:** PostgreSQL 16, SQLAlchemy 2.0, asyncpg, Redis 7, boto3
**Avoids:** Storing PDB files as database BLOBs (performance trap); shared bucket without per-account isolation (security mistake)

### Phase 2: Authentication and User Accounts
**Rationale:** Auth must gate every other feature — job dispatch, billing, data isolation. No user-scoped route can be built without JWT validation middleware in place. Supabase RLS also requires the DB schema to be stable.
**Delivers:** Email/password + Google OAuth via Supabase Auth; JWT validation middleware in FastAPI; per-user data isolation enforced at DB layer via RLS
**Uses:** Supabase Auth, PyJWT 2.x
**Avoids:** Custom JWT implementation (poor risk/reward for small team)

### Phase 3: GPU Provider Interface and MockProvider
**Rationale:** This is the most important phase enabler in the build order. The `MockProvider` that returns canned job IDs and fake status sequences unblocks Agent Service development (Phase 4) and Job Service development (Phase 5) simultaneously, with no live GPU required. Idempotency design must happen here, before billing is wired in.
**Delivers:** `GPUProvider` ABC (`submit`, `get_status`, `cancel`); `MockProvider` with configurable status sequences; `ModalProvider` concrete implementation; idempotent dispatch state machine (`pending → dispatching → dispatched → running → completed/failed`); hard wallclock timeout configuration per tool
**Uses:** Modal Python SDK 0.73.x; abstract interface pattern
**Avoids:** Modal/RunPod-specific API calls inside Job Service; non-idempotent dispatch (Pitfall 8); GPU provider logic polluting business logic (Anti-Pattern 3)

### Phase 4: Agent Service and PDB Ingest
**Rationale:** The agent produces the `JobSpec` that gates job dispatch. It also handles PDB upload and accession fetch — the first moment proprietary structures enter the system. PDB normalization and pre-flight validation must be built here, before any GPU dispatch can be attempted. Tool classification test suite must be locked before wizard questions are written.
**Delivers:** Claude Agent SDK session management (Redis-backed, session_id → user_id); tool definitions (`fetch_structure`, `emit_job_spec`); tool classification logic with 30+ case test suite; PDB normalization pipeline (MODEL 1 extraction, altloc, MSE, HETATM handling); pre-flight validation checks (chain existence, residue count, hotspot index verification); accession fetch from RCSB and UniProt with caching
**Uses:** Anthropic SDK 0.43.x, Instructor 1.x, Biopython, httpx
**Avoids:** Sending PDB coordinates to Claude API (security mistake); PDB edge cases silently corrupting input (Pitfall 6); prompt drift causing tool misclassification (Pitfall 2); storing agent conversation in the browser (Anti-Pattern 2)

### Phase 5: Job Service, Background Poller, and SSE
**Rationale:** Job dispatch, status monitoring, and result delivery are tightly coupled by the data flow. Building them together ensures the poller, SSE endpoint, and job state machine are consistent. This phase uses MockProvider — no live GPU needed.
**Delivers:** Job dispatch (writes to DB, calls provider, returns 202 with job_id); background polling loop (30-60s, publishes to Redis pub/sub per `job:{job_id}:status`); SSE endpoint with Redis subscription and reconnect handling; presigned URL generation for R2 downloads; watchdog for stuck jobs (10-minute heartbeat gap → pod-kill); job completion handler distinguishing zero-output from failure
**Uses:** Taskiq 0.11.x, Redis pub/sub, sse-starlette, boto3/aiobotocore
**Avoids:** Polling GPU provider from SSE handler (Anti-Pattern 4); holding HTTP connection open for GPU job (Anti-Pattern 1); stuck container cost runaway (Pitfall 3)

### Phase 6: Frontend — Chat, Job Monitor, and Structure Viewer
**Rationale:** Frontend is buildable now because the API surface is stable (auth, agent, jobs, SSE, presigned URLs). The 3D viewer requires stable presigned URL delivery to function correctly.
**Delivers:** Chat/wizard UI (conversational, shows agent rationale before confirmation, plain-language parameter summary); job monitor (SSE consumer, milestone stage display: queued / initializing / designing / scoring / complete); Mol* structure viewer embedded in results page; PDB download links; job history list; cost estimate display with confirmation gate (blocks dispatch until confirmed)
**Uses:** Next.js 15.x, React 19, molstar 4.x, molstar-react, SSE EventSource API
**Avoids:** Hiding agent reasoning from users (Pitfall 7); no cost estimate before dispatch (UX pitfall); "job running" as the only status state (UX pitfall); stack traces as failure messages (UX pitfall)

### Phase 7: Billing Integration
**Rationale:** Billing must be wired in after the job completion state machine is stable, because Stripe usage should be reported at confirmed job completion with provider-confirmed cost — not estimated at dispatch. The zero-output vs. failure distinction (Phase 5) is required before billing policy can be enforced.
**Delivers:** Stripe Billing Meters API integration (meter event emitted at job completion with GPU seconds from provider API); per-job cost cap enforcement; cost estimate calculation from tool + num_designs + historical runtime; Stripe webhook handler at `/billing/webhook`; refund policy and wizard disclosure copy for zero-output BindCraft runs
**Uses:** Stripe Python SDK 7.x, Stripe Billing Meters API (`stripe.billing.meter_event`)
**Avoids:** Legacy Stripe Usage Records API (removed in `2025-03-31.basil`); reporting usage at job start rather than job end; billing for provider-confirmed cost vs. internal clock estimate; NL quantity ambiguity causing unexpected costs (Pitfall 5)

### Phase 8: RunPodProvider, Hardening, and Pre-Launch Validation
**Rationale:** The second GPU provider implementation validates that the abstract interface is truly provider-agnostic. Hardening includes the full "Looks Done But Isn't" checklist from PITFALLS.md, which must pass before any public access.
**Delivers:** `RunPodProvider` concrete implementation (including the 30-minute result retention window and mandatory S3 copy before expiry); provider selection by `GPU_PROVIDER` env var; load testing against the SSE endpoint; chaos testing (intentional container kill, mid-dispatch kill, retry verification); full pre-flight PDB test suite against edge-case corpus (NMR ensemble, insertion codes, altloc, MSE, HETATM-heavy structures); data isolation verification (User A cannot access User B signed URLs); Stripe usage reconciliation against provider invoice for 10 test jobs
**Uses:** RunPod Python SDK 1.7.x, pytest-asyncio, structlog, Sentry SDK 2.x
**Avoids:** RunPod result data loss after 30-minute window; provider-specific logic leaking into Job Service; double-dispatch not caught by idempotency check

### Phase Ordering Rationale

- MockProvider (Phase 3) is the critical path enabler — it unblocks both agent (Phase 4) and job service (Phase 5) without live GPU, which compresses the build timeline
- PDB normalization and pre-flight validation are placed in Phase 4 (agent phase), not Phase 5 (job phase), because the wizard uses PDB data to populate its questions — normalization must precede wizard field population, not just dispatch
- Billing (Phase 7) intentionally comes after the job state machine (Phase 5) and frontend (Phase 6) are stable — this ensures Stripe usage reporting is wired to confirmed job completion events, not estimates
- Frontend (Phase 6) is built after the full API surface is stable, not iteratively alongside it — this avoids rebuilding the SSE consumer and wizard UI as the API shape changes

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 3 (GPU Provider Interface):** Modal's `spawn.aio()` async patterns and result retention model need validation against the specific tool Docker images; RunPod's 30-minute result window requires careful S3 copy timing to be designed up front
- **Phase 4 (Agent Service):** Claude Agent SDK session resumption API (`resume=session_id`) behavior at context window limits needs validation; the tool classification prompt needs testing against real scientist phrasing variants before the wizard question tree is built
- **Phase 7 (Billing):** Stripe Billing Meters API interaction with per-job cost caps and the zero-output refund policy workflow needs detailed design before implementation — billing disputes are expensive to handle post-launch

Phases with standard patterns (skip research-phase):
- **Phase 1 (Data Layer):** PostgreSQL + SQLAlchemy 2.0 + Alembic is a well-documented, established pattern
- **Phase 2 (Auth):** Supabase Auth + FastAPI JWT validation is thoroughly documented with official examples
- **Phase 5 (SSE):** Redis pub/sub + sse-starlette SSE pattern is well-documented; the poller loop is standard asyncio

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Core choices verified against official docs; version compatibility matrix confirmed; the only MEDIUM items are some library patch versions in the fast-moving ecosystem |
| Features | MEDIUM | Platform internals inferred from competitor docs and community sources; competitor feature sets observed rather than API-documented; LLM-to-workflow patterns from published research |
| Architecture | HIGH | All major patterns verified against Modal, RunPod, FastAPI, and Claude Agent SDK official docs; the SSE + Redis pub/sub pattern has community validation consistent with official docs |
| Pitfalls | MEDIUM-HIGH | Domain-specific pitfalls verified via official docs and GitHub issues; billing edge cases from Stripe docs; GPU cost patterns from RunPod/Modal documentation; BindCraft zero-output behavior from published benchmarks |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **BindCraft GPU memory requirements by design count and GPU tier:** The research references documented OOM thresholds but does not pin specific `num_designs` × GPU tier combinations. During Phase 3 (provider interface), calibrate cost and OOM thresholds with real BindCraft test runs before setting wizard defaults.
- **UniProt → PDB accession mapping strategy:** RCSB API returns multiple PDB entries for a single UniProt accession (many-to-many). The wizard must include a structure selection step when multiple PDB entries exist; the UX for this is not designed in the current research and should be scoped before Phase 4 implementation.
- **Claude Agent SDK session resumption at context limits:** The research notes that very long wizard conversations will eventually truncate context, but does not specify the handling strategy (summarization, truncation policy, max turns per session). This must be decided during Phase 4 design before implementation.
- **Stripe billing policy for partial job runs:** The research recommends charging on provider-confirmed GPU cost at job completion, but does not address the case where a job runs for 45 minutes, produces zero designs, and the user disputes. The exact refund policy and ToS language must be drafted and reviewed before Phase 7 billing integration.

## Sources

### Primary (HIGH confidence)
- Anthropic Models Overview — current model IDs (`claude-sonnet-4-6`), SDK version compatibility
- Claude Agent SDK Overview — session management, tool use, structured outputs
- Modal Job Queue docs — spawn/get patterns, 7-day result retention
- FastAPI official docs — async endpoints, BackgroundTasks limitations, middleware
- Stripe Billing Meters API docs — meter events, legacy Usage Records API removal date
- RunPod Serverless Job Operations — job states, result retention window, billing model
- SQLAlchemy 2.0 async docs — AsyncSession, asyncpg driver requirements
- Supabase RLS docs — row-level security for multi-tenant isolation
- Mol* official site and npm — current standard for web macromolecular visualization
- S3 Presigned URLs (AWS official) — access control pattern

### Secondary (MEDIUM confidence)
- Tamarind Bio platform and docs — competitor feature analysis
- BindCraft GitHub and Nature paper — tool capabilities, OOM thresholds, filter behavior
- Taskiq GitHub — FastAPI integration, Redis broker, throughput benchmarks vs. ARQ/Celery
- MAST taxonomy (March 2025) — multi-agent failure rates and misclassification causes
- Biopython PDB parser FAQ — edge case handling, warning system
- RunPod/Modal comparison (Northflank) — provider cost and performance tradeoffs
- Celery idempotency and late acknowledgment pitfalls — dispatch reliability patterns
- Stripe SaaS billing best practices — dispute handling, zero-output billing policy

### Tertiary (LOW confidence)
- LLM State Machine pattern (community GitHub example) — wizard-as-state-machine pattern is sound; specific implementation details vary
- SSE for agent streaming (community Medium article) — consistent with FastAPI docs; implementation specifics not independently verified

---
*Research completed: 2026-03-18*
*Ready for roadmap: yes*
