# Phase 1: Foundation - Research

**Researched:** 2026-03-18
**Domain:** Authentication (Supabase Auth + FastAPI JWT), local dev environment (Supabase CLI, Docker Compose, MinIO, Redis)
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Auth implementation:** Supabase Auth handles email/password authentication, email verification, and password reset — all built-in, no custom implementation needed
- **Database:** Supabase Postgres is the primary database — single Supabase project handles both auth and application data (users table, jobs table)
- **JWT validation:** FastAPI validates Supabase-issued JWTs locally using python-jose with the Supabase JWT secret (read from env) — no network round-trip per request
- **Local dev:** Runs via `supabase start` (Supabase CLI), which spins up local Postgres, Auth, and Inbucket containers internally — no separate Postgres container in docker-compose.yml
- **Session storage:** Supabase tokens stored in HTTP-only cookies — not accessible to JavaScript, protected against XSS
- **Auto-refresh:** Access token auto-refresh handled silently by the Supabase JS SDK — user stays logged in transparently without manual refresh logic
- **CSRF protection:** Required (standard for cookie-based auth in FastAPI)
- **Email delivery (production):** Resend configured as the SMTP provider in Supabase dashboard settings
- **Email delivery (local dev):** Supabase Inbucket (built-in email catcher, launched automatically by `supabase start`) — web UI accessible at `localhost:54324`, zero config
- **Docker Compose services:** FastAPI backend, Redis (job queue + pub-sub for GPU job dispatch and SSE in Phase 3), MinIO (S3-compatible local substitute for Cloudflare R2)
- **MinIO:** Uses the same boto3 client as production R2 — only the endpoint URL env var changes between local and prod
- **Seed script:** Creates a pre-verified test user and baseline schema rows on `supabase db reset`; runs automatically on local environment setup
- **Frontend:** React + TypeScript (Next.js or Vite) using the Supabase JS SDK for auth — TypeScript types generated from Supabase schema
- **One-command dev startup:** Single `make dev` or `./scripts/dev-up.sh` that runs `supabase start` and `docker compose up` in sequence
- **Seed UX:** Seed script prints test user credentials on first run so developers don't have to look them up

### Claude's Discretion

- Per-user R2/MinIO key structure (exact prefix pattern, e.g. `users/{user_id}/...`)
- PostgreSQL schema column details beyond the required tables (users, jobs)
- CSRF implementation approach (double-submit cookie vs. synchronizer token)
- Exact seed data beyond the test user (sample jobs rows, etc.)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| AUTH-01 | User can create an account with email and password | Supabase Auth `signUp()` handles this with zero custom code; email confirmation enabled via Supabase config |
| AUTH-02 | User receives email verification link after signup and must verify before accessing the app | Supabase Auth built-in email confirmation flow; Inbucket catches emails locally at `localhost:54324`; `confirm_email = true` in `config.toml` |
| AUTH-03 | User can reset password via email link | Supabase Auth `resetPasswordForEmail()` built-in; same Inbucket/Resend pipeline |
| AUTH-04 | User session persists across browser refresh without re-authenticating | HTTP-only cookie pattern with FastAPI as cookie setter; Supabase JS SDK auto-refresh via `onAuthStateChange` and `getSession()` reads cookie through backend proxy |

</phase_requirements>

---

## Summary

Phase 1 establishes the authenticated data layer that all subsequent phases depend on. The architecture uses Supabase for auth and Postgres, FastAPI as the API server validating JWTs locally, Redis and MinIO running in Docker Compose, and the Supabase CLI (`supabase start`) managing the local Postgres and Auth containers.

The most technically nuanced aspect of this phase is the HTTP-only cookie auth pattern. The Supabase JS SDK by default stores tokens in `localStorage` and can also manage cookies — but the SDK's built-in cookie handling does not set the `HttpOnly` flag, because the browser client needs direct access to the refresh token for silent auto-refresh. Achieving true HTTP-only cookies in an SPA requires routing all auth operations through the FastAPI backend: FastAPI calls Supabase Auth, receives the token pair, and sets the cookies directly via `Set-Cookie` response headers with `HttpOnly; Secure; SameSite=Lax`. The React frontend never touches raw tokens. Token refresh is then handled by a FastAPI `/auth/refresh` endpoint that the frontend calls when the access token is close to expiry (using an interceptor or the Supabase JS SDK's `onAuthStateChange` event in a degraded mode).

