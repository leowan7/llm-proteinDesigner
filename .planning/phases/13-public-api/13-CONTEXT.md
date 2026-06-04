# Phase 13: Public API - Context

**Gathered:** 2026-06-04
**Status:** Ready for planning

<domain>
## Phase Boundary

REST API + API-key authentication + Python SDK for programmatic protein-design
job lifecycle (submit, status, list, cancel, download). All endpoints live under
`/api/v1/*` and reuse the existing org-tenancy (Phase 12), Stripe-metered billing
(Phase 3), payment-method + ToS gates (Phases 3, 10), rate-limit middleware
(Phase 5), and dispatch infrastructure (Phase 3 + Phase 4) without introducing
new product features. The SDK ships from its own PyPI repo and is treated as the
load-bearing contract surface for LIMS, notebook, and pipeline integrators.

</domain>

<decisions>
## Implementation Decisions

### API Key Model + Org Binding

- **D-01:** One API key = one organization. The key embeds `organization_id` at
  creation; calls do not need to send the `X-Org-Id` header. Multi-org users
  create one key per org. Mirrors Stripe/OpenAI org-key UX and avoids the
  X-Org-Id mistake class for headless consumers. Web flow keeps X-Org-Id.
- **D-02:** API key inherits the creator's org role at creation time
  (owner / scientist / viewer). Enforcement re-uses the existing
  `require_role(*allowed)` dependency from `backend/auth/org_dependencies.py`;
  the new `get_current_api_key` dep resolves to the same `(org_id, role)`
  shape that `get_active_org` returns so router code is unchanged. No new
  scope vocabulary in v1.
- **D-03:** Key format: `bw_live_<32-char-random>` (and `bw_test_<32-char-random>`
  for staging). Plaintext shown exactly once at creation; stored as bcrypt hash
  at rest (resists rainbow tables if the keys table leaks). Prefix is intentional
  so leaked keys are grep-able by GitHub secret scanning + Cloudflare WAF.
- **D-04:** Never-expire + manual revoke (Stripe-style). Each key row tracks
  `created_at`, `last_used_at`, `revoked_at`. Settings UI surfaces idle keys
  (no `last_used_at` in 30 days) for self-cleanup. No TTL — LIMS pipelines must
  not break on a missed reminder email.

### Endpoint Behavior + Idempotency

- **D-05:** `POST /api/v1/jobs` **requires** an `Idempotency-Key` header.
  Stripe-style: server stores `(api_key_id, idempotency_key) → (job_id, response_body, status_code)`
  for 24 hours and replays the same response on duplicate retries. This sits
  *above* the existing BILL-04 DB-first dispatch idempotency — BILL-04 prevents
  double GPU billing, the header prevents double job-row creation on network
  flakes. Missing header → 400 with a clear hint.
- **D-06:** `GET /api/v1/jobs` uses **cursor pagination** + filters.
  - Query params: `cursor=<opaque>`, `limit` (default 25, max 100), `status`,
    `tool`, `created_after` (ISO 8601), `created_before` (ISO 8601).
  - Cursor encodes `(created_at_desc, id_desc)` tiebreaker so newly-inserted
    jobs do not shift page boundaries.
  - Response includes `next_cursor` (null on last page).
- **D-07:** `GET /api/v1/jobs/{id}` returns **inline** job metadata + ranked
  candidate list + 24h presigned URLs for every output file and the design
  report in a single response. Matches the shape the webhook handler already
  produces — no new aggregation layer. URLs go stale after 24h; caller re-GETs
  to mint fresh URLs (no separate `/download-urls` endpoint).
- **D-08:** Real-time streaming is **poll-only** for v1. SDK helper
  `job.wait_until_complete(poll_every=30)` wraps the polling loop with sensible
  backoff. SSE re-exposure and outbound webhooks are deferred (see Deferred
  Ideas) — they are the natural Phase 14 scope.

### SDK Design + Branding

