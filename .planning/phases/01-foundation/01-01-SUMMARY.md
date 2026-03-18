---
phase: 01-foundation
plan: 01
subsystem: infra
tags: [supabase, postgres, docker, redis, minio, fastapi, pydantic, python]

# Dependency graph
requires: []
provides:
  - Local Supabase project (Postgres + Auth + Inbucket) with email verification enforced
  - PostgreSQL schema: public.users and public.jobs tables with RLS policies
  - Pre-verified test user (test@example.com / Password123!) seeded on db reset
  - Docker Compose stack: FastAPI backend + Redis + MinIO (no Postgres — owned by Supabase CLI)
  - Pydantic Settings config module with all env vars including database_url, supabase_jwt_secret, and testing flag
  - One-command dev startup via `make dev` (supabase start + docker compose up + MinIO bucket creation)
affects:
  - 01-02 (auth endpoints — depends on backend/config.py, Supabase local stack, and seed user)
  - 01-03 (frontend — depends on Supabase URL/anon key from .env.local)
  - all subsequent phases (all depend on this local dev stack)

# Tech tracking
tech-stack:
  added:
    - supabase CLI 2.81.3 (npx — local dev stack management)
    - FastAPI 0.135.1
    - uvicorn 0.42.0
    - PyJWT 2.12.1
    - starlette-csrf 3.0.0
    - supabase Python client 2.28.2
    - asyncpg 0.31.0
    - boto3 1.42.71
    - redis 7.3.0
    - python-dotenv 1.2.2
    - pydantic-settings 2.8.1
    - python-multipart 0.0.22
    - httpx 0.28.1
    - pytest 8.3.5 + pytest-asyncio 0.24.0
    - Docker Compose (Redis 7-alpine, MinIO minio/minio:latest)
  patterns:
    - Supabase CLI owns Postgres and Auth; docker-compose.yml owns only application-layer services
    - Pydantic BaseSettings reads from .env.local for all config; no hardcoded values
    - FastAPI as auth cookie setter (all auth routes through FastAPI, not Supabase directly)
    - Per-user S3 key structure: users/{user_id}/jobs/{job_id}/inputs|outputs/{filename}

key-files:
  created:
    - supabase/config.toml
    - supabase/migrations/20260318000000_init.sql
    - supabase/seed.sql
    - supabase/.gitignore
    - docker-compose.yml
    - .env.example
    - .gitignore
    - Makefile
    - scripts/dev-up.sh
    - backend/Dockerfile
    - backend/requirements.txt
    - backend/config.py
  modified: []

key-decisions:
  - "PyJWT over python-jose: python-jose is unmaintained with known CVEs; PyJWT 2.x is actively maintained and covers HS256 use case"
  - "supabase start owns Postgres on 54322; docker-compose.yml contains no Postgres service to avoid port conflicts"
  - "enable_confirmations = true in supabase/config.toml enforces AUTH-02 (email verification required before sign-in)"
  - "database_url and testing fields in backend/config.py: database_url allows Docker network override; testing enables CSRF bypass in test mode (Plan 01-02)"
  - "Direct SQL insert into auth.users in seed.sql with pgcrypto extension for local-only pre-verified test user"

patterns-established:
  - "Pattern: Supabase CLI owns Postgres/Auth; Docker Compose owns application services (FastAPI, Redis, MinIO)"
  - "Pattern: Pydantic BaseSettings with .env.local as source; all config via environment variables"
  - "Pattern: FastAPI as auth cookie setter — frontend calls FastAPI /auth/* endpoints, never Supabase Auth directly"

requirements-completed: [AUTH-01, AUTH-02, AUTH-04]

# Metrics
duration: 4min
completed: 2026-03-18
---

# Phase 01 Plan 01: Local Dev Environment Scaffold Summary

**Supabase local stack (Postgres + Auth + Inbucket) with email verification enforced, PostgreSQL RLS schema, pre-verified seed user, and Docker Compose for FastAPI + Redis + MinIO — all bootable with `make dev`**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-18T21:14:20Z
- **Completed:** 2026-03-18T21:18:19Z
- **Tasks:** 2
- **Files modified:** 12

## Accomplishments