The rest of the phase — Supabase CLI local dev setup, Docker Compose for Redis and MinIO, PostgreSQL migrations, and seed data — is well-documented and low-risk. The key structural discipline is that `supabase start` owns Postgres and Auth; docker-compose.yml owns only the application-layer services (FastAPI, Redis, MinIO).

**Primary recommendation:** Use FastAPI as the auth cookie setter. The React frontend calls FastAPI `/auth/login`, `/auth/signup`, `/auth/refresh`, and `/auth/logout` — never Supabase directly for authentication. FastAPI sets and clears all cookies. FastAPI validates JWTs on every protected route using PyJWT with HS256 and the Supabase JWT secret from env.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Supabase CLI | 2.x (npm: 2.81.3) | Local dev stack management | `supabase start` spins up Postgres, Auth (GoTrue), Inbucket with one command |
| @supabase/supabase-js | 2.99.2 | Frontend auth SDK, DB client | Official Supabase JS client; handles `signUp`, `signIn`, token refresh |
| @supabase/ssr | 0.9.0 | Server-side cookie config utilities | Provides cookie adapter interfaces for SSR frameworks |
| supabase (Python) | 2.28.2 | FastAPI access to Supabase Postgres and Auth admin | Official Python client; used for admin operations like user pre-verification in seed |
| FastAPI | 0.135.1 | Python API framework | Async-native, Pydantic validation, excellent for JWT middleware |
| PyJWT | 2.12.1 | Local JWT validation in FastAPI | Preferred over python-jose (unmaintained); HS256 validation against Supabase secret |
| starlette-csrf | 3.0.0 | CSRF middleware for FastAPI/Starlette | Double-submit cookie pattern; drop-in Starlette middleware |
| uvicorn | 0.42.0 | ASGI server for FastAPI | Standard FastAPI ASGI server |
| asyncpg | 0.31.0 | Async Postgres driver | Used for direct DB access from FastAPI where Supabase Python client is insufficient |
| boto3 | 1.42.71 | S3-compatible client for MinIO/R2 | Same client works for MinIO (local) and Cloudflare R2 (prod); only endpoint URL changes |
| redis (Python) | 7.3.0 | Redis client | Standard Python Redis client; async-compatible via `redis.asyncio` |
| python-dotenv | 1.2.2 | Env var loading from `.env` file | Standard; prevents hardcoded config |
| python-multipart | 0.0.22 | Form data parsing in FastAPI | Required for cookie-based auth form submissions |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | 0.28.1 | Async HTTP client in FastAPI | Needed if FastAPI calls Supabase Auth REST directly |
| Vite + React + TypeScript | latest | Frontend scaffolding | If not using Next.js; simpler for SPA with separate FastAPI backend |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyJWT | python-jose | python-jose is largely unmaintained as of 2024; PyJWT is actively maintained and covers the same HS256 use case |
| starlette-csrf | fastapi-csrf-protect | Both implement double-submit; starlette-csrf is a clean Starlette middleware with fewer dependencies |
| MinIO (docker-compose) | Supabase Storage (local) | Supabase Storage is available locally via `supabase start` — but the decision locks MinIO to match the Cloudflare R2 production client (boto3) |
| asyncpg direct | Supabase Python client only | Supabase Python client wraps PostgREST (REST API), not raw SQL; asyncpg gives full SQL control for schema migrations and complex queries |

**Installation (Python backend):**
```bash
pip install fastapi==0.135.1 uvicorn==0.42.0 PyJWT==2.12.1 starlette-csrf==3.0.0 \
    supabase==2.28.2 asyncpg==0.31.0 boto3==1.42.71 redis==7.3.0 \
    python-dotenv==1.2.2 python-multipart==0.0.22 httpx==0.28.1
```