- **D-09:** PyPI package name: `bindwave`. `pip install bindwave`. Matches the
  current brand and the live domain (`bindwave.com`, `jobs@bindwave.com`,
  `app.bindwave.com`). **ROADMAP correction required:** Phase 13 SC 6 currently
  reads `pip install kendrew` — update during this phase, similar to how Phase
  11 corrected its SC 6 and SC 8 (see `11-CONTEXT.md` "ROADMAP Corrections
  Required").
- **D-10:** Both sync and async client surfaces. Public API:
  `from bindwave import Client, AsyncClient`. Default examples in docs and
  README use `Client` (data-scientist / notebook audience); `AsyncClient` is
  documented for pipeline / FastAPI integrators. Both share an httpx-based
  transport layer; ~one file delta between the two.
- **D-11:** Thin typed client + a small set of high-value helpers — NOT a CLI
  and NOT a thick framework. Helpers in scope for v1:
  - `client.jobs.submit(...)` → `Job`
  - `job.wait_until_complete(timeout=, poll_every=)` → terminal `Job`
  - `job.download_results(dest_dir=)` → list of local file paths (uses presigned URLs from D-07)
  - `client.jobs.iter_all(**filters)` → generator that auto-paginates (cursor from D-06)
  - `client.api_keys.list/revoke` (for self-managed automation)
- **D-12:** Hand-written SDK in a **separate** `bindwave-python` repo (not
  monorepo). Ergonomic types + helpers in the Anthropic/OpenAI style.
  Lives in its own GitHub repo with its own PyPI release workflow + tag-based
  versioning. The main repo (this one) ships a CI contract test that hits the
  OpenAPI spec and asserts every endpoint the SDK calls still exists with the
  expected request/response shape — kills SDK-drift before it reaches PyPI.

### OpenAPI Surface + Error Format

- **D-13:** Swagger UI only at `/api/docs` (FastAPI's built-in default).
  Single docs URL to link from the SDK README, marketing, and onboarding emails.
  ReDoc is not enabled in v1 — revisit if the spec grows enough that a
  reference-style view earns its keep.
- **D-14:** `/api/docs` is **public** (no auth gate). Stripe/OpenAI/GitHub
  pattern: the published spec IS the contract. Indexable by search; prospects
  can evaluate the API without an account. Safe because of D-15.
- **D-15:** Only `/api/v1/*` routes appear in the OpenAPI spec. Every other
  router (`/agent/`, `/sessions/`, `/admin/`, `/organizations/`, `/webhooks/`,
  `/user/`, `/auth/`, `/billing/`, `/jobs/` web-flow) gets
  `include_in_schema=False` on its `APIRouter`. The published spec IS the
  public-API surface; nothing in it can be accidentally deprecated.
- **D-16:** Error responses on `/api/v1/*` follow **RFC 7807** —
  `application/problem+json` with `{type, title, status, detail, instance, errors?}`.
  SDK exposes typed exception hierarchy (`BindwaveError` →
  `BindwaveAuthError` / `BindwaveRateLimitError` / `BindwaveValidationError` /
  `BindwaveJobError`). Existing web-flow routes keep their current error shape
  from `backend/jobs/errors.py` — the RFC 7807 translation is a thin
  exception handler scoped to the v1 routers only.

### Claude's Discretion

- Exact DB schema for `api_keys` table (columns: `id`, `organization_id`,
  `created_by_user_id`, `name`, `prefix`, `bcrypt_hash`, `role_at_creation`,
  `created_at`, `last_used_at`, `revoked_at`) — researcher confirms naming
  against existing migration patterns in `supabase/migrations/`.
- Idempotency storage backend (Redis vs Postgres table). Lean toward Redis
  with 24h TTL since BILL-04 already proves the dispatch path; Postgres if
  the researcher finds the existing rate-limit middleware already uses Postgres.
- Rate-limit response shape: `Retry-After` header (RFC standard) + RFC 7807
  body. Whether to add `X-RateLimit-Limit` / `X-RateLimit-Remaining` /
  `X-RateLimit-Reset` headers (GitHub/Stripe style) — recommend yes, but
  details go in the planner's hands.
- Cursor encoding scheme (base64-encoded JSON `{created_at, id}` vs opaque
  HMAC-signed) — researcher picks per security/observability tradeoff.
- `bindwave-python` repo bootstrap details: tooling (uv / hatch / poetry),
  test layout, docs site host. Recommend matching the Anthropic SDK layout
  (hatchling + ruff + mypy + GitHub Actions release workflow) unless the
  researcher finds reasons to diverge.
- Whether the contract test in D-12 lives in `backend/tests/contract/` or
  `.github/workflows/sdk-contract.yml` — planner's call.
- Exact OpenAPI title / description / contact / license metadata
  (`FastAPI(title="Bindwave API", ...)`) — copy tone from `bindwave.com`
  marketing.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase Dependencies + Roadmap
- `.planning/ROADMAP.md` §Phase 13 — public API goals + success criteria. **Note:** SC 6 says `pip install kendrew`; D-09 changes this to `bindwave` and the planner must produce a ROADMAP edit alongside the implementation plans (same pattern as Phase 11 SC 6/8).
- `.planning/REQUIREMENTS.md` — covers existing requirements; Phase 13 mainly satisfies the v2 `PLAT-V2-01` (REST API access for power users to submit jobs programmatically). New requirement IDs TBD by planner.

### Prior-Phase Context (load-bearing for D-01..D-16)
- `.planning/phases/03-job-execution-frontend-and-billing/03-CONTEXT.md` — dispatch idempotency (BILL-04), Stripe Billing Meters, payment gate, web-flow job router
- `.planning/phases/05-production-hardening/05-CONTEXT.md` — rate-limit middleware that the per-API-key limiter extends; `/health` deep check; structured logging; Sentry
- `.planning/phases/10-legal-and-compliance/10-CONTEXT.md` — ToS acceptance gate, subprocessor list, retention policy (90d default applies to API-created jobs too)
- `.planning/phases/11-deployment/11-CONTEXT.md` — `bindwave.com` brand cutover (drives D-09), `app.bindwave.com` API base URL, JWKS-based JWT verify, webhook dual-secret rotation
- `.planning/phases/12-teams-and-organizations/12-RESEARCH.md` + the org plans 12-01..12-06 — RLS policies, `require_role`, `get_active_org`, org-scoped Stripe customer, jobs.organization_id (D-01 piggy-backs on all of this)

### Existing Code + Config (must read before planning)
- `backend/main.py` — FastAPI app, router registration, lifespan; new `/api/v1/*` router groups mount here with `include_in_schema=True`; everything else flips to `False` per D-15
- `backend/auth/dependencies.py` — `get_current_user`; new `get_current_api_key` lives next to it
- `backend/auth/org_dependencies.py` — `get_active_org`, `require_role` factory; the API-key dep returns a compatible `(org_id, role)` tuple per D-02
- `backend/auth/jwks.py` — Supabase ECC JWKS verify; reference for crypto primitives, but API keys do not go through JWKS
- `backend/jobs/router.py` — web-flow job endpoints; `/api/v1/jobs/*` is a parallel router that delegates into the same service layer
- `backend/jobs/service.py` + `backend/jobs/dispatch.py` — dispatch + cancel + download already org-scoped; `/api/v1/*` re-uses them unchanged
- `backend/jobs/errors.py` — current web-flow error shape; the new `/api/v1/*` exception handler translates these into RFC 7807 problem+json per D-16
- `backend/jobs/sse.py` (if present in `router.py`) — out of scope for v1 (D-08), but referenced as the path forward in Phase 14
- `backend/user/router.py` — current settings endpoints; API-key CRUD lives here (`/user/api-keys` web routes, mirrored on `/api/v1/api-keys` for self-management)
- `backend/billing/stripe_client.py` — `get_or_create_customer` resolves Stripe customer from `organization_id`; works identically for API-submitted jobs
- `backend/organizations/router.py` — invitation flow context for D-01 (multi-org users create one key per org)
- `backend/middleware/` — existing rate-limit middleware (Phase 5); per-API-key 60 rpm budget extends this
- `backend/config.py` — Pydantic Settings; add API-key bcrypt cost, idempotency TTL, OpenAPI metadata
- `backend/Dockerfile` + `backend/requirements.txt` — `bcrypt` is already present (Phase 1 auth); confirm before planner relies on it
- `supabase/migrations/` — naming convention `<yyyymmdd>NNNN_<slug>.sql`; new migration creates `api_keys` + `api_key_idempotency` (or Redis-equivalent decision per Claude's Discretion)
- `frontend/src/pages/SettingsPage.tsx` — add "API Keys" tab; current tabs are Account / Notifications / Billing / Privacy (Phase 10) — API Keys becomes the 5th
- `frontend/src/lib/api.ts` — X-Org-Id opt-out list (Phase 12); `/api/v1/api-keys` is unaffected because web flow continues to use the existing `/user/api-keys` web routes

### External Standards + Documentation
- RFC 7807 — Problem Details for HTTP APIs (https://datatracker.ietf.org/doc/html/rfc7807) — drives D-16
- Stripe API Keys + Idempotency docs — pattern reference for D-01, D-03, D-04, D-05
- OpenAI Python SDK structure — pattern reference for D-10, D-11
- Anthropic Python SDK structure — pattern reference for D-12 (hand-written, separate repo, hatch-based release)
- FastAPI Custom Documentation URLs / `include_in_schema` docs — reference for D-13, D-15
- httpx — sync + async transport (already in `requirements.txt` per Phase 3)

### New Repo (created during this phase)
- `bindwave-python/` — separate GitHub repo, owned by Ranomics. PyPI namespace `bindwave`. CI workflow publishes on tag push. Initial layout TBD by planner (see Claude's Discretion).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`require_role(*allowed)` from `backend/auth/org_dependencies.py`** — D-02 re-uses verbatim; API-key dep returns the same `(org_id, role)` shape that `get_active_org` produces, so handlers do not need to change.
- **`bcrypt` already in requirements** — Phase 1 auth uses it; D-03 hash-at-rest is additive, no new dep.
- **Existing rate-limit middleware (Phase 5)** — extends with a per-API-key bucket; 60 rpm is a one-line config in the limiter table.
- **`backend/jobs/service.py`** — `submit_job`, `get_job`, `list_jobs`, `cancel_job`, `download_results` already org-scoped after Phase 12; `/api/v1/*` is a thin router that delegates here.
- **Webhook handler shape (`backend/webhooks/router.py`)** — produces the exact candidate + presigned-URL payload D-07 needs; API GET endpoint can re-use the same serializer.
- **Stripe `get_or_create_customer(organization_id)`** — works for API-submitted jobs without modification.

### Established Patterns
- **DB-first dispatch (BILL-04)** — API layer sits above it; idempotency-key (D-05) replays the existing dispatch result rather than re-dispatching.
- **`X-Org-Id` opt-out list in `frontend/src/lib/api.ts`** — Phase 12 chose explicit list over allowlist. D-01 makes this list one route shorter for API keys (they never need the header).
- **`include_in_schema=False` on internal routers** — already used selectively; D-15 just makes it the default for everything except `/api/v1/*`.
- **`docs/runbook-*.md` rollout pattern (Phase 12)** — API-key launch follows the same gated-rollout pattern: feature flag + per-org allowlist before fully public.
- **Supabase migration filename convention** — `<yyyymmdd>NNNN_<slug>.sql`; new `api_keys` migration uses next date number.
- **ROADMAP correction in implementation plan** — Phase 11 corrected SC 6 and SC 8 inside its implementation plans; Phase 13 follows the same pattern for SC 6 (`kendrew` → `bindwave`).

### Integration Points
- **`backend/main.py`** — register `api_v1_router = APIRouter(prefix="/api/v1")` and include child routers (`jobs`, `api_keys`); set `include_in_schema=True` only on this router, `False` on existing routers per D-15.
- **`backend/auth/`** — new `api_key_dependencies.py` (or extend `dependencies.py`) implementing `get_current_api_key()` that adapts to the `(org_id, role)` tuple shape.
- **`backend/jobs/router.py`** — no edits; new `backend/api/v1/jobs.py` (or similar) mounts at `/api/v1/jobs` and delegates to `jobs/service.py`.
- **`frontend/src/pages/SettingsPage.tsx`** — adds 5th tab "API Keys" with create / list / revoke + last-used-at column; the create modal surfaces the plaintext key exactly once with a "copy to clipboard" gate.
- **Stripe Billing Meters** — no change; API-submitted jobs already write `organization_id` and flow through the existing meter.
- **GitHub Actions** — new `.github/workflows/sdk-contract.yml` (or test under `backend/tests/contract/`) wires the OpenAPI-to-SDK contract test from D-12. Existing `test.yml` Phase 9 gates run unchanged.

</code_context>

<specifics>
## Specific Ideas

- Key prefix: `bw_live_` and `bw_test_` (lowercase, underscore-separated, easy to grep). Do NOT use `pk_`, `sk_`, or any Stripe-lookalike prefix.
- Idempotency-Key window: 24 hours. Same as presigned URL expiry — one mental model for the integrator.
- Cursor pagination defaults: `limit=25`, max `100`. Returns `{data: [...], next_cursor: "..."}` (null on last page).
- Error envelope (RFC 7807, on `/api/v1/*` only):
  ```json
  { "type": "https://bindwave.com/errors/job-not-found",
    "title": "Job not found",
    "status": 404,
    "detail": "Job 'job_01ABCDEF' does not exist or is not in this organization",
    "instance": "/api/v1/jobs/job_01ABCDEF" }
  ```
- SDK example in README must show: install, set API key via env (`BINDWAVE_API_KEY`), submit + wait + download in <15 lines.
- Settings UI: API key plaintext shown once in a modal with copy-to-clipboard and an "I have saved this key" confirmation before close. Mirrors the GitHub PAT UX.

</specifics>

<deferred>
## Deferred Ideas

- **Outbound webhooks (caller registers a URL; server POSTs job-completed/failed events with HMAC sig)** — best UX for LIMS pipelines but introduces an outbound HTTP infrastructure surface, retry queue, signature verification on caller side. Substantial scope. **Park for Phase 14.**
- **SSE re-exposure under `/api/v1/jobs/{id}/stream`** — useful for live Jupyter notebooks watching a single job, but HTTP/1.1 proxy/timeout concerns make it lower priority than webhooks. **Pairs naturally with Phase 14.**
- **CLI (`bindwave jobs submit ...`)** — doubles documentation, test surface, and PyPI release surface. Defer until a real shell-user request appears.
- **Finer-grained scopes (`jobs:read`, `jobs:write`, `billing:read`)** — D-02 inherits the org role for v1; revisit if API-only "service accounts" are requested.
- **Per-key TTL / rotation reminders** — D-04 chose never-expire; revisit if a SOC2 / enterprise customer requires it.
- **Per-API-key spend in admin dashboard** — admin already has revenue overview (Phase 7); per-key breakdown is a small extension when it earns its keep.
- **OpenAPI versioning policy for breaking changes (v2 path strategy)** — `/api/v1/` is locked but the v2 governance doc is post-launch concern.
- **Auth-gated `/api/docs` on staging** — D-14 chose public everywhere; revisit only if leak risk appears.
- **ReDoc at `/api/redoc`** — D-13 chose Swagger-only; trivial to add later if the spec grows.

</deferred>

---

*Phase: 13-public-api*
*Context gathered: 2026-06-04*
