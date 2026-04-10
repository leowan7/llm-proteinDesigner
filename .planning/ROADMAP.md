# Roadmap: LLM Protein Designer

## Overview

Thirteen phases build the platform from prototype to commercial SaaS. Phases 1-3 build the core platform (auth, agent, job dispatch, frontend, billing). Phase 4 validates all 5 design pipelines with real GPU runs. Phase 5 hardens security, idempotency, and billing. Phases 6-7 add UI polish and admin dashboard (parallel). Phase 8 adds post-run analysis. Phase 9 adds testing and CI/CD. Phase 10 adds legal/compliance for biopharma procurement. Phase 11 deploys to production. Phase 12 adds teams/organizations for enterprise sales. Phase 13 adds a public API for programmatic access.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation** - PostgreSQL schema, object storage, authentication, and dev environment
- [x] **Phase 2: Agent and Structure Input** - Claude wizard, PDB ingest pipeline, pre-flight validation, and GPU provider abstraction (completed 2026-03-19)
- [ ] **Phase 3: Job Execution, Frontend, and Billing** - Async job dispatch, SSE monitoring, results delivery, frontend, and Stripe billing
- [ ] **Phase 4: Pipeline Validation** - End-to-end GPU validation of all 5 design tools with real targets
- [ ] **Phase 5: Production Hardening** - Security, idempotency, billing reconciliation, monitoring, observability
- [x] **Phase 6: UI Improvements** - Sidebar navigation, session persistence, job history, user settings, onboarding, accessibility (completed 2026-04-09)
- [ ] **Phase 7: Admin Dashboard** - Admin auth, user management, job monitoring, revenue overview, system health, audit log
- [ ] **Phase 8: Post-Run Analysis Agent** - Agent-assisted result analysis, candidate ranking, shortlisting, and report generation
- [ ] **Phase 9: Testing & CI/CD** - Automated test suite (unit, integration, E2E), CI pipeline, pre-deploy gates
- [ ] **Phase 10: Legal & Compliance** - ToS, privacy policy, GDPR/CCPA, data retention, cookie consent, biopharma procurement readiness
- [ ] **Phase 11: Deployment** - Vercel, Railway, Supabase Cloud, Upstash, R2, custom domain, SSL
- [ ] **Phase 12: Teams & Organizations** - Multi-user accounts, team billing, shared job history, role-based access (admin/scientist/viewer)
- [ ] **Phase 13: Public API** - REST API with API keys for programmatic job submission, status polling, result download

## Phase Details

### Phase 1: Foundation
**Goal**: Users can create accounts and the data layer is ready to support all subsequent feature work
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04
**Success Criteria** (what must be TRUE):
  1. User can create an account with email and password, receive a verification email, and access the app only after clicking the verification link
  2. User can log in and remain logged in across browser refresh without re-authenticating
  3. User can request a password reset and complete it via an email link
  4. PostgreSQL schema (users, jobs tables), Redis, and Cloudflare R2 with per-user key structure are running in the local dev environment via Docker Compose
**Plans**: 4 plans
Plans:
- [ ] 01-01-PLAN.md — Dev environment scaffold (Supabase, Docker Compose, migrations, seed, config)
- [ ] 01-02-PLAN.md — FastAPI backend auth (endpoints, JWT validation, CSRF, CORS, tests)
- [ ] 01-03-PLAN.md — Frontend scaffold (Vite + React + shadcn, dark theme, AuthLayout, API client)
- [ ] 01-04-PLAN.md — Auth screens and end-to-end verification (6 screens, routing, human verification)