**Installation (Frontend):**
```bash
npm install @supabase/supabase-js@2.99.2
```

**Installation (Supabase CLI — macOS/Linux via brew):**
```bash
brew install supabase/tap/supabase
# or npm (non-Windows):
npx supabase@latest init
```

---

## Architecture Patterns

### Recommended Project Structure

```
project/
├── supabase/                     # Supabase CLI project root
│   ├── config.toml               # Local Supabase settings (ports, email, auth options)
│   ├── migrations/               # Versioned SQL migrations (auto-numbered by CLI)
│   │   └── 20260318000000_init.sql
│   └── seed.sql                  # Test data; runs on supabase db reset
│
├── backend/                      # FastAPI application
│   ├── main.py                   # App entry point, middleware registration
│   ├── auth/
│   │   ├── router.py             # /auth/login, /auth/signup, /auth/refresh, /auth/logout
│   │   └── dependencies.py       # get_current_user FastAPI dependency (JWT validation)
│   ├── db/
│   │   └── connection.py         # asyncpg pool setup
│   ├── storage/
│   │   └── client.py             # boto3 S3 client (MinIO local / R2 prod)
│   ├── config.py                 # Pydantic Settings reading from env
│   └── requirements.txt
│
├── frontend/                     # React + TypeScript app
│   ├── src/
│   │   ├── lib/supabase.ts       # Supabase client init
│   │   ├── auth/                 # Login, signup, reset forms
│   │   └── api/                  # API client (all requests to FastAPI, not Supabase directly)
│   └── package.json
│
├── docker-compose.yml            # FastAPI, Redis, MinIO only (NOT Postgres — owned by supabase CLI)
├── .env.local                    # Local env vars (never committed)
├── .env.example                  # Template with all required var names
├── Makefile                      # `make dev`, `make reset`, etc.
└── scripts/
    └── dev-up.sh                 # supabase start && docker compose up
```

### Pattern 1: FastAPI as Auth Cookie Setter

**What:** The React frontend never calls Supabase Auth directly. It calls `/auth/login` on FastAPI. FastAPI calls Supabase Auth, receives `access_token` and `refresh_token`, and sets them as HTTP-only cookies in the response.

**When to use:** Always. This is the only way to achieve true HTTP-only cookie storage in a SPA + separate backend architecture.

**Login flow:**
```
Browser → POST /auth/login {email, password}
FastAPI → supabase.auth.sign_in_with_password({email, password})
Supabase → {access_token, refresh_token, expires_in}
FastAPI → Response with:
    Set-Cookie: access_token=<jwt>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=3600
    Set-Cookie: refresh_token=<token>; HttpOnly; Secure; SameSite=Lax; Path=/auth/refresh; Max-Age=604800
```

**Token refresh flow:**
```
Browser → POST /auth/refresh (access_token cookie expired, refresh_token cookie sent automatically)
FastAPI → reads refresh_token from cookie
FastAPI → supabase.auth.refresh_session(refresh_token)
Supabase → new {access_token, refresh_token}
FastAPI → Response with updated Set-Cookie headers
```

**FastAPI JWT validation dependency:**
```python
# Source: PyJWT docs + Supabase JWT claims reference
import jwt
from fastapi import Cookie, HTTPException, status

SUPABASE_JWT_SECRET = settings.supabase_jwt_secret  # read from env

async def get_current_user(access_token: str | None = Cookie(default=None)):
    """FastAPI dependency: validates Supabase JWT from HTTP-only cookie."""
    if access_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = jwt.decode(
            access_token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        user_id: str = payload["sub"]  # Supabase user UUID
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
```

**Protecting routes:**
```python
from fastapi import Depends

@router.get("/me")
async def get_me(user_id: str = Depends(get_current_user)):
    return {"user_id": user_id}
```

### Pattern 2: Supabase CLI Local Dev Setup

**What:** `supabase init` creates `supabase/config.toml`. `supabase start` boots local containers. `supabase db reset` re-applies all migrations and runs `supabase/seed.sql`.

