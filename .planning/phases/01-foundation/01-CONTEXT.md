# Phase 1: Foundation - Context

**Gathered:** 2026-03-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Set up the authenticated data layer that all subsequent phases build on: Supabase project (PostgreSQL schema + auth), Redis, and MinIO (local R2 substitute) running in the dev environment. Users can create accounts, verify email, log in, and stay logged in across browser refresh. No application features — just the foundation.

</domain>

<decisions>
## Implementation Decisions

### Auth implementation
- **Supabase Auth** handles email/password authentication, email verification, and password reset — all built-in, no custom implementation needed
- **Supabase Postgres** is the primary database — single Supabase project handles both auth and application data (users table, jobs table)
- FastAPI validates Supabase-issued JWTs **locally** using python-jose with the Supabase JWT secret (read from env) — no network round-trip per request
- Local dev runs via **`supabase start`** (Supabase CLI), which spins up local Postgres, Auth, and Inbucket containers internally — no separate Postgres container in docker-compose.yml

### Session mechanism
- Supabase tokens stored in **HTTP-only cookies** — not accessible to JavaScript, protected against XSS
- Access token auto-refresh handled silently by the **Supabase JS SDK** — user stays logged in transparently without manual refresh logic
- CSRF protection required (standard for cookie-based auth in FastAPI)

### Email delivery
- Production: **Resend** configured as the SMTP provider in Supabase dashboard settings
- Local dev: **Supabase Inbucket** (built-in email catcher, launched automatically by `supabase start`) — web UI accessible at `localhost:54324`, zero config

### Dev environment
- `supabase start` manages: local PostgreSQL, Supabase Auth (GoTrue), Inbucket
- **docker-compose.yml** manages the remaining services: FastAPI backend, **Redis** (job queue + pub-sub for GPU job dispatch and SSE status updates in Phase 3), **MinIO** (S3-compatible local substitute for Cloudflare R2)
- MinIO uses the same boto3 client as production R2 — only the endpoint URL env var changes between local and prod
- **Seed script** (`seed.sql` or Python script) creates a pre-verified test user and baseline schema rows on `supabase db reset`; runs automatically on local environment setup
- Frontend: **React + TypeScript** (Next.js or Vite) using the Supabase JS SDK for auth — TypeScript types generated from Supabase schema

### Claude's Discretion
- Per-user R2/MinIO key structure (exact prefix pattern, e.g. `users/{user_id}/...`)
- PostgreSQL schema column details beyond the required tables (users, jobs)
- CSRF implementation approach (double-submit cookie vs. synchronizer token)
- Exact seed data beyond the test user (sample jobs rows, etc.)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements are fully captured in decisions above. Key external documentation agents should consult at planning time:

### Supabase
- Supabase CLI local dev docs: https://supabase.com/docs/guides/cli/local-development
- Supabase Auth + FastAPI integration: https://supabase.com/docs/guides/auth/server-side/creating-a-client

### Requirements
- `.planning/REQUIREMENTS.md` — AUTH-01 through AUTH-04 define the acceptance criteria for this phase

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield project, no existing code

### Established Patterns
- None yet — Phase 1 establishes the patterns all subsequent phases follow

### Integration Points
- FastAPI backend connects to Supabase Postgres via the Supabase Python client or direct psycopg2/asyncpg connection string
- FastAPI reads Supabase JWT secret from env to validate tokens on every protected endpoint
- Redis connection string from env — same client used in Phase 3 for job queuing and SSE pub-sub
- MinIO endpoint URL from env — same boto3 client used in Phase 3 for R2 uploads; only env var changes in production

</code_context>

<specifics>
## Specific Ideas

- Dev environment should be a single `make dev` or `./scripts/dev-up.sh` that runs `supabase start` and `docker compose up` in sequence — developer should be fully running with one command
- Seed script should print the test user credentials on first run so developers don't have to look them up

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-foundation*
*Context gathered: 2026-03-18*