### Phase 2: Agent and Structure Input
**Goal**: A scientist can describe a protein design goal in natural language, provide a target structure by any supported method, and receive a validated JobSpec ready to dispatch — without touching a config file
**Depends on**: Phase 1
**Requirements**: INPUT-01, INPUT-02, INPUT-03, INPUT-04, INPUT-05, AGENT-01, AGENT-02, AGENT-03, AGENT-04, AGENT-05
**Success Criteria** (what must be TRUE):
  1. User can upload a PDB file, enter a PDB accession ID, or enter a UniProt accession ID, and the system normalizes the structure in all three cases (multi-model NMR, insertion codes, altloc atoms, MSE residues handled without user intervention)
  2. User can describe a target in natural language (e.g., "IL-6 receptor") and the agent resolves and fetches the canonical structure automatically
  3. Agent classifies the user's design intent, recommends the correct tool (RFdiffusion, BindCraft, or Boltzgen) with a plain-language rationale, and requires explicit user confirmation before proceeding
  4. Agent guides the user through a wizard that collects all required parameters; user sees a plain-language summary of what the agent understood before confirming
  5. Agent surfaces pre-flight validation warnings (PDB quality, hotspot feasibility, parameter sanity) and requires user acknowledgment before allowing dispatch to proceed
**Plans**: 5 plans
Plans:
- [ ] 02-01-PLAN.md — Type contracts, DB migration, config, and test scaffolds
- [ ] 02-02-PLAN.md — PDB pipeline backend (fetch, normalize, validate, router)
- [ ] 02-03-PLAN.md — Agent backend (tools, session, SSE router, system prompt)
- [ ] 02-04-PLAN.md — Frontend chat UI (ChatPage, components, agent API client)
- [ ] 02-05-PLAN.md — Integration wiring and end-to-end verification

### Phase 3: Job Execution, Frontend, and Billing
**Goal**: A scientist can launch a validated job, watch its status in real time, download ranked PDB structures and a design report, and be charged accurately for GPU compute consumed
**Depends on**: Phase 2
**Requirements**: JOB-01, JOB-02, JOB-03, RESULT-01, RESULT-02, RESULT-03, BILL-01, BILL-02, BILL-03, BILL-04
**Success Criteria** (what must be TRUE):
  1. User sees real-time job status (queued, running, complete, failed) update in the browser without refreshing the page, and receives an email notification on job completion or failure
  2. User can cancel a running job from the UI and GPU billing stops on cancellation
  3. User can download all output PDB files ranked by design score and view the design report (run parameters, scoring metrics, ranked candidate summary)
  4. Agent provides post-job next-step guidance (AlphaFold validation recommendation, synthesis considerations) on the results page
  5. User sees a GPU cost estimate before confirming job launch; payment method is required before any job dispatches; Stripe is charged the actual GPU compute cost at confirmed job completion; job state is persisted to the database before any GPU provider API call
**Plans**: 5 plans
Plans:
- [ ] 03-01-PLAN.md — Foundation: DB migration, config, GPUProvider ABC, type contracts, test scaffolds
- [ ] 03-02-PLAN.md — Billing backend: Stripe client, cost estimation, payment gate, billing router
- [ ] 03-03-PLAN.md — Job execution backend: worker, dispatch, webhooks, job router, notifications
- [ ] 03-04-PLAN.md — Frontend: JobPage, all job components, SSE client, route registration
- [ ] 03-05-PLAN.md — Integration wiring: chat-to-job launch flow, end-to-end human verification

### Phase 4: Pipeline Validation
**Goal**: Every design tool advertised on the platform actually works end-to-end — from PDB input through GPU execution to ranked output files. Validated with real targets, not mocked backends.
**Depends on**: Phase 3
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05, PIPE-06, PIPE-07
**Success Criteria** (what must be TRUE):
  1. RFdiffusion: 100-design pilot completes on a test target (e.g., IL-6R). Output contains poly-glycine backbone PDBs. ProteinMPNN sequence design runs on the output. AF2 validation produces ipTM scores. End-to-end in <1 hour on A100.
  2. BindCraft (FreeBindCraft fork): 10-design pilot completes. Output contains ranked sequences with ipTM, pLDDT, RMSD, shape complementarity scores. All 4 stages (hallucination, MPNN, AF2 monomer, interface analysis) execute without error.
  3. RFantibody: 100-design VHH pilot completes. CDR-H1/H2/H3 loops generated on a framework template. AbMPNN sequences assigned. RF2 antibody validation scores produced.
  4. BoltzGen: 100-design `protein-anything` pilot completes. YAML config parses correctly. BoltzIF inverse folding and Boltz-2 refolding stages execute. Quality-diversity filtering produces ranked candidates.
  5. PXDesign: 100-design basic-mode pilot completes. ProteinMPNN sequence design runs on output backbones. AF2-IG filtering produces confidence scores.
  6. Each tool's output files are correctly uploaded to R2, parsed into candidate cards, and displayed on the job results page.
  7. Docker images / RunPod templates for all 5 tools are built, tested, and version-pinned.