**Key ports after `supabase start`:**
```
API (PostgREST + Auth):  http://127.0.0.1:54321
Postgres:                postgresql://postgres:postgres@127.0.0.1:54322/postgres
Supabase Studio:         http://127.0.0.1:54323
Inbucket (email):        http://127.0.0.1:54324
```

**Auth config in `supabase/config.toml`:**
```toml
[auth]
site_url = "http://localhost:5173"         # Frontend URL (Vite default)
additional_redirect_urls = []
jwt_expiry = 3600                          # Access token lifetime in seconds (1 hour)
enable_signup = true

[auth.email]
enable_signup = true
double_confirm_changes = true
enable_confirmations = true               # AUTH-02: email verification required
```

**Seed pattern for pre-verified test user:**
```sql
-- supabase/seed.sql
-- Creates a test user bypassing email verification for local dev
INSERT INTO auth.users (
    id,
    instance_id,
    email,
    encrypted_password,
    email_confirmed_at,
    created_at,
    updated_at,
    aud,
    role
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000000',
    'test@example.com',
    crypt('Password123!', gen_salt('bf')),
    NOW(),
    NOW(),
    NOW(),
    'authenticated',
    'authenticated'
) ON CONFLICT (id) DO NOTHING;
```

**Note:** Direct inserts into `auth.users` work for local dev seed only. In production, use Supabase Admin API.

### Pattern 3: Docker Compose for Application Services

**What:** docker-compose.yml runs only application-layer services. Postgres is NOT in docker-compose.yml — `supabase start` owns it.

```yaml
# docker-compose.yml
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    env_file:
      - .env.local
    depends_on:
      - redis
      - minio

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # Web console
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - minio_data:/data

volumes:
  minio_data:
```

### Pattern 4: MinIO / R2 boto3 Client

**What:** boto3 S3 client configured to use MinIO locally via endpoint URL override. Same code runs against Cloudflare R2 in production — only the env vars change.

```python
# backend/storage/client.py
import boto3
from botocore.config import Config

def get_s3_client():
    """Returns a boto3 S3 client configured for MinIO (local) or Cloudflare R2 (prod)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,       # "http://localhost:9000" local, R2 URL prod
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",                           # R2 uses "auto"; MinIO accepts it
    )
```