- Supabase project initialized with `enable_confirmations = true` (AUTH-02) and Vite dev server as site_url
- PostgreSQL schema with `public.users` and `public.jobs` tables, both with Row Level Security policies restricting rows to authenticated owner
- Pre-verified test user seeded via raw SQL into `auth.users` using pgcrypto extension — bypasses email verification for local dev convenience
- Docker Compose with FastAPI + Redis 7-alpine + MinIO (no Postgres — Supabase CLI owns it) with single `make dev` entry point
- `backend/config.py` Pydantic Settings module with all required env vars, including `database_url` for Docker networking and `testing` flag for CSRF bypass in tests
- `scripts/dev-up.sh` handles full startup sequence: Supabase start, Docker Compose up, MinIO bucket creation (idempotent)

## Task Commits

1. **Task 1: Supabase project init, migration, seed, and config** - `40628a9` (chore)
2. **Task 2: Docker Compose, env files, backend Dockerfile, config module, Makefile, dev startup script** - `3b1f137` (chore)
3. **Supabase .gitignore (generated artifact)** - `23afc7f` (chore)

## Files Created/Modified

- `supabase/config.toml` - Supabase local dev configuration: site_url=localhost:5173, enable_confirmations=true, jwt_expiry=3600
- `supabase/migrations/20260318000000_init.sql` - users and jobs tables with RLS policies
- `supabase/seed.sql` - Pre-verified test@example.com user with pgcrypto password hashing
- `supabase/.gitignore` - Generated by supabase init; excludes .temp, .branches, env files
- `docker-compose.yml` - FastAPI + Redis + MinIO services with env_file reference
- `.env.example` - Template with all required variable names and placeholder values
- `.env.local` - Developer-local env file (gitignored); pre-populated from .env.example
- `.gitignore` - Root gitignore covering .env.local, __pycache__, node_modules, minio_data, supabase/.temp
- `Makefile` - dev/stop/reset targets; `make dev` calls scripts/dev-up.sh
- `scripts/dev-up.sh` - Full startup: supabase start, supabase status, docker compose up -d, MinIO bucket creation, credential display
- `backend/Dockerfile` - python:3.12-slim base with requirements install
- `backend/requirements.txt` - All pinned dependencies (fastapi, PyJWT, starlette-csrf, supabase, asyncpg, boto3, redis, etc.)
- `backend/config.py` - Pydantic BaseSettings with all env vars including database_url and testing flag

## Decisions Made

- **PyJWT over python-jose:** python-jose is effectively unmaintained (last meaningful release 2022, known CVEs). PyJWT 2.x is the actively maintained standard for HS256 JWT validation.
- **Supabase CLI owns Postgres:** docker-compose.yml contains no Postgres service. `supabase start` manages Postgres on port 54322. This is the correct architecture to avoid port conflicts.
- **email confirmation enforced:** `enable_confirmations = true` in config.toml — satisfies AUTH-02 requirement that users must verify email before accessing the app.
- **database_url and testing in config.py:** `database_url` allows overriding the Postgres DSN for Docker networking (container-to-container uses hostname, not 127.0.0.1). `testing = True` will be used in Plan 01-02 to bypass CSRF in test mode.
- **Raw SQL seed for local dev:** Direct `INSERT INTO auth.users` with pgcrypto for the test user is appropriate for local dev only. Production would use Supabase Admin API.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

After running `make dev` for the first time, the developer must copy the Supabase ANON KEY, SERVICE ROLE KEY, and JWT SECRET from the `supabase status` output into `.env.local`. The startup script prints these values and reminds the developer to copy them. No external service configuration required beyond this.

## Next Phase Readiness

- Full local dev stack infrastructure is in place for Plan 01-02 (FastAPI auth endpoints)
- `backend/config.py` exposes all required settings that auth endpoints will use (supabase_url, supabase_jwt_secret, csrf_secret, cookie_secure, cors_origins, testing)
- Pre-verified test user (test@example.com) available on `supabase db reset` for use in Plan 01-02 integration tests
- MinIO bucket `protein-designer` created idempotently on `make dev`

## Self-Check: PASSED

All 12 created files verified present. All 3 task commits verified in git log.

---
*Phase: 01-foundation*
*Completed: 2026-03-18*