**Plans**: 7 plans
Plans:
- [ ] 04-01-PLAN.md — Infrastructure: pipelines module (config generators + result parsers), PXDesign in JobSpec/config, worker presigned URL + timeout fixes
- [ ] 04-02-PLAN.md — RFdiffusion Docker image + RunPod handler + validation run
- [ ] 04-03-PLAN.md — BindCraft Docker image + RunPod handler + validation run
- [ ] 04-04-PLAN.md — RFantibody Docker image + RunPod handler + validation run
- [ ] 04-05-PLAN.md — BoltzGen Docker image + RunPod handler + validation run
- [ ] 04-06-PLAN.md — PXDesign Docker image + RunPod handler + validation run
- [ ] 04-07-PLAN.md — Result aggregation: wire parsers into webhook handler, end-to-end frontend verification

### Phase 5: Production Hardening
**Goal**: The platform is safe to open to external users — billing integrity is verified against real GPU costs, and the system survives failure scenarios without double-charging or data loss.
**Depends on**: Phase 4
**Requirements**: (none — all v1 requirements covered in Phases 1-3; this phase validates and hardens the system built there)
**Success Criteria** (what must be TRUE):
  1. A job dispatched twice (network-blip retry simulation) produces exactly one provider job and one billing event
  2. A stuck GPU container (no heartbeat for 10 minutes) is automatically killed and the job is marked failed; the user is not billed for the hung time beyond the watchdog threshold
  3. User A cannot access User B's PDB files or job results via presigned URL manipulation or direct DB queries
  4. Stripe usage events reconcile against the RunPod provider invoice within acceptable tolerance across 10 test jobs
  5. Rate limiting, input validation, and OWASP top 10 protections in place
**Plans**: 5 plans
Plans:
- [x] 05-01-PLAN.md — Rate limiting, health check hardening, Sentry backend, structured logging
- [x] 05-02-PLAN.md — Billing idempotency key, webhook replay protection, terminal-state guard
- [x] 05-03-PLAN.md — Container heartbeat endpoint, stale job watchdog, live progress SSE
- [x] 05-04-PLAN.md — On-demand upload URLs with job token auth, presigned URL security
- [x] 05-05-PLAN.md — Sentry frontend, GPU spend alerting, SSE limiter, input validation

### Phase 6: UI Improvements
**Goal**: The platform feels like a polished SaaS product — persistent sessions, navigable history, user settings, and WCAG 2.2 AA accessibility for biopharma procurement
**Depends on**: Phase 3
**Requirements**: UI-01, UI-02, UI-03, UI-04, UI-05, UI-06
**Success Criteria** (what must be TRUE):
  1. User conversations persist across page refreshes and browser sessions; user can resume any previous conversation from the sidebar
  2. Collapsible left sidebar provides navigation between chat, job history, and settings
  3. User can view all past jobs in a filterable table at /jobs with status, cost, dates, and download links
  4. User settings page allows notification preferences, password change, and billing management (via Stripe Customer Portal)
  5. First-run onboarding presents clickable example prompts in the greeting card; no tooltip tours
  6. All interactive components pass WCAG 2.2 AA audit (keyboard navigation, aria-live for SSE updates, color contrast)
  7. Help/docs page (/docs) with tool descriptions, parameter explanations, result interpretation guide, and FAQ — written for the target audience (biopharma scientists), not generic
  8. Resources page (/resources) with links to original publications for each tool, benchmark data, example use cases, and video walkthroughs