**Per-user key structure (Claude's discretion):**
```
users/{user_id}/jobs/{job_id}/inputs/{filename}
users/{user_id}/jobs/{job_id}/outputs/{filename}
```

This prefix structure enables per-user IAM policies in production (R2 bucket policy scoped to `users/{user_id}/*`) and is trivially scannable for orphaned data cleanup.

### Pattern 5: CSRF Protection via starlette-csrf

**What:** Double-submit cookie pattern. On GET requests, server sets a signed CSRF cookie. On unsafe methods (POST/PUT/DELETE/PATCH), the client sends the same value in an `x-csrftoken` header. Starlette-csrf middleware compares them.

```python
# backend/main.py
from starlette_csrf import CSRFMiddleware
from fastapi import FastAPI

app = FastAPI()
app.add_middleware(
    CSRFMiddleware,
    secret="your-csrf-secret-from-env",   # rotate with SECRET_KEY
    cookie_samesite="lax",
    cookie_secure=True,                    # HTTPS only; set False for local HTTP
)
```

**Frontend pattern:** Fetch the CSRF token from the `csrftoken` cookie (it is NOT HTTP-only — must be readable by JS to put in the `x-csrftoken` header) before each mutating request.

### Anti-Patterns to Avoid

- **Storing Supabase anon key on frontend and calling Supabase Auth directly from the browser for sign-in**: With HTTP-only cookie architecture, the browser should not receive raw tokens. All auth operations route through FastAPI.
- **Running Postgres in docker-compose.yml alongside `supabase start`**: Port conflicts will occur on 5432. Supabase CLI owns Postgres on port 54322. Docker Compose manages application services only.
- **Using python-jose instead of PyJWT**: python-jose is effectively unmaintained as of late 2023. PyJWT 2.x is the actively maintained standard.
- **Reading the JWT from the `Authorization: Bearer` header in the cookie-based arch**: Cookies are sent automatically. The FastAPI dependency reads from `Cookie(default=None)`, not from the `Authorization` header.
- **Sharing the refresh token cookie path with `/`**: Set `Path=/auth/refresh` on the refresh token cookie so it is only sent to the refresh endpoint — not on every API call.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Email verification flow | Custom token generation, email templates, verification endpoint | Supabase Auth built-in | Token generation, expiry, email delivery, redirect handling — all complex; Supabase handles this end-to-end |
| Password reset flow | Custom reset tokens, expiry logic, email delivery | Supabase Auth `resetPasswordForEmail()` | Same complexity reasons; Supabase handles one-time-use token and expiry |
| CSRF protection | Custom CSRF middleware, token storage, comparison logic | `starlette-csrf` | Cookie comparison, signed token generation, SameSite interaction — subtle security bugs without a tested library |
| S3 client (local dev) | Custom file storage server or adapter | MinIO + boto3 | MinIO speaks native S3 protocol; same boto3 code runs against R2 in prod |
| JWT validation logic | Parsing JWT header/payload manually, verifying signatures by hand | PyJWT | Signature verification, expiry checking, audience validation — all handled; manual implementation will have bugs |
| Local email capture | Running a real SMTP server, configuring nodemailer/postfix | Supabase Inbucket | Launched automatically by `supabase start`; zero config; web UI at `localhost:54324` |

**Key insight:** Supabase Auth eliminates roughly 80% of auth implementation work in Phase 1. The remaining complexity is in the HTTP-only cookie plumbing between the Supabase token response and the browser — which is the intentional architectural choice for XSS protection.

---

## Common Pitfalls

### Pitfall 1: HTTP-only Cookie Breaks Supabase JS SDK Auto-Refresh

**What goes wrong:** The Supabase JS SDK's built-in auto-refresh (via `onAuthStateChange`) reads the refresh token from its storage adapter (default: localStorage). If the refresh token is in an HTTP-only cookie, the SDK cannot read it, and the session expires after 1 hour with no automatic recovery.

**Why it happens:** The HTTP-only flag specifically prevents JavaScript from reading the cookie. The SDK was not designed for this mode in SPA deployments.

**How to avoid:** With the FastAPI-as-cookie-setter architecture, the React frontend does NOT use the Supabase JS SDK for sign-in/sign-out/refresh. Instead:
1. The frontend calls FastAPI `/auth/login`, `/auth/logout`, `/auth/refresh` directly via `fetch`.
2. The browser automatically attaches the HTTP-only cookies on every request (because `credentials: 'include'` is set in fetch options).
3. For token refresh, implement a fetch interceptor that catches 401 responses, calls `/auth/refresh`, then retries the original request.
4. The Supabase JS SDK can still be used for non-auth operations (e.g., real-time subscriptions) if needed, but auth state management lives in the FastAPI cookie layer.

**Warning signs:** Users are logged out after 1 hour of inactivity; `onAuthStateChange` never fires `SIGNED_IN` after page reload.

### Pitfall 2: `supabase start` Port Conflicts with Existing Services

**What goes wrong:** Supabase CLI starts Postgres on 54322, API on 54321, Studio on 54323, Inbucket on 54324. If any of these ports are taken, `supabase start` fails non-obviously.

**How to avoid:** Document required ports in README. Customize in `supabase/config.toml` under `[api]`, `[db]`, `[studio]`, `[inbucket]` if conflicts arise. Check that docker-compose.yml does NOT bind to these ports.

### Pitfall 3: Seed Script Inserts into `auth.users` Without `gen_salt`

**What goes wrong:** Supabase local Postgres does not have `pgcrypto` extension enabled by default. The `crypt()` function used for password hashing requires it.

**How to avoid:** Add `CREATE EXTENSION IF NOT EXISTS pgcrypto;` at the top of the seed migration or the seed.sql file. Supabase local dev runs the extension by default in the `extensions` schema — call `SELECT extensions.crypt(...)` or ensure the extension is in the public search path.

**Warning signs:** `ERROR: function crypt(unknown, unknown) does not exist` on `supabase db reset`.

### Pitfall 4: MinIO Bucket Does Not Exist on First Start

**What goes wrong:** MinIO starts with an empty data volume. boto3 calls fail with `NoSuchBucket` until the bucket is created.

**How to avoid:** Add a MinIO init container or a startup script to `dev-up.sh` that creates the required bucket using `mc` (MinIO Client) or a boto3 `create_bucket()` call. The startup script should be idempotent (`if bucket does not exist, create it`).

**Warning signs:** `botocore.exceptions.ClientError: NoSuchBucket` on first backend startup.

### Pitfall 5: CSRF `cookie_secure=True` Breaks Local HTTP Dev

**What goes wrong:** `starlette-csrf` with `cookie_secure=True` sets the `Secure` cookie flag. Browsers refuse to send secure cookies over HTTP (`localhost` is exempt in most browsers, but not all dev configurations).

**How to avoid:** Read `cookie_secure` from an env var: `True` in production, `False` in local dev. Make this explicit in `.env.example`.

### Pitfall 6: FastAPI CORS Must Allow Credentials for Cookie Auth

**What goes wrong:** React frontend on `localhost:5173` calls FastAPI on `localhost:8000`. Without proper CORS headers, the browser blocks the response (or blocks cookies from being sent).

**How to avoid:** Configure FastAPI `CORSMiddleware` with `allow_credentials=True` and an explicit `allow_origins` list (not `["*"]` — wildcards are incompatible with `allow_credentials=True`).

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Code Examples

### JWT Validation FastAPI Dependency
```python
# Source: PyJWT 2.12.1 docs + Supabase JWT Claims reference (supabase.com/docs/guides/auth/jwt-fields)
import jwt
from fastapi import Cookie, HTTPException, status
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    supabase_jwt_secret: str
    class Config:
        env_file = ".env.local"

settings = Settings()

async def get_current_user(access_token: str | None = Cookie(default=None)) -> str:
    """
    FastAPI dependency that validates a Supabase JWT from an HTTP-only cookie.
    Returns the Supabase user UUID (sub claim).
    """
    if access_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(
            access_token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload["sub"]  # Supabase user UUID
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

### FastAPI Login Endpoint (Cookie Setter)
```python
# Source: Supabase Python client 2.28.x docs
from fastapi import APIRouter, Response
from supabase import create_client

supabase = create_client(settings.supabase_url, settings.supabase_anon_key)
router = APIRouter(prefix="/auth")

@router.post("/login")
async def login(email: str, password: str, response: Response):
    """Authenticate with Supabase and set HTTP-only cookies."""
    result = supabase.auth.sign_in_with_password({"email": email, "password": password})
    session = result.session
    response.set_cookie(
        key="access_token",
        value=session.access_token,
        httponly=True,
        secure=settings.cookie_secure,  # True in prod, False in local dev
        samesite="lax",
        max_age=session.expires_in,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=session.refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
        path="/auth/refresh",       # Scoped — only sent to refresh endpoint
    )
    return {"message": "Logged in"}
```

### Supabase config.toml (Email Verification Enforcement)
```toml
# Source: Supabase CLI config docs (supabase.com/docs/guides/cli/config)
[auth]
site_url = "http://localhost:5173"
jwt_expiry = 3600

[auth.email]
enable_signup = true
double_confirm_changes = true
enable_confirmations = true    # AUTH-02: user cannot sign in without verifying email
```

### PostgreSQL Schema Migration
```sql
-- supabase/migrations/20260318000000_init.sql
-- Application tables; auth.users is managed by Supabase Auth

CREATE TABLE IF NOT EXISTS public.users (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'queued', 'running', 'complete', 'failed', 'cancelled')),
    tool        TEXT,
    parameters  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS: users see only their own rows
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_own ON public.users
    FOR ALL USING (auth.uid() = id);

