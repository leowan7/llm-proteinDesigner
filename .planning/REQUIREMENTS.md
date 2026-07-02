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

### API (Phase 13)

- [x] **API-01**: API key creation returns plaintext exactly once; storage is HMAC-SHA256+pepper of the plaintext (D-03 hash-at-rest intent, see RESEARCH §2.10).
- [x] **API-02**: API keys carry one org_id at creation time; calls authenticate as that org with the creator's role-at-creation; no X-Org-Id header (D-01, D-02).
- [x] **API-03**: API keys never expire; user-initiated revoke flips `revoked_at`; revoked keys reject auth immediately (D-04).
- [x] **API-04**: `POST /api/v1/jobs` REQUIRES `Idempotency-Key` header; missing → 400; same key + same body within 24h replays the stored response byte-for-byte; same key + different body → 422 (D-05 + RESEARCH §2.9).
- [x] **API-05**: `GET /api/v1/jobs` paginates by opaque cursor encoding `(created_at, id)` tiebreaker; default limit 25, max 100; supports filters `status`, `tool`, `created_after`, `created_before` (D-06 + RESEARCH §2.3).
- [x] **API-06**: `GET /api/v1/jobs/{id}` returns inline metadata + ranked candidate list + 24h presigned URLs in one response (D-07).
- [x] **API-07**: All `/api/v1/*` responses on error use `application/problem+json` per RFC 7807; web-flow routes keep their existing error shape (D-16 + RESEARCH §2.7).
- [x] **API-08**: All `/api/v1/*` responses emit `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (unix epoch seconds) headers; 429 responses additionally emit `Retry-After` (seconds) (RESEARCH §2.4).
- [x] **API-09**: Only `/api/v1/*` routes appear in the OpenAPI spec; every other router (12 total) sets `include_in_schema=False`; an OpenAPI snapshot test guards the surface (D-15 + RESEARCH §2.8, §2.12).
- [x] **API-10**: Per-API-key rate limit: 60 requests/minute, keyed on `api_keys.id`; uses a separate slowapi `Limiter` instance with `headers_enabled=True` (Phase 13 SC 4 + RESEARCH §2.4).
- [x] **API-11**: OpenAPI docs at `/api/docs` are publicly accessible; the published spec IS the public-API contract (D-13, D-14).
- [x] **API-12**: Python SDK `bindwave` ships from `bindwave-python` repo with sync `Client` + async `AsyncClient`; published to PyPI on tag push; a backend pytest contract test asserts the spec covers every endpoint the SDK calls (D-09, D-10, D-12 + RESEARCH §2.5, §2.6).

### Organizations (Phase 12)

- [x] **ORG-01**: User can create an organization and invite team members by email (DB foundation 12-01; router + invites + notifications 12-02; frontend invite UI in 12-05; E2E coverage 12-06)
- [x] **ORG-02**: Organization roles owner / scientist / viewer with documented permission matrix (DB ENUM + RLS helpers 12-01; get_active_org + require_role backend enforcement 12-02; jobs/billing route gating in 12-03; E2E owner/non-owner billing gate in 12-06)
- [x] **ORG-03**: All jobs within an organization are visible to all org members (RLS policies laid in 12-01; jobs router cutover in 12-03; Launched-by column 12-05; E2E cross-user job visibility in 12-06)
- [x] **ORG-04**: Organization-level billing — one Stripe customer per org (column moved in 12-01; stripe_client cutover in 12-03; metadata stamping in 12-04; drop-column migration + verify gate in 12-06)
- [x] **ORG-05**: Owner can remove members and transfer ownership (last-owner trigger laid in 12-01; transfer + remove endpoints 12-02; frontend members UI 12-05; E2E transfer flow in 12-06)
- [x] **ORG-06**: User can belong to multiple organizations and switch between them (GET /organizations/mine + X-Org-Id resolver 12-02; frontend X-Org-Id wiring in 12-05; E2E switcher round-trip in 12-06)
- [x] **ORG-07**: Existing single-tenant users migrated without data loss (personal-org backfill in 12-01; verified via Plan 12-04 stamp script + drop migration gating in 12-06)
- [x] **ORG-08**: Last owner cannot leave an organization (DB-enforced trigger 12-01; route translation 12-02; E2E last-owner block in 12-06)

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

- **PLAT-V2-01**: REST API access for power users to submit jobs programmatically — Validated by Phase 13 (API-04..API-12)
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
| ORG-01 | Phase 12 | Validated (12-01 DB + 12-02 router + invitations + 12-05 UI + 12-06 E2E) |
| ORG-02 | Phase 12 | Validated (12-01 ENUM+helpers + 12-02 require_role + 12-03 route gating + 12-06 E2E billing gate) |
| ORG-03 | Phase 12 | Validated (12-01 RLS + 12-03 jobs cutover + 12-05 Launched-by column + 12-06 cross-user E2E) |
| ORG-04 | Phase 12 | Validated (12-01 column move + 12-03 stripe_client cutover + 12-04 metadata stamp + 12-06 drop-column migration) |
| ORG-05 | Phase 12 | Validated (12-01 last-owner trigger + 12-02 transfer/remove + 12-05 UI + 12-06 transfer E2E) |
| ORG-06 | Phase 12 | Validated (12-02 /mine + X-Org-Id resolver + 12-05 frontend wiring + 12-06 switcher E2E) |
| ORG-07 | Phase 12 | Validated (12-01 backfill + 12-04 Stripe metadata stamp + 12-06 drop-column gating) |
| ORG-08 | Phase 12 | Validated (12-01 trigger + 12-02 route translation + 12-06 last-owner E2E) |
| TEST-01 | Phase 9 | Planned |
| TEST-02 | Phase 9 | Planned |
| TEST-03 | Phase 9 | Planned |
| TEST-04 | Phase 9 | Planned |
| TEST-05 | Phase 9 | Planned |
| TEST-06 | Phase 9 | Planned |
| TEST-07 | Phase 9 | Planned |
| PLAT-V2-01 | Phase 13 | Validated |
| API-01 | Phase 13 | Validated |
| API-02 | Phase 13 | Validated |
| API-03 | Phase 13 | Validated |
| API-04 | Phase 13 | Validated |
| API-05 | Phase 13 | Validated |
| API-06 | Phase 13 | Validated |
| API-07 | Phase 13 | Validated |
| API-08 | Phase 13 | Validated |
| API-09 | Phase 13 | Validated |
| API-10 | Phase 13 | Validated |
| API-11 | Phase 13 | Validated |
| API-12 | Phase 13 | Validated |

**Coverage:**
- v1 requirements: 44 total (24 + 8 organizations + 12 API) plus 7 testing
- Mapped to phases: 51
- Unmapped: 0 ✓

All 12 API requirements validated; PLAT-V2-01 promoted from v2 to v1 + validated.
Phase 13 closed 2026-06-04 — verification record at .planning/phases/13-public-api/13-VERIFICATION.md

---
*Requirements defined: 2026-03-18*
*Last updated: 2026-06-04 — ORG-01..ORG-08 marked Validated after Plan 12-06 close-out (E2E spec + drop-column migration + rollout runbook). Phase 12 implementation complete; deployment gated by docs/runbook-phase-12-rollout.md.*