**Plans**: 5 plans
Plans:
- [x] 06-01-PLAN.md — Session persistence backend (DB migration, session CRUD, agent router migration)
- [x] 06-02-PLAN.md — Supporting backend APIs (jobs list, user usage, payment method, notification prefs)
- [x] 06-03-PLAN.md — App shell restructure (sidebar, header, layout, routing, ChatPage refactor)
- [x] 06-04-PLAN.md — Job history page and enhanced GreetingCard onboarding
- [x] 06-05-PLAN.md — Settings page and WCAG 2.2 AA accessibility audit
**Research**: .planning/research/UI-FEATURES.md

### Phase 7: Admin Dashboard
**Goal**: Platform operator has full visibility into users, jobs, revenue, and system health through a custom admin dashboard at /admin
**Depends on**: Phase 3
**Requirements**: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6
**Success Criteria** (what must be TRUE):
  1. Admin can view all users with signup date, last login, payment status, and job count
  2. Admin can view all jobs across all users with status, tool, GPU time, cost, and error details; can cancel stuck jobs
  3. Revenue overview shows total GPU revenue, cost-of-goods (GPU spend), and margin — sourced from jobs table (Stripe excludes metered billing from MRR)
  4. System health page shows GPU queue depth, worker status, API error rates, and storage usage
  5. Admin auth uses is_admin column on users table with get_current_admin dependency; separate from user auth
  6. All admin actions are recorded in an audit log table
**Plans**: 5 plans
Plans:
- [x] 07-01-PLAN.md — Admin backend: DB migration, auth dependency, admin router (all endpoints), audit logging, shared cancel service
- [x] 07-02-PLAN.md — Admin backend tests: dependency tests, router endpoint tests, cancel service tests
- [x] 07-03-PLAN.md — Admin frontend foundation: AdminLayout, API client, AdminUsersPage, AdminJobsPage
- [x] 07-04-PLAN.md — Admin frontend completion: AdminRevenuePage (Recharts), AdminSystemPage, AdminAuditPage
- [x] 07-05-PLAN.md — Schema push, admin bootstrap, and end-to-end human verification
**Research**: .planning/research/ADMIN-DASHBOARD.md

### Phase 8: Post-Run Analysis Agent
**Goal**: After a design job completes, the agent assists the scientist in analyzing results — ranking candidates, explaining metrics, identifying the best designs to order for experimental validation, and suggesting next steps.
**Depends on**: Phase 4 (needs real output data to analyze)
**Requirements**: ANA-01, ANA-02, ANA-03, ANA-04, ANA-05, ANA-06, ANA-07, ANA-08, ANA-09, ANA-10, ANA-11, ANA-12
**Success Criteria** (what must be TRUE):
  1. Agent can load completed job results (candidate PDBs, scoring metrics CSV) into conversation context and answer questions about them.
  2. Agent can rank candidates by user-specified criteria (e.g., "show me the top 10 by ipTM", "which designs have the best shape complementarity?", "filter for pLDDT > 85 and SAP < 4").
  3. Agent can explain what each metric means in the context of the user's specific design (not generic definitions — e.g., "your top candidate has an ipTM of 0.89, which is strong evidence of a well-formed interface, but the SAP score of 5.2 suggests aggregation risk — consider testing solubility").
  4. Agent can compare candidates across multiple metrics and recommend a shortlist (5-20 designs) for experimental validation with reasoning.
  5. Agent provides actionable next-step guidance: recommended expression system, purification strategy, binding assay (SPR vs BLI), counter-screen suggestions, and whether a yeast display library is warranted.
  6. Agent can identify red flags: designs that look good on ipTM but have high clash scores, low shape complementarity, or sequence similarity to known allergens/immunogens.
  7. Agent can generate a summary report (downloadable) with the shortlisted candidates, their metrics, and the rationale for selection.