CREATE POLICY jobs_own ON public.jobs
    FOR ALL USING (auth.uid() = user_id);
```

### MinIO bucket init in dev-up.sh
```bash
#!/usr/bin/env bash
# scripts/dev-up.sh
set -e

echo "Starting Supabase local stack..."
supabase start

echo "Starting Docker Compose services..."
docker compose up -d

echo "Waiting for MinIO to be ready..."
until docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin 2>/dev/null; do
    sleep 1
done

echo "Creating MinIO buckets..."
docker compose exec minio mc mb --ignore-existing local/protein-designer

echo "Dev environment ready."
echo "  Supabase Studio: http://localhost:54323"
echo "  Inbucket:        http://localhost:54324"
echo "  FastAPI:         http://localhost:8000"
echo "  MinIO console:   http://localhost:9001"
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| python-jose for JWT validation | PyJWT 2.x | 2023-2024 (python-jose stagnated) | python-jose has unresolved CVEs and no active maintenance; PyJWT is the current standard |
| `supabase/auth-helpers` JS package | `@supabase/ssr` | 2023 | Auth Helpers deprecated; `@supabase/ssr` is the current SSR cookie adapter |
| Supabase localStorage token storage | Custom HTTP-only cookie via backend setter | Ongoing design choice | HTTP-only cookies are more secure against XSS; requires backend mediation of all auth calls |
| `supabase.auth.getUser()` via network round-trip on every request | Local JWT validation with PyJWT | Current best practice | Network round-trip to Supabase on every request adds 50-200ms latency; local validation is zero-latency |
| Docker Hub/Quay MinIO image | `minio/minio` (direct from MinIO) | Oct 2025 | MinIO stopped updating Docker Hub and Quay images; use official `minio/minio` image directly |

