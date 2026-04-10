# Requirements: LLM Protein Designer

**Defined:** 2026-03-18
**Core Value:** A scientist should be able to go from "I want to design a binder for IL-6 receptor" to downloadable, scored PDB structures without writing a single config file.

## v1 Requirements

### Authentication

- [x] **AUTH-01**: User can create an account with email and password
- [x] **AUTH-02**: User receives email verification link after signup and must verify before accessing the app
- [x] **AUTH-03**: User can reset password via email link
- [x] **AUTH-04**: User session persists across browser refresh

### Structure Input

- [x] **INPUT-01**: User can upload a PDB file as the target structure for a design job
- [x] **INPUT-02**: User can provide a PDB accession ID; system fetches the structure from RCSB automatically
- [x] **INPUT-03**: User can provide a UniProt accession ID; system resolves it to a PDB structure and fetches it
- [x] **INPUT-04**: User can describe a target in natural language only (e.g. "IL-6 receptor"); agent identifies and fetches the canonical structure
- [x] **INPUT-05**: System normalizes all uploaded/fetched PDB files before use (multi-model NMR, insertion codes, altloc atoms, MSE residues handled)

### Agent & Wizard

- [x] **AGENT-01**: Agent classifies user's design intent from natural language (binder design, de novo backbone, motif scaffolding)
- [x] **AGENT-02**: Agent recommends the appropriate tool (RFdiffusion, BindCraft, or Boltzgen) with a plain-language rationale; user must explicitly confirm before proceeding
- [x] **AGENT-03**: Agent runs a guided wizard to collect required parameters (chain length, number of designs, binding site/hotspot residues, etc.) before launching the job
- [x] **AGENT-04**: Agent performs pre-flight validation on inputs before dispatching: PDB quality check, hotspot feasibility, parameter sanity
- [x] **AGENT-05**: Agent surfaces validation warnings to the user and requires acknowledgment before continuing if issues are found

### Job Management

- [x] **JOB-01**: User can see real-time job status updates in the UI (queued, running, complete, failed) without refreshing the page
- [x] **JOB-02**: User receives an email notification when a job completes or fails
- [x] **JOB-03**: User can cancel a running job; GPU billing stops on cancellation

### Results

- [x] **RESULT-01**: Completed job returns all output PDB structures as downloadable files, ranked by design score
- [x] **RESULT-02**: Completed job returns a design report: run parameters, scoring metrics, ranked candidate summary
- [x] **RESULT-03**: Agent provides post-job next-step guidance (e.g. suggested AlphaFold validation, expression system, synthesis considerations)

### Billing

- [x] **BILL-01**: User is charged per job on completion, priced by actual GPU compute time (Stripe Billing Meters API)
- [x] **BILL-02**: User sees an estimated GPU cost before confirming job launch
- [x] **BILL-03**: User must have a valid payment method on file before launching any job
- [x] **BILL-04**: Job state is written to the database before any GPU provider API call (prevents double-billing on retry)

## v2 Requirements

### Results

- **RESULT-V2-01**: Interactive Mol* 3D structure viewer embedded in-browser for completed jobs
- **RESULT-V2-02**: Side-by-side comparison view of multiple design candidates

### Authentication

- **AUTH-V2-01**: OAuth login via Google or GitHub

### Job Management

- **JOB-V2-01**: Job history page listing all past jobs with status, parameters, and results

### Billing

- **BILL-V2-01**: Per-user optional spending cap to prevent unexpected charges
- **BILL-V2-02**: Defined refund/credit policy for zero-output runs (BindCraft returns 0 designs when filters are strict; documented behavior, not a bug)

### Platform

- **PLAT-V2-01**: REST API access for power users to submit jobs programmatically
- **PLAT-V2-02**: Shared workspaces / team accounts

### Testing & CI/CD

- **TEST-01**: Backend unit test coverage >80% line coverage across all modules
- **TEST-02**: Backend integration tests with real Supabase test instance
- **TEST-03**: Frontend unit tests (Vitest) for API client, utility functions, and page component smoke tests
- **TEST-04**: Frontend E2E tests (Playwright) covering auth, chat, jobs, and settings flows
- **TEST-05**: CI pipeline (GitHub Actions) with 4 gates on every PR: backend tests, frontend tests, E2E, lint+typecheck
- **TEST-06**: Docker image CI builds on merge to main via GitHub Actions
- **TEST-07**: Post-deploy smoke test workflow verifying health, auth, frontend load, and response time

## Out of Scope

| Feature | Reason |
|---------|--------|
| Mobile-native app | Web-first; responsive sufficient for v1 |
| Self-hosted GPU (user's own clusters) | Infra complexity out of scope for v1 |
| AlphaFold2/AF3 as design tool | Structure prediction, not design; may appear as a validation suggestion only |
| Real-time collaboration / shared workspaces | Deferred to v2 |
| Sequence-only input without structure source | Scientifically ambiguous; all three tools require structure context |
| Auto-retry on job failure | Masks config errors, burns GPU budget without user consent |
| Full streaming job logs | High infra complexity, low user value vs status + email notification |
| Streaming LLM responses mid-wizard | Adds frontend complexity; wizard is dialog-based, not chat-streaming |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| AUTH-01 | Phase 1 | Complete |
| AUTH-02 | Phase 1 | Complete |
| AUTH-03 | Phase 1 | Complete |
| AUTH-04 | Phase 1 | Complete |
| INPUT-01 | Phase 2 | Complete |
| INPUT-02 | Phase 2 | Complete |
| INPUT-03 | Phase 2 | Complete |
| INPUT-04 | Phase 2 | Complete |
| INPUT-05 | Phase 2 | Complete |
| AGENT-01 | Phase 2 | Complete |
| AGENT-02 | Phase 2 | Complete |
| AGENT-03 | Phase 2 | Complete |
| AGENT-04 | Phase 2 | Complete |
| AGENT-05 | Phase 2 | Complete |
| JOB-01 | Phase 3 | Complete |
| JOB-02 | Phase 3 | Complete |
| JOB-03 | Phase 3 | Complete |
| RESULT-01 | Phase 3 | Complete |
| RESULT-02 | Phase 3 | Complete |
| RESULT-03 | Phase 3 | Complete |
| BILL-01 | Phase 3 | Complete |
| BILL-02 | Phase 3 | Complete |
| BILL-03 | Phase 3 | Complete |
| BILL-04 | Phase 3 | Complete |
| TEST-01 | Phase 9 | Planned |
| TEST-02 | Phase 9 | Planned |
| TEST-03 | Phase 9 | Planned |
| TEST-04 | Phase 9 | Planned |
| TEST-05 | Phase 9 | Planned |
| TEST-06 | Phase 9 | Planned |
| TEST-07 | Phase 9 | Planned |

**Coverage:**
- v1 requirements: 24 total (+7 testing)
- Mapped to phases: 31
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-18*
*Last updated: 2026-03-18 after roadmap creation*