**Plans**: 4 plans
Plans:
- [x] 08-01-PLAN.md — Analysis tools (load_job_results, analyze_candidates, flag_red_flags), ranking engine, metric/guidance profiles, system prompt
- [x] 08-02-PLAN.md — BioPython PDB structural features (BSA, clash score, interface contacts)
- [x] 08-03-PLAN.md — Report generation (PDF/CSV/Markdown), refolding job submission tool
- [x] 08-04-PLAN.md — Router wiring, Export Report button, end-to-end verification
**Research**: .planning/phases/08-post-run-analysis-agent/08-RESEARCH.md

### Phase 9: Testing & CI/CD
**Goal**: Automated test coverage and a CI pipeline that prevents regressions from reaching production.
**Depends on**: Phase 5
**Requirements**: (new)
**Success Criteria** (what must be TRUE):
  1. Unit tests cover all backend modules (agent tools, billing, job dispatch, PDB utils, auth) with >80% line coverage
  2. Integration tests verify the full agent conversation flow (resolve -> classify -> collect -> validate -> launch) against a test database
  3. Frontend E2E tests (Playwright) cover: login, chat flow, structure card interaction, job launch, job status page
  4. CI pipeline (GitHub Actions) runs all tests on every PR; blocks merge on failure
  5. Pre-deploy smoke test hits production health endpoint after deploy and rolls back automatically on failure
**Plans**: TBD

### Phase 10: Legal & Compliance
**Goal**: Platform meets legal requirements for commercial operation and biopharma procurement. Scientists at regulated companies can get internal approval to use the platform.
**Depends on**: Phase 5
**Requirements**: (new)
**Success Criteria** (what must be TRUE):
  1. Terms of Service published and accepted on signup — covers IP ownership (user retains all rights to designs), data handling, liability limitations, acceptable use
  2. Privacy Policy published — GDPR and CCPA compliant, covers what data is collected (PDB uploads, job specs, usage metrics), retention periods, deletion rights
  3. Cookie consent banner implemented (platform uses HTTP-only auth cookies — minimal but must be disclosed)
  4. User can request full data export (GDPR Article 20) and account deletion (GDPR Article 17) from settings
  5. Data retention policy: uploaded PDB files and job results auto-expire after configurable period (default 90 days); user notified before deletion
  6. No user-uploaded structures are used for model training or shared with third parties — explicitly stated in ToS
  7. Subprocessor list documented (Supabase, Cloudflare, RunPod, Stripe, Anthropic) for enterprise procurement due diligence
**Plans**: TBD

### Phase 11: Deployment
**Goal**: Platform deployed to production infrastructure and accessible to external users at a custom domain.
**Depends on**: Phase 9, Phase 10
**Success Criteria** (what must be TRUE):
  1. Frontend deployed on Vercel with custom domain and SSL
  2. Backend + worker deployed on Railway as Docker containers with auto-deploy from main branch
  3. Database on Supabase Cloud (Pro plan) with connection pooling and backups
  4. Redis on Upstash with TLS
  5. Object storage on Cloudflare R2 with presigned URL access
  6. GPU jobs dispatch to RunPod from production backend
  7. Environment variables and secrets managed via platform-native secret stores (not .env files)
  8. Monitoring: Sentry for errors, uptime monitoring with PagerDuty/Opsgenie alerting
  9. Rollback possible within 5 minutes via Railway/Vercel deploy history
**Plans**: TBD

### Phase 12: Teams & Organizations
**Goal**: Biopharma teams can use the platform under a shared organization with centralized billing and role-based access. This is how you sell to companies, not individuals.
**Depends on**: Phase 11 (post-launch feature)
**Requirements**: (new)
**Success Criteria** (what must be TRUE):
  1. User can create an organization and invite team members by email
  2. Organization roles: owner (billing + admin), scientist (run jobs, view all org jobs), viewer (read-only)
  3. All jobs within an organization are visible to all org members (not siloed per user)
  4. Organization-level billing: one Stripe subscription, one invoice, usage aggregated across all members
  5. Owner can remove members and transfer ownership
  6. User can belong to multiple organizations and switch between them
**Plans**: TBD

