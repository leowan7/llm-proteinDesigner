# Roadmap: LLM Protein Designer

## Overview

Four phases build the platform from the ground up: Phase 1 establishes the authenticated data layer, Phase 2 builds the Claude agent and PDB ingest pipeline that forms the product's core differentiation, Phase 3 wires GPU job dispatch, real-time monitoring, results delivery, and billing into a working end-to-end flow with a full frontend, and Phase 4 hardens the system for public access — second GPU provider, chaos testing, billing reconciliation, and pre-launch validation.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation** - PostgreSQL schema, object storage, authentication, and dev environment
- [ ] **Phase 2: Agent and Structure Input** - Claude wizard, PDB ingest pipeline, pre-flight validation, and GPU provider abstraction
- [ ] **Phase 3: Job Execution, Frontend, and Billing** - Async job dispatch, SSE monitoring, results delivery, frontend, and Stripe billing
- [ ] **Phase 4: Production Hardening** - RunPod provider, chaos testing, billing reconciliation, and pre-launch validation

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
**Plans**: TBD

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
**Plans**: TBD

### Phase 4: Production Hardening
**Goal**: The platform is safe to open to external users — second GPU provider is integrated, billing integrity is verified against real GPU costs, and the system survives failure scenarios without double-charging or data loss
**Depends on**: Phase 3
**Requirements**: (none — all v1 requirements covered in Phases 1-3; this phase validates and hardens the system built there)
**Success Criteria** (what must be TRUE):
  1. Switching the GPU_PROVIDER env var from Modal to RunPod causes jobs to dispatch and complete correctly with no changes to Job Service code
  2. A job dispatched twice (network-blip retry simulation) produces exactly one provider job and one billing event
  3. A stuck GPU container (no heartbeat for 10 minutes) is automatically killed and the job is marked failed; the user is not billed for the hung time beyond the watchdog threshold
  4. User A cannot access User B's PDB files or job results via presigned URL manipulation or direct DB queries
  5. Stripe usage events reconcile against the Modal/RunPod provider invoice within acceptable tolerance across 10 test jobs
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 3/4 | In Progress|  |
| 2. Agent and Structure Input | 0/TBD | Not started | - |
| 3. Job Execution, Frontend, and Billing | 0/TBD | Not started | - |
| 4. Production Hardening | 0/TBD | Not started | - |