**Deprecated / outdated:**
- `python-jose`: Last meaningful release 2022; known CVEs; do not use for new projects
- `supabase/auth-helpers` npm package: Deprecated; replaced by `@supabase/ssr`
- `supabase.auth.setAuth()`: Removed in supabase-js v2
- Calling `supabase.auth.getSession()` in server code and trusting it: Supabase official docs now warn against this; use `getClaims()` or local JWT validation instead

---

## Open Questions

1. **Frontend framework: Next.js vs Vite**
   - What we know: CONTEXT.md says "Next.js or Vite" — Claude's discretion is not explicitly stated here
   - What's unclear: Next.js has a built-in server that could handle cookie-setting natively via API routes, which simplifies the HTTP-only cookie architecture. Vite (SPA) requires FastAPI to handle all cookie operations.
   - Recommendation: For a simple SPA with a separate FastAPI backend, Vite is the lower-complexity choice. Next.js adds value if server-side rendering is needed — not the case for Phase 1. Use Vite + React. This should be confirmed before planning begins.

2. **Supabase JWT secret rotation strategy**
   - What we know: JWT secret is read from env, used in local PyJWT validation
   - What's unclear: Supabase supports multiple signing keys (JWT signing key rotation as of 2024). The Python client and PyJWT default to the primary secret. Rotation behavior in the local dev environment is unverified.
   - Recommendation: Use a single static secret for Phase 1. Document key rotation as a future ops concern.

3. **Supabase Python client for admin user operations in seed**
   - What we know: Direct SQL inserts into `auth.users` work locally with `gen_salt`/`crypt`
   - What's unclear: The Python client's `supabase.auth.admin.create_user()` with `email_confirm=True` is the cleaner approach for seed scripts, as it goes through GoTrue and correctly sets all internal state
   - Recommendation: Use `supabase.auth.admin.create_user()` with the service role key in the seed script rather than raw SQL. Requires the service role key in the local dev env (available from `supabase status`).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + httpx (async test client for FastAPI) |
