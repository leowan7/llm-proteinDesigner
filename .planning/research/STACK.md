# Stack Research

**Domain:** Scientific SaaS — LLM-driven protein design with async GPU job dispatch
**Researched:** 2026-03-18
**Confidence:** MEDIUM-HIGH (core stack HIGH; some library version pins MEDIUM due to fast-moving ecosystem)

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| FastAPI | 0.115.x | Python API backend | Native async, Pydantic v2 integration, automatic OpenAPI docs, first-class typing. The de facto standard for async Python APIs in 2025. Django/Flask are synchronous by default — a fundamental mismatch for a system where nearly every request either touches an async task queue or awaits an LLM call. |
| PostgreSQL | 16+ | Primary database | Relational model is correct for user accounts, job records, billing events, and structured design metadata. Supports JSONB for flexible job parameters without schema gymnastics. |
| SQLAlchemy | 2.0.x | ORM + async engine | SQLAlchemy 2.0 is a rewrite with first-class async support via `AsyncSession`. Pair with `asyncpg` driver. Use `Alembic` for migrations. Do not use SQLAlchemy 1.x — the legacy ORM has no native async and requires sync thread pool workarounds. |
| asyncpg | 0.30.x | PostgreSQL async driver | The only Python PostgreSQL driver designed from scratch for asyncio. Required by SQLAlchemy's async engine. Significantly faster than psycopg2 under concurrent load. |
| Redis | 7.x | Task queue broker + result backend | Required by Taskiq (the async task queue). Also serves as ephemeral job-status cache so the API can return status without polling the GPU provider on every request. Use Redis Stack or plain Redis; either works. |
| Taskiq | 0.11.x | Async task queue | Built for asyncio from the ground up; deep FastAPI integration via `taskiq-fastapi` (shared dependency injection). Contrast with Celery, which is synchronous and requires thread pool workarounds to interoperate with async FastAPI. ARQ is simpler but struggles under load (benchmarks show ~10x lower throughput than Taskiq). Taskiq supports Redis and RabbitMQ brokers without code changes. |
| Next.js | 15.x | Frontend framework | Server-side rendering for the marketing/auth pages; React SPA behavior for the job dashboard and 3D viewer. The FastAPI backend is the authoritative API — Next.js does not replace it. Next.js App Router is the default in v15; use it. |
| Supabase Auth | current | User authentication | Handles email/password + OAuth (Google, GitHub) out of the box. Issues JWTs that FastAPI validates directly. Row-level security (RLS) policies on Postgres enforce per-user data isolation at the database layer — the correct place to enforce it for a multi-tenant SaaS. Avoid rolling custom auth; the risk/reward is poor. |
| anthropic (Python SDK) | 0.43.x | Claude API client | Official SDK. Use `AsyncAnthropic` client for all agent calls. Current production models: `claude-sonnet-4-6` (best speed/intelligence ratio for conversational agent turns) and `claude-opus-4-6` (reserve for complex tool orchestration if needed). Do not use any Claude 3.x model — retired or being retired. |
| Modal | 0.73.x | Primary GPU provider SDK | Python-native serverless GPU. Decorator-based function registration means the design tool (RFdiffusion, BindCraft, Boltzgen) is wrapped in a Modal function and deployed independently of the web app. `function.spawn()` returns a `FunctionCall` handle immediately; `.get()` polls asynchronously. Results available for 7 days. Cold starts: ~1–4 seconds for GPU containers. Best fit when the team wants Python-first infrastructure with no container management overhead. |
| runpod (Python SDK) | 1.7.x | Secondary GPU provider (abstraction target) | RunPod Serverless endpoints expose an HTTP API for async job dispatch; the Python SDK wraps status polling. More cost-efficient for sustained A100/H100 workloads (per-GPU pricing vs Modal's per-call pricing). The abstract GPU provider interface in the application code should support both Modal and RunPod behind a common `JobProvider` protocol. |
| Stripe | 7.x (Python SDK) | Billing | Use Stripe Billing Meters API (not the deprecated Usage Records API, removed in Stripe API version `2025-03-31.basil`). Flow: on job completion, emit a `stripe.billing.meter_event` with GPU seconds consumed; Stripe aggregates and bills at period end. For per-job billing without subscriptions, use Stripe Checkout in payment link mode for prepaid credits, or a thin subscription with metered add-ons. |
| Mol* (Molstar) | 4.x | 3D molecular visualization | The current standard for web-based macromolecular visualization — used by PDBe, RCSB PDB, and AlphaFold DB. Handles large structures efficiently via WebGL. The `molstar` npm package is the direct integration path; `molstar-react` is a thin React wrapper suitable for embedding in Next.js. Load PDB files from S3 presigned URLs directly. |
| AWS S3 (or Cloudflare R2) | — | PDB file + result storage | Object storage for uploaded target PDB files and generated design outputs. Generate presigned URLs server-side (boto3 / Cloudflare SDK); the client uploads/downloads directly — no large binary traffic through the FastAPI server. Per-account S3 prefixes (`user_id/job_id/`) enforce logical data isolation. R2 is S3-compatible and has no egress fees — prefer it if cost is a concern. |

---

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Pydantic | 2.x | Data validation + settings | Use `BaseModel` for all request/response schemas and job parameter models. Use `pydantic-settings` for env var management. FastAPI uses Pydantic v2 natively; do not pin v1. |
| Alembic | 1.14.x | Database migrations | Required when using SQLAlchemy. All schema changes go through Alembic — never alter the database directly in production. |
| python-jose / PyJWT | — | JWT validation in FastAPI | Validate Supabase-issued JWTs in FastAPI middleware. Supabase uses RS256 asymmetric keys by default; fetch the JWKS endpoint at startup. Use `PyJWT` 2.x (simpler API than python-jose). |
| boto3 / aiobotocore | 1.35.x / 2.15.x | S3 operations | `boto3` for synchronous presigned URL generation (fine — URL generation is fast). `aiobotocore` if you need truly async S3 operations in worker tasks. |
| httpx | 0.28.x | Async HTTP client | Use in FastAPI background tasks and Taskiq workers for calling external APIs (PDB/UniProt fetch, Stripe, RunPod REST API). `requests` is synchronous and blocks the event loop — do not use it inside async handlers. |
| taskiq-redis | 0.5.x | Redis broker for Taskiq | The standard Taskiq broker backend. `RedisStreamBroker` + `RedisAsyncResultBackend`. |
| taskiq-fastapi | 0.3.x | FastAPI dependency injection in Taskiq workers | Allows Taskiq workers to reuse FastAPI dependencies (database sessions, config, etc.). Required for clean architecture — avoids duplicating app initialization code in workers. |
| pytest-asyncio | 0.24.x | Async test runner | Required for testing FastAPI async endpoints and Taskiq task functions. Set `asyncio_mode = "auto"` in pytest config. |
| structlog | 24.x | Structured logging | Emit JSON logs from both the FastAPI app and Taskiq workers. Essential for correlating job IDs across the GPU provider, task queue, and API layers. |
| Sentry SDK | 2.x | Error tracking | Instrument both FastAPI and Taskiq workers. Sentry supports async contexts and task queues. Set `traces_sample_rate` conservatively in production given GPU job volume. |
| Instructor | 1.x | Structured outputs from Claude | Wraps `anthropic` SDK to return validated Pydantic models from Claude responses. Eliminates manual JSON parsing from tool call results. Use for the parameter wizard — Claude returns a `DesignParameters` Pydantic model directly. |

---

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv | Python package management + virtual envs | Dramatically faster than pip. Drop-in replacement. Use `uv pip install` and `uv venv`. Not compatible with conda; use one or the other. |
| Docker Compose | Local development environment | Spin up PostgreSQL, Redis, and the FastAPI app together. The Taskiq worker is a separate service in the Compose file. Do not run Modal functions locally in development — use Modal's `--local` stub mode or mock the provider interface. |
| Alembic | Migration management | `alembic revision --autogenerate` to generate migrations from SQLAlchemy models. Always review autogenerated migrations before applying. |
| Pytest | Test framework | With `pytest-asyncio` and `httpx.AsyncClient` for endpoint testing. |
| ESLint + Prettier | Frontend code quality | Standard Next.js defaults. |
| Vercel | Frontend deployment | Next.js deploys natively to Vercel. FastAPI backend deploys separately (Railway, Render, or AWS ECS). Do not co-deploy FastAPI on Vercel — Python serverless on Vercel has cold start and timeout limitations unsuitable for job orchestration. |

---

## Installation

```bash
# Backend (Python)
uv venv .venv
source .venv/bin/activate

uv pip install fastapi uvicorn[standard] sqlalchemy[asyncio] asyncpg alembic \
  taskiq taskiq-redis taskiq-fastapi redis \
  anthropic instructor pydantic pydantic-settings \
  boto3 httpx python-jose[cryptography] PyJWT \
  stripe structlog sentry-sdk[fastapi]

uv pip install modal runpod  # GPU provider SDKs

uv pip install -D pytest pytest-asyncio httpx

# Frontend (Node)
cd frontend
npx create-next-app@latest . --typescript --tailwind --app
npm install molstar molstar-react @stripe/stripe-js
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Taskiq | Celery | Only if you need the mature Celery ecosystem (Flower monitoring, Beat scheduler, broad broker support). Celery's synchronous core is a liability in a fully async FastAPI codebase — every Celery task must either be sync or use `asyncio.run()`, which creates a new event loop per task. Celery is the right answer for Django; not for this stack. |
| Taskiq | ARQ | ARQ is simpler to set up and adequate for low-volume workloads. If job volume stays under ~100 concurrent tasks, ARQ is fine. At higher concurrency, ARQ's throughput degrades significantly in load tests relative to Taskiq. |
| Modal | Replicate | Replicate is purpose-built for serving pre-trained models via API. It does not support running arbitrary custom GPU code (e.g., BindCraft with custom diffusion pipelines). Use Replicate only if the tool is already available as a public Replicate model. |
| Mol* (molstar) | NGL Viewer | NGL is the predecessor to Mol* (RCSB migrated from NGL to Mol*). Mol* supersedes NGL for all new projects. NGL's npm package is less actively maintained. |
| Mol* (molstar) | 3Dmol.js | 3Dmol.js is lighter and faster to integrate for simple PDB rendering (fewer KB, simpler API). Use 3Dmol.js if you only need basic ribbon/surface rendering and want to minimize bundle size. Use Mol* if you need trajectory playback, large assemblies, or the full RCSB-grade UI. For this app, Mol* is appropriate — designs may include large complexes and users expect RCSB-level visualization quality. |
| Supabase Auth | Auth0 / Clerk | Auth0 and Clerk are hosted auth services with richer UI components. Choose them if Supabase's auth feature set is limiting (e.g., complex enterprise SSO/SAML requirements). For a scientific SaaS with email + Google OAuth, Supabase Auth is sufficient and avoids an additional vendor. |
| Supabase Auth | Custom JWT (FastAPI-Users) | FastAPI-Users is a good library but requires building and maintaining the auth infrastructure yourself. The security risk is not worth it for a small team. |
| PostgreSQL | MongoDB | MongoDB's document model would be convenient for flexible job parameters, but the relational integrity between users, jobs, billing records, and file metadata makes PostgreSQL the correct choice. Use JSONB columns for the parts that need schema flexibility. |
| Stripe Meters API | Lago / OpenMeter | Lago and OpenMeter are open-source usage metering platforms. They add operational overhead. Stripe Meters API handles the metering and billing in one system — correct choice unless you have complex multi-dimensional metering that Stripe cannot express. |
| Cloudflare R2 | AWS S3 | R2 is S3-compatible with zero egress fees. For a biotech SaaS where users download multi-MB PDB files, egress costs on S3 accumulate. Use R2 unless you have existing AWS infrastructure that makes S3 the path of least resistance. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| FastAPI `BackgroundTasks` for GPU jobs | `BackgroundTasks` runs in the same process after the response is sent. It has no persistence — if the server restarts, the task is lost. For 30-min to 2-hr GPU jobs, this is unacceptable. | Taskiq with Redis broker |
| Celery in an async FastAPI codebase | Celery's worker model is synchronous. Interoperating with async code requires `asyncio.run()` inside tasks, which spawns a new event loop per task — wasteful and error-prone. | Taskiq |
| requests library inside async handlers | `requests` is blocking. Calling it inside a FastAPI async endpoint or Taskiq task blocks the event loop for the duration of the HTTP call. | httpx with `AsyncClient` |
| Claude 3.x models | Claude 3.5 Sonnet and 3.x models are retired or being retired. `claude-3-5-sonnet` now returns errors on the API. | `claude-sonnet-4-6` (default), `claude-opus-4-6` (complex orchestration) |
| Stripe legacy Usage Records API | Removed in Stripe API version `2025-03-31.basil`. Any code using `stripe.SubscriptionItem.create_usage_record()` will break if you pin to a recent Stripe API version. | Stripe Billing Meters API (`stripe.billing.meter_event`) |
| SQLAlchemy 1.x | No native async support. Requires `run_in_executor` to avoid blocking the event loop — defeats the purpose of async FastAPI. | SQLAlchemy 2.0 with `AsyncSession` |
| NGL Viewer | Superseded by Mol*; actively maintained by fewer contributors. RCSB and PDBe have migrated away from it. | molstar |
| LangChain for Claude orchestration | LangChain adds significant abstraction overhead and has historically lagged behind Anthropic's native SDK in supporting new Claude capabilities (tool use, structured outputs, extended thinking). The Claude tool use API is expressive enough to build the parameter wizard and job dispatch agent directly. | anthropic Python SDK + Instructor for structured outputs |
| Next.js API routes as the FastAPI replacement | Next.js API routes are Node.js serverless functions. Running Python (or calling Python GPU SDKs) from them is awkward and requires subprocess calls. The FastAPI backend owns all business logic; Next.js is the UI layer only. | FastAPI for all backend logic |

---

## Stack Patterns by Variant

**If Modal cold starts become a bottleneck (e.g., users complain about 3-5 second delays before GPU spin-up):**
- Pre-warm Modal containers with a scheduled keep-alive ping
- Or switch long-running workloads (>60 min) to RunPod dedicated pods where containers stay alive
- Modal's `keep_warm=1` parameter on the `@app.function()` decorator maintains a warm container

**If RunPod is used as primary provider (cost optimization at scale):**
- RunPod Serverless endpoints expose a REST API, not a Python SDK for function registration
- The GPU container must be pre-built as a Docker image with the design tool installed
- Job dispatch is via POST to the endpoint URL; status polling is via GET with job ID
- The abstract `JobProvider` interface in the app should make this swap transparent to the FastAPI layer

**If Cloudflare R2 is used instead of AWS S3:**
- R2 is S3-compatible; boto3 with a custom endpoint URL works without code changes
- Set `endpoint_url="https://<account_id>.r2.cloudflarestorage.com"` in the boto3 client
- No egress fees — strongly recommended for PDB download-heavy workloads

**If the agent needs to maintain multi-turn conversation state (parameter wizard sessions):**
- Store the conversation history in Redis (keyed by session ID), not in PostgreSQL
- Redis TTL of 24 hours is sufficient for wizard sessions
- Do not store full conversation history in the FastAPI session or in-memory — the server is stateless

---

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| FastAPI 0.115.x | Pydantic 2.x | FastAPI 0.100+ requires Pydantic v2. Do not mix FastAPI 0.115 with Pydantic v1. |
| SQLAlchemy 2.0.x | asyncpg 0.30.x | asyncpg is the required driver for `create_async_engine` with PostgreSQL. |
| taskiq-fastapi 0.3.x | Taskiq 0.11.x | Pin both together; minor version mismatches cause dependency injection failures. |
| anthropic 0.43.x | Instructor 1.x | Instructor wraps the anthropic client; update both together when either releases breaking changes. |
| Next.js 15.x | React 19 | Next.js 15 requires React 19. Mol* and molstar-react are compatible with React 18/19 but test this on upgrade. |
| Stripe Python SDK 7.x | Stripe API `2025-01-27` or later | Pin the Stripe API version explicitly in the SDK constructor. Do not let it float — Stripe's API versions are dated and breaking changes between versions are real. |

---

## GPU Provider Abstract Interface

The application layer must never call Modal or RunPod directly. Define a `JobProvider` protocol so either can be swapped:

```python
from typing import Protocol

class JobProvider(Protocol):
    async def submit(self, tool: str, params: dict, input_paths: list[str]) -> str:
        """Submit a job. Returns a provider-specific job ID."""
        ...

    async def status(self, job_id: str) -> str:
        """Returns: 'pending' | 'running' | 'completed' | 'failed'"""
        ...

    async def result(self, job_id: str) -> dict:
        """Returns output paths and scores after completion."""
        ...
```

The Taskiq worker instantiates the correct provider from config; the FastAPI layer only talks to the worker via the task queue.

---

## Sources

- [Modal Job Queue docs](https://modal.com/docs/guide/job-queue) — spawn/get patterns, 7-day result retention (HIGH confidence, official docs)
- [Modal Async docs](https://modal.com/docs/guide/async) — `spawn.aio()` for async job submission (HIGH confidence, official docs)
- [Anthropic Models Overview](https://platform.claude.com/docs/en/about-claude/models/overview) — Current model IDs, pricing, capabilities (HIGH confidence, official docs, fetched 2026-03-18)
- [Anthropic Tool Use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use) — Tool use implementation (HIGH confidence, official docs)
- [Stripe Billing Meters API](https://docs.stripe.com/api/billing/meter) — Meter events for usage-based billing (HIGH confidence, official docs)
- [Stripe Usage-Based Billing migration](https://docs.stripe.com/billing/subscriptions/usage-based-legacy/migration-guide) — Legacy Usage Records API removal date (HIGH confidence, official docs)
- [Taskiq GitHub](https://github.com/taskiq-python/taskiq) — FastAPI integration, Redis broker support (HIGH confidence, official repo)
- [taskiq-fastapi GitHub](https://github.com/taskiq-python/taskiq-fastapi) — Dependency injection integration (HIGH confidence, official repo)
- [Mol* official site](https://molstar.org/) — Current standard for web macromolecular visualization (HIGH confidence, official)
- [molstar npm](https://www.npmjs.com/package/molstar) — NPM package availability (HIGH confidence)
- [Supabase RLS docs](https://supabase.com/docs/guides/database/postgres/row-level-security) — Row-level security for multi-tenant isolation (HIGH confidence, official docs)
- [Northflank RunPod vs Modal comparison](https://northflank.com/blog/runpod-vs-modal) — Provider comparison (MEDIUM confidence, third-party analysis)
- [RunPod Python SDK](https://github.com/runpod/runpod-python) — Async endpoint polling (MEDIUM confidence, official repo)
- [SQLAlchemy async FastAPI — Leapcell](https://leapcell.io/blog/building-high-performance-async-apis-with-fastapi-sqlalchemy-2-0-and-asyncpg) — SQLAlchemy 2.0 + asyncpg integration patterns (MEDIUM confidence, verified against SQLAlchemy docs)
- [Taskiq vs ARQ vs Celery comparison](https://devproportal.com/languages/python/python-background-tasks-celery-rq-dramatiq-comparison-2025/) — Load test benchmarks (MEDIUM confidence, community analysis)

---

*Stack research for: LLM-driven protein design SaaS (scientific)*
*Researched: 2026-03-18*
