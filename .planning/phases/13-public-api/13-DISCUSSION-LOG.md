# Phase 13: Public API - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-04
**Phase:** 13-public-api
**Areas discussed:** API key model + org binding, Endpoint behavior + idempotency, SDK design + branding, OpenAPI surface + error format

---

## API key model + org binding

### Q1 — Tenancy binding

| Option | Description | Selected |
|--------|-------------|----------|
| One key = one org | Key embeds org_id at creation; calls don't need X-Org-Id header. Mirrors Stripe/OpenAI org keys. Multi-org users create one key per org. | ✓ |
| User-scoped + X-Org-Id required | Mirrors the web frontend (Phase 12). One key works across all orgs the user belongs to; every call must send X-Org-Id. | |
| Both — key declares default org, X-Org-Id can override | Hybrid; most flexible, most surface area to test. | |

**User's choice:** One key = one org (Recommended)
**Notes:** Cleanest mental model for LIMS/pipeline integrators; avoids the X-Org-Id mistake class for headless consumers.

### Q2 — Permission scopes

| Option | Description | Selected |
|--------|-------------|----------|
| Inherit creator's org role only | API key acts as the user-role tuple at creation time. Re-uses require_role enforcement. | ✓ |
| Coarse scopes: read-only vs submit | Add a `scopes` field with values from {`jobs:read`, `jobs:write`, `billing:read`}. | |
| Fine-grained per-endpoint scopes | Each endpoint declares required scope; key carries explicit list. | |

**User's choice:** Inherit creator's org role only (Recommended)
**Notes:** No new scope vocabulary in v1; finer-grained scopes parked as a Deferred Idea.

### Q3 — Key format + storage

| Option | Description | Selected |
|--------|-------------|----------|
| Prefix-tagged + bcrypt | `bw_live_<32-char-random>` / `bw_test_*`. Shown once at creation. Bcrypt at rest. Prefix → secret-scanning grep-able. | ✓ |
| Prefix-tagged + SHA-256 | Same prefix scheme but cheaper hash. | |
| Opaque random + SHA-256 (no prefix) | Plain random token, no human-readable prefix. | |

**User's choice:** Prefix-tagged + bcrypt (Recommended)
**Notes:** Bcrypt resists rainbow tables if hashes leak; prefix enables GitHub secret-scanning + Cloudflare WAF detection.

### Q4 — Rotation / expiry

| Option | Description | Selected |
|--------|-------------|----------|
| Never-expire + manual revoke | Stripe/OpenAI-style. last_used_at tracked; settings shows idle keys for self-cleanup. | ✓ |
| Default 90-day TTL + reminder emails | Forces hygiene but breaks unattended pipelines if users miss the email. | |
| Per-key choice at creation | Creator picks `expires` (30d / 90d / 365d / never). | |

**User's choice:** Never-expire + manual revoke (Recommended)
**Notes:** LIMS pipelines must not break on a missed reminder; per-key TTL deferred until SOC2/enterprise demand appears.

---

## Endpoint behavior + idempotency

### Q1 — POST /api/v1/jobs idempotency

| Option | Description | Selected |
|--------|-------------|----------|
| Required Idempotency-Key header | Caller MUST supply Idempotency-Key (Stripe-style); server stores key+response for 24h. | ✓ |
| Optional Idempotency-Key | Header honored if present, ignored if missing. | |
| No idempotency — rely on dispatch DB-first | BILL-04 already prevents double-billing on retry. | |

**User's choice:** Required Idempotency-Key header (Recommended)
**Notes:** Sits above BILL-04 — prevents double job-row creation on network flakes that BILL-04's GPU-layer idempotency does not catch.

### Q2 — GET /api/v1/jobs pagination + filtering

| Option | Description | Selected |
|--------|-------------|----------|
| Cursor pagination + ?status/?tool/?since | `?cursor=<opaque>&limit=50` (default 25, max 100); filters: status, tool, created_after/before. | ✓ |
| Offset pagination + filters | `?offset=0&limit=50` + same filters. | |
| No pagination — last 50 by default + filters | Single page bounded by ?limit. | |