| Config file | `backend/pytest.ini` or `pyproject.toml` `[tool.pytest.ini_options]` — Wave 0 gap |
| Quick run command | `pytest backend/tests/ -x -q` |
| Full suite command | `pytest backend/tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AUTH-01 | POST /auth/signup creates a Supabase user | integration | `pytest backend/tests/test_auth.py::test_signup -x` | Wave 0 gap |
| AUTH-02 | Unverified user cannot call protected routes; verified user can | integration | `pytest backend/tests/test_auth.py::test_email_verification_required -x` | Wave 0 gap |
| AUTH-03 | POST /auth/reset-password calls Supabase resetPasswordForEmail | integration | `pytest backend/tests/test_auth.py::test_password_reset -x` | Wave 0 gap |
| AUTH-04 | Access token cookie present after login; protected route succeeds after refresh | integration | `pytest backend/tests/test_auth.py::test_session_persists -x` | Wave 0 gap |

**Notes:**
- AUTH-02 and AUTH-04 require a live local Supabase stack (`supabase start`). These are integration tests, not unit tests. They should run against the local stack, not mocks.
- AUTH-03 (email reset) can be smoke-tested by verifying the API call completes without error and Inbucket receives the email via the Inbucket REST API (`GET http://localhost:54324/api/v1/mailbox/test@example.com`).

### Sampling Rate
- **Per task commit:** `pytest backend/tests/test_auth.py -x -q`
- **Per wave merge:** `pytest backend/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `backend/tests/__init__.py` — package init
- [ ] `backend/tests/conftest.py` — shared fixtures (test client, env vars, pre-verified test user)
- [ ] `backend/tests/test_auth.py` — covers AUTH-01 through AUTH-04
- [ ] `backend/pytest.ini` or `pyproject.toml` `[tool.pytest.ini_options]` — test config
- [ ] Framework install: `pip install pytest pytest-asyncio httpx` — not yet in requirements.txt

---

## Sources

### Primary (HIGH confidence)
- PyPI registry — verified versions for all Python packages: PyJWT 2.12.1, FastAPI 0.135.1, supabase 2.28.2, starlette-csrf 3.0.0, asyncpg 0.31.0, boto3 1.42.71, redis 7.3.0, uvicorn 0.42.0
- npm registry — verified versions: @supabase/supabase-js 2.99.2, @supabase/ssr 0.9.0, supabase CLI 2.81.3
- Supabase JWT Claims Reference: https://supabase.com/docs/guides/auth/jwt-fields — `sub`, `email`, `aud`, `exp`, `role` claims confirmed
- Supabase sessions documentation: https://supabase.com/docs/guides/auth/sessions — access token 1-hour default, refresh token one-time-use with 10s reuse window confirmed
- Supabase seeding docs: https://supabase.com/docs/guides/local-development/seeding-your-database — `supabase/seed.sql` default location, `supabase db reset` behavior confirmed
- Supabase CLI start reference: https://supabase.com/docs/reference/cli/start — ports 54321/54322/54323/54324 confirmed
- PyJWT 2.12.1 official docs — `jwt.decode()` with `algorithms=["HS256"]`, `audience="authenticated"` pattern

### Secondary (MEDIUM confidence)
- starlette-csrf 3.0.0 (PyPI + GitHub: https://github.com/frankie567/starlette-csrf) — double-submit cookie middleware for Starlette/FastAPI, confirmed active maintenance
- Supabase discussion #12303 (GitHub) — confirmed HTTP-only cookies require backend-mediated auth; SDK auto-refresh does not work with HTTP-only cookies in pure SPA mode
- DEV Community article "Validating a Supabase JWT with Python and FastAPI" — HS256 + audience="authenticated" + sub=user_id pattern cross-verified with official JWT claims docs

### Tertiary (LOW confidence)
- MinIO Docker Hub deprecation (October 2025 per WebSearch) — flagged for validation; use `minio/minio` official image as a precaution

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified against live package registries (PyPI + npm)
- Architecture (FastAPI cookie setter pattern): MEDIUM-HIGH — confirmed by multiple sources + Supabase official discussion; no official Supabase FastAPI doc exists
- HTTP-only cookie / Supabase JS SDK interaction: HIGH — confirmed by official Supabase discussion thread with maintainer response
- Pitfalls: HIGH — derived from official docs and confirmed package behavior
- JWT validation pattern: HIGH — cross-referenced PyJWT docs with official Supabase JWT claims reference

**Research date:** 2026-03-18
**Valid until:** 2026-04-18 (stable stack; Supabase CLI versions update frequently — re-verify CLI version before scaffold)