### Phase 13: Public API
**Goal**: Computational biologists can submit jobs, check status, and download results programmatically — enabling integration into automated pipelines and LIMS systems.
**Depends on**: Phase 11 (post-launch feature)
**Requirements**: (new)
**Success Criteria** (what must be TRUE):
  1. REST API with API key authentication (separate from session-based web auth)
  2. Endpoints: POST /api/v1/jobs (submit), GET /api/v1/jobs/{id} (status + results), GET /api/v1/jobs (list), POST /api/v1/jobs/{id}/cancel
  3. API keys managed in user settings — create, revoke, view usage
  4. Rate limiting: 60 requests/minute per API key
  5. OpenAPI/Swagger documentation auto-generated and hosted at /api/docs
  6. Python SDK published to PyPI: `pip install kendrew` with typed client
**Plans**: TBD

## Progress

**Execution Order (two parallel tracks):**

```
Track A — GPU/Docker (long-running, background):
  4-02 RFdiffusion → 4-03 BindCraft → 4-04 RFantibody → 4-05 BoltzGen → 4-06 PXDesign → 4-07 Result wiring
  Then: 8 Post-Run Analysis (needs real GPU output data)

Track B — Ship the app (no GPU dependency):
  5 Production Hardening → 6 UI Improvements → 9 Testing & CI/CD → 10 Legal → 11 Deployment (launch)
  Then: 7 Admin Dashboard (post-launch, not blocking)

Post-launch growth: 12 Teams, 13 Public API
```

Track A and Track B run in parallel. The app can launch (Track B) with GPU jobs
showing "queued" — the moment Docker images work (Track A), jobs light up with
no code changes. Phase 8 (Post-Run Analysis) is the only feature that requires
real GPU output and cannot be mocked.

**Track A — GPU/Docker pipeline:**

| Phase | Description | Plans | Status | Depends on |
|-------|-------------|-------|--------|------------|
| 4. Pipeline Validation | Docker images + real GPU runs | 2/7 | In Progress | Track A only |
| 8. Post-Run Analysis | AI result analysis, candidate ranking | 0/4 | Planned | Phase 4 (needs real data) |

**Track B — Ship the app (critical path to launch):**

| Phase | Description | Plans | Status | Depends on |
|-------|-------------|-------|--------|------------|
| 5. Production Hardening | Security, idempotency, billing | 5/5 | Done |  |
| 6. UI Improvements | Sidebar, sessions, settings | 5/5 | Done | Phase 3 (done) |
| 7. Admin Dashboard | User/job/revenue monitoring | 0/5 | Next | Phase 3 (done) |
| 9. Testing & CI/CD | Tests, GitHub Actions, Docker CI | 0/TBD | Not started | Phase 5 |
| 10. Legal & Compliance | ToS, privacy, GDPR | 0/TBD | Not started | Phase 5 |
| 11. Deployment | Vercel, Railway, Supabase Cloud | 0/TBD | Not started | Phases 9, 10 |

**Post-launch:**

| Phase | Description | Plans | Status | Depends on |
|-------|-------------|-------|--------|------------|
| 12. Teams & Organizations | Multi-user, org billing, RBAC | 0/TBD | Not started | Phase 11 |
| 13. Public API | REST API, API keys, Python SDK | 0/TBD | Not started | Phase 11 |

**Completed:**

| Phase | Description | Plans | Completed |
|-------|-------------|-------|-----------|
| 1. Foundation | Auth, DB, dev env | 4/4 | Done |
| 2. Agent + Structure Input | Agent, PDB pipeline | 5/5 | 2026-03-19 |
| 3. Jobs, Frontend, Billing | Job dispatch, UI, Stripe | 5/5 | Done (cost estimate deferred) |

---

## Backlog

Items deferred from completed phases. Promote to a future phase when prioritized.

| ID | Origin | Description | Priority |
|----|--------|-------------|----------|
| 999.1 | Phase 7 (SC-4) | API error rate tracking in admin System page — roadmap mentions "API error rates" but discuss-phase scoped to liveness checks only. Add error rate metrics (5xx count, p99 latency) to /admin/system endpoint and SystemPage. | Low |