**User's choice:** Cursor pagination + ?status/?tool/?since (Recommended)
**Notes:** Cursor encodes (created_at, id) tiebreaker so insertions do not shift pages — critical when LIMS pipelines submit constantly.

### Q3 — Result delivery shape

| Option | Description | Selected |
|--------|-------------|----------|
| Inline metadata + presigned URLs in response | Single GET returns job + ranked candidate list + 24h presigned URLs. | ✓ |
| Split: GET /jobs/{id} + GET /jobs/{id}/files for URLs | Status response is small; download URLs fetched separately. | |
| Inline metadata + on-demand POST /jobs/{id}/download-urls | Caller POSTs to mint fresh URLs (configurable expiry). | |

**User's choice:** Inline metadata + presigned URLs in response (Recommended)
**Notes:** Matches the webhook handler's existing payload shape; URLs go stale after 24h and caller re-GETs.

### Q4 — Real-time updates

| Option | Description | Selected |
|--------|-------------|----------|
| Poll-only for v1 | Caller polls GET /api/v1/jobs/{id} every 30-60s. SDK helper wraps the poll loop. | ✓ |
| Re-expose SSE under /api/v1/jobs/{id}/stream | Bearer-auth wrapper over the existing SSE endpoint. | |
| Outbound webhooks (caller registers a URL) | Server POSTs job-completed/failed events with HMAC sig. | |

**User's choice:** Poll-only for v1 (Recommended)
**Notes:** SSE re-exposure and outbound webhooks both deferred as the natural Phase 14 scope.

---

## SDK design + branding

### Q1 — PyPI package name

| Option | Description | Selected |
|--------|-------------|----------|
| bindwave | Matches current brand and the live domain. `pip install bindwave`. Requires ROADMAP SC 6 update. | ✓ |
| kendrew (per ROADMAP) | Keeps the ROADMAP wording. Brand drift from the post-Phase-11 Bindwave rename. | |
| Other / let me name it | Suggest your own name. | |

**User's choice:** bindwave (Recommended)
**Notes:** ROADMAP Phase 13 SC 6 needs correction during implementation, same pattern as Phase 11 SC 6/8.

### Q2 — Sync vs async client surface

| Option | Description | Selected |
|--------|-------------|----------|
| Both — Client + AsyncClient | Default `from bindwave import Client` is sync; `from bindwave import AsyncClient` for async. Mirrors Anthropic/OpenAI/Stripe SDKs. | ✓ |
| Sync-only | Single Client class using httpx sync. | |
| Async-only | httpx.AsyncClient only. | |

**User's choice:** Both — Client + AsyncClient (Recommended)
**Notes:** ~one file delta between the two; aligns with the data-scientist target audience while keeping the async option open for pipelines.

### Q3 — Convenience layer depth

| Option | Description | Selected |
|--------|-------------|----------|
| Thin client + a few high-value helpers | Typed methods 1:1 with endpoints, PLUS wait_until_complete, download_results, iter_all auto-paginator. | ✓ |
| Thin typed client only | Pure 1:1 wrapper, no extras. | |
| Thick — client + helpers + CLI | Add a `bindwave` CLI entry point. | |

**User's choice:** Thin client + a few high-value helpers (Recommended)
**Notes:** Covers 80% of LIMS/notebook use case without going full framework; CLI deferred.

### Q4 — Generation strategy + repo location

| Option | Description | Selected |
|--------|-------------|----------|
| Hand-written, separate `bindwave-python` repo | Ergonomic hand-written types + helpers. Lives in its own repo for clean PyPI release pipeline + community PRs. Contract test asserts SDK matches backend OpenAPI. | ✓ |
| Hand-written, monorepo `sdk/python/` | Same code style, but lives in the main repo under `sdk/python/`. | |
| OpenAPI-generator from spec | Auto-generate client from the FastAPI OpenAPI spec on each release. | |

**User's choice:** Hand-written, separate `bindwave-python` repo (Recommended)
**Notes:** Anthropic/OpenAI pattern. Contract test in this repo kills SDK-drift before it reaches PyPI.

---

## OpenAPI surface + error format

### Q1 — Documentation UI

| Option | Description | Selected |
|--------|-------------|----------|
| Swagger UI only | FastAPI's built-in default at /api/docs. Try-it-now interactive. | ✓ |
| Both Swagger + ReDoc | Swagger interactive + ReDoc reference style. Two URLs to keep in sync. | |
| ReDoc only | Cleaner reference style; no try-it-now. | |

**User's choice:** Swagger UI only (Recommended)
**Notes:** Single docs URL to link from SDK README + marketing + onboarding emails; ReDoc revisitable if the spec grows.

### Q2 — Docs auth gate

| Option | Description | Selected |
|--------|-------------|----------|
| Public | Stripe/OpenAI/GitHub-style. Indexable; the published spec IS the contract. | ✓ |
| Auth-gated (logged-in users only) | Forces login before viewing the spec. | |
| Public on prod, gated on staging | Two-config story. | |

**User's choice:** Public (Recommended)
**Notes:** Safe because of Q3 — only /api/v1/* routes appear in the spec.

### Q3 — Spec scope

| Option | Description | Selected |
|--------|-------------|----------|
| Only /api/v1/* (public-API routes) | Internal routers tagged include_in_schema=False. The published spec IS the public-API contract. | ✓ |
| All routes, internal ones marked deprecated/internal tag | Surfaces every route under a `tags=[internal]` group. | |
| Split: /api/docs shows v1, /api/internal-docs shows all | Two specs. Internal one gated. | |

**User's choice:** Only /api/v1/* (public-API routes) (Recommended)
**Notes:** Nothing in the published spec can be accidentally deprecated; internal routes stay out of consumer reach.

### Q4 — Error format

| Option | Description | Selected |
|--------|-------------|----------|
| RFC 7807 problem+json | `{type, title, status, detail, instance, errors?}` with `application/problem+json`. SDK exposes typed exception hierarchy. | ✓ |
| Custom JSON matching backend/jobs/errors.py | Reuse existing error shape; ties public API to internal shape. | |
| Custom JSON, but versioned with the API | Hand-rolled shape, explicitly carved out for /api/v1/*. | |

**User's choice:** RFC 7807 problem+json (Recommended)
**Notes:** Web-flow routes keep their existing error shape; RFC 7807 translation is a thin exception handler scoped to v1 routers only.

---

## Claude's Discretion

- DB schema column naming for the `api_keys` table (researcher confirms against existing migration patterns)
- Idempotency storage backend (Redis 24h TTL preferred; Postgres acceptable if rate-limit middleware already uses it)
- Rate-limit response headers (`X-RateLimit-Limit` / `Remaining` / `Reset`) — recommend GitHub/Stripe style, planner finalizes
- Cursor encoding (base64-JSON vs HMAC-signed) — researcher picks per security/observability tradeoff
- `bindwave-python` repo bootstrap (uv vs hatch vs poetry; test layout; docs host) — recommend Anthropic SDK layout unless researcher diverges
- Whether the contract test lives in `backend/tests/contract/` or `.github/workflows/sdk-contract.yml`
- OpenAPI metadata (`title`, `description`, `contact`, `license`) — copy tone from bindwave.com marketing

## Deferred Ideas

- Outbound webhooks (caller registers a URL; server POSTs job-completed/failed events with HMAC sig) — Phase 14
- SSE re-exposure under `/api/v1/jobs/{id}/stream` — pairs naturally with Phase 14
- CLI (`bindwave jobs submit ...`)
- Finer-grained scopes (`jobs:read`, `jobs:write`, `billing:read`)
- Per-key TTL / rotation reminders
- Per-API-key spend in admin dashboard
- OpenAPI versioning policy for breaking changes (v2 path strategy)
- Auth-gated `/api/docs` on staging
- ReDoc at `/api/redoc`
