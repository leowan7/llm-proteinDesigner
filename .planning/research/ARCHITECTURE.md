# Architecture Research

**Domain:** LLM-agent + async GPU job SaaS (protein design)
**Researched:** 2026-03-18
**Confidence:** HIGH (patterns verified across Modal docs, RunPod docs, FastAPI docs, Claude Agent SDK docs)

---

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FRONTEND LAYER                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  Chat/Wizard │  │  Job Monitor │  │  Results /   │              │
│  │  UI (React)  │  │  (SSE client)│  │  3D Viewer   │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
└─────────┼────────────────┼─────────────────┼────────────────────────┘
          │ REST/HTTP       │ SSE stream       │ presigned URL download
┌─────────┼────────────────┼─────────────────┼────────────────────────┐
│         ▼                ▼                 ▼                         │
│                    FASTAPI BACKEND                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  API Router                                                  │   │
│  │  /chat  /jobs  /jobs/{id}/status  /jobs/{id}/results         │   │
│  └───────┬──────────────┬──────────────────┬────────────────────┘   │
│          │              │                  │                         │
│  ┌───────▼──────┐  ┌────▼──────────┐  ┌───▼────────────────────┐   │
│  │ Agent Service│  │ Job Service   │  │  Auth / Billing Service │   │
│  │ (Claude SDK) │  │ (dispatcher)  │  │  (JWT + Stripe)         │   │
│  └───────┬──────┘  └────┬──────────┘  └────────────────────────┘   │
│          │              │                                            │
│  ┌───────▼──────────────▼──────────────────────────────────────┐    │
│  │                   Job Store (PostgreSQL)                     │    │
│  │   jobs table: id, user_id, status, provider_job_id,         │    │
│  │               tool, params, created_at, cost_cents           │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   Redis                                      │   │
│  │   - SSE event pub/sub (job status updates)                   │   │
│  │   - Agent conversation state cache (session_id → messages)   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
          │ GPU job dispatch (provider interface)
┌─────────┼──────────────────────────────────────────────────────────┐
│         ▼                                                           │
│             GPU PROVIDER LAYER (abstract interface)                 │
│  ┌───────────────────────┐    ┌───────────────────────┐            │
│  │  ModalProvider        │    │  RunPodProvider        │            │
│  │  modal.Function.spawn │    │  runpod.run_async      │            │
│  │  returns job handle   │    │  returns job_id        │            │
│  └───────────────────────┘    └───────────────────────┘            │
│          │                              │                           │
│  ┌───────▼──────────────────────────────▼───────────────────────┐  │
│  │              GPU Containers (per job)                         │  │
│  │   RFdiffusion runner  |  BindCraft runner  |  Boltzgen runner │  │
│  │   Docker image per tool, GPU-accelerated, writes to S3       │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
          │ output files (PDB, scores, report)
┌─────────▼───────────────────────────────────────────────────────────┐
│                       OBJECT STORAGE (S3 / R2)                      │
│   inputs/{user_id}/{job_id}/target.pdb                              │
│   outputs/{user_id}/{job_id}/design_001.pdb                         │
│   outputs/{user_id}/{job_id}/report.json                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

### Component Responsibilities

| Component | Responsibility | Boundary Rule |
|-----------|----------------|---------------|
| **Frontend (React)** | Chat UI, parameter wizard, job status display, 3D viewer (Mol* or NGL), file download | Never talks to GPU provider directly. Only talks to FastAPI backend. |
| **FastAPI Router** | HTTP entry point, auth middleware, input validation, route to services | Thin layer — no business logic. |
| **Agent Service** | Manages Claude conversation sessions; interprets NL input; asks wizard questions; produces validated JobSpec | Only output crossing this boundary is a `JobSpec` (structured dict). Never dispatches jobs itself. |
| **Job Service** | Accepts `JobSpec`, writes job record to DB, calls GPU provider, polls/receives status, publishes SSE events, generates presigned download URLs | The only component that calls GPU provider or touches job records. |
| **GPU Provider Interface** | Abstract base class; concrete implementations for Modal and RunPod; submits job, polls status, cancels | No knowledge of jobs table or user context. Input: tool name + params + S3 paths. Output: provider job ID + status. |
| **GPU Containers** | Execute the design tool (RFdiffusion / BindCraft / Boltzgen); write outputs to S3; report status via exit code | Stateless per job. Credentials injected via environment. No knowledge of users or billing. |
| **Job Store (PostgreSQL)** | Source of truth for all job records: status, cost, params, user ownership | Single writer: Job Service. Frontend reads via API — never direct DB access. |
| **Redis** | SSE event bus (pub/sub channels per job_id); agent session state cache | Short-lived. Not source of truth. Job records in Postgres are authoritative. |
| **Object Storage (S3/R2)** | Durable storage for input PDB files and output PDB/report files | Access controlled via presigned URLs with expiry. No public bucket access. |
| **Auth/Billing Service** | JWT issuance, user account management, Stripe webhook handling, GPU cost tracking per job | Stripe webhooks update payment state; cost per job recorded in jobs table. |

---

## Data Flow

### Flow 1: NL Input → Agent → Wizard → JobSpec

```
User types: "Design a binder for IL-6R, I have the PDB"
    │
    ▼
POST /chat  {session_id, message, file?}
    │
    ▼
Agent Service
  ├── Resumes Claude session (session_id → Redis cache → conversation history)
  ├── Claude interprets intent, selects tool (BindCraft), asks clarifying questions
  ├── Each wizard question is a Claude turn: AskUserQuestion tool call
  │   → response streamed back to frontend as SSE or chunked JSON
  ├── After all parameters collected, Claude emits structured JobSpec:
  │   { tool: "bindcraft", target_chain: "A", hotspot_residues: [...],
  │     num_designs: 50, ... }
  └── Agent Service validates JobSpec against Pydantic schema, returns to router
    │
    ▼
POST /jobs  {session_id, job_spec}  [user confirms]
```

### Flow 2: Job Dispatch → GPU Execution

```
POST /jobs  {job_spec}
    │
    ▼
Job Service
  ├── Writes job record: status=PENDING, user_id, params, created_at
  ├── Uploads input file to S3: inputs/{user_id}/{job_id}/target.pdb
  ├── Calls GPU provider:
  │   provider.submit(tool="bindcraft", params=job_spec, s3_input=..., s3_output=...)
  │   → returns provider_job_id
  ├── Updates job record: status=QUEUED, provider_job_id
  └── Returns job_id to frontend immediately (202 Accepted)
    │
    ▼
GPU Container (Modal or RunPod)
  ├── Pulls input PDB from S3
  ├── Runs design tool (30 min – 2 hr)
  ├── Writes outputs to S3: outputs/{user_id}/{job_id}/
  └── Signals completion (exit code / return value)
```

### Flow 3: Status Updates → Real-Time Frontend

```
Background poller (Job Service, asyncio task or Celery beat)
  ├── Every 30s: calls provider.get_status(provider_job_id)
  ├── On status change: UPDATE jobs SET status=... WHERE id=...
  └── Publishes event to Redis channel: job:{job_id}:status
        │
        ▼
SSE endpoint: GET /jobs/{job_id}/status/stream
  ├── FastAPI subscribes to Redis channel job:{job_id}:status
  ├── Yields EventSourceResponse to browser
  └── Browser receives: {status: "RUNNING", progress: "iteration 23/100"}

On COMPLETED:
  ├── Job Service generates presigned S3 URLs (24hr expiry)
  ├── Updates job record: status=COMPLETED, output_urls=[...]
  └── Publishes final event with download URLs
```

### Flow 4: Result Delivery

```
SSE event received: {status: "COMPLETED", results_ready: true}
    │
    ▼
Frontend requests: GET /jobs/{job_id}/results
    │
    ▼
Job Service
  ├── Verifies job belongs to requesting user (auth check)
  ├── Generates fresh presigned S3 URLs
  └── Returns: { designs: [{rank, score, pdb_url, ...}], report_url, ... }
    │
    ▼
Frontend
  ├── Renders 3D viewer (Mol* / NGL Viewer) from pdb_url directly
  └── Download link hits presigned URL → S3 directly (no backend proxy)
```

---

## GPU Provider Interface Design

The provider interface must be stable regardless of whether Modal or RunPod is underneath. Design as an abstract base class:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class JobStatus(str, Enum):
    QUEUED   = "QUEUED"
    RUNNING  = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED   = "FAILED"
    CANCELLED = "CANCELLED"

@dataclass
class SubmitRequest:
    tool: str                    # "rfdiffusion" | "bindcraft" | "boltzgen"
    params: dict                 # tool-specific parameters
    s3_input_prefix: str         # s3://bucket/inputs/{user_id}/{job_id}/
    s3_output_prefix: str        # s3://bucket/outputs/{user_id}/{job_id}/

@dataclass
class SubmitResult:
    provider_job_id: str
    estimated_cost_cents: int | None

@dataclass
class StatusResult:
    status: JobStatus
    progress_message: str | None
    error_message: str | None
    cost_cents: int | None       # populated on COMPLETED

class GPUProvider(ABC):
    @abstractmethod
    async def submit(self, request: SubmitRequest) -> SubmitResult: ...

    @abstractmethod
    async def get_status(self, provider_job_id: str) -> StatusResult: ...

    @abstractmethod
    async def cancel(self, provider_job_id: str) -> None: ...
```

**ModalProvider specifics:**
- Use `modal.Function.from_name("rfdiffusion-runner").spawn(**params)` to submit asynchronously
- Modal call objects have `.get_call_graph()` and `.poll()` methods for status
- Modal containers write outputs directly to S3 via boto3 (credentials as Modal secrets)

**RunPodProvider specifics:**
- Use `runpod.run_async(endpoint_id, {"input": params})` → returns job_id string
- Poll `GET /status/{job_id}` on RunPod endpoint URL
- Job states: IN_QUEUE → RUNNING → COMPLETED / FAILED
- Results persist for 30 minutes on RunPod; must copy to S3 before that window closes

**Provider selection at runtime:**
```python
# config.py
GPU_PROVIDER = os.environ.get("GPU_PROVIDER", "modal")  # "modal" or "runpod"

def get_provider() -> GPUProvider:
    if GPU_PROVIDER == "modal":
        return ModalProvider()
    elif GPU_PROVIDER == "runpod":
        return RunPodProvider()
    raise ValueError(f"Unknown GPU_PROVIDER: {GPU_PROVIDER}")
```

---

## Job Status Polling Architecture

For 30 min – 2 hr jobs, SSE with a Redis pub/sub backend and a separate polling loop is the correct pattern. Do not poll the GPU provider from the HTTP request handler.

```
┌────────────────────────────────────────────────────────────────────┐
│  Background Polling Loop (asyncio or Celery beat, runs separately) │
│                                                                    │
│  1. SELECT * FROM jobs WHERE status IN ('QUEUED', 'RUNNING')       │
│  2. For each job: provider.get_status(provider_job_id)             │
│  3. If status changed:                                             │
│     a. UPDATE jobs SET status=new_status                           │
│     b. PUBLISH to Redis channel: job:{job_id}:status               │
│  4. Sleep 30s, repeat                                              │
└────────────────────────────────────────────────────────────────────┘
                      │ Redis pub/sub
┌─────────────────────▼──────────────────────────────────────────────┐
│  SSE Endpoint (FastAPI async generator)                            │
│                                                                    │
│  GET /jobs/{job_id}/status/stream                                  │
│  ├── Auth check: job belongs to this user                          │
│  ├── Subscribe to Redis channel job:{job_id}:status                │
│  ├── Yield current status immediately (from DB, handles reconnect) │
│  └── Yield each new message until COMPLETED or FAILED or timeout   │
└────────────────────────────────────────────────────────────────────┘
```

**Why not webhook from GPU provider:**
- Modal supports webhook callbacks but they require a public HTTPS endpoint and add infrastructure
- RunPod does not reliably support outbound webhooks on all GPU types
- Polling every 30s is sufficient for 30 min – 2 hr jobs (max 1% overhead on a 30 min job)
- Polling is simpler and easier to test

**SSE reconnect handling:**
- Browser EventSource reconnects automatically on disconnect
- On reconnect, SSE endpoint reads current status from DB and sends immediately
- Client does not need to re-poll; it just reconnects and gets current state

**Polling interval:**
- QUEUED jobs: poll every 60s (nothing to see yet)
- RUNNING jobs: poll every 30s (user wants progress)
- Adaptive backoff optional but not required for v1

---

## Recommended Project Structure

```
llm-protein-designer/
├── backend/
│   ├── main.py                  # FastAPI app, router registration, lifespan
│   ├── config.py                # Settings from env (GPU_PROVIDER, S3 bucket, etc.)
│   ├── auth/
│   │   ├── router.py            # /auth/login, /auth/register, /auth/refresh
│   │   └── service.py           # JWT creation, user lookups
│   ├── agent/
│   │   ├── router.py            # POST /chat
│   │   ├── service.py           # Claude Agent SDK session management
│   │   ├── session_store.py     # Redis-backed conversation state
│   │   └── tools/               # Custom tools exposed to Claude
│   │       ├── fetch_structure.py  # fetch PDB/UniProt by accession
│   │       └── emit_job_spec.py    # structured tool to finalize params
│   ├── jobs/
│   │   ├── router.py            # POST /jobs, GET /jobs, GET /jobs/{id}
│   │   ├── service.py           # dispatch, status, presigned URLs
│   │   ├── models.py            # SQLAlchemy Job model
│   │   ├── schemas.py           # Pydantic JobSpec, JobStatus, JobResult
│   │   ├── poller.py            # background polling loop
│   │   └── sse.py               # SSE streaming endpoint
│   ├── providers/
│   │   ├── base.py              # GPUProvider ABC, JobStatus, SubmitRequest
│   │   ├── modal_provider.py    # Modal implementation
│   │   └── runpod_provider.py   # RunPod implementation
│   ├── storage/
│   │   └── s3.py                # upload, presigned URL generation
│   ├── billing/
│   │   ├── router.py            # Stripe webhook receiver
│   │   └── service.py           # cost tracking, Stripe session creation
│   └── db/
│       ├── session.py           # SQLAlchemy async engine setup
│       └── migrations/          # Alembic migrations
├── gpu-workers/
│   ├── rfdiffusion/
│   │   ├── runner.py            # Modal/RunPod entry point for RFdiffusion
│   │   └── Dockerfile
│   ├── bindcraft/
│   │   ├── runner.py
│   │   └── Dockerfile
│   └── boltzgen/
│       ├── runner.py
│       └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Chat/            # wizard conversation UI
│   │   │   ├── JobMonitor/      # SSE consumer, status display
│   │   │   └── StructureViewer/ # Mol* or NGL integration
│   │   └── api/                 # typed fetch wrappers
│   └── package.json
└── docker-compose.yml           # local dev: postgres, redis, backend, frontend
```

**Structure rationale:**
- `providers/` isolates GPU vendor logic; switching providers requires no changes outside this folder
- `gpu-workers/` are separate deployable units — each has its own Docker image and is deployed independently to Modal/RunPod
- `agent/` is isolated from `jobs/` — the agent produces a `JobSpec` but never dispatches; decouples wizard iteration from job execution
- `storage/s3.py` is a single module; GPU workers and the backend both import it — no duplication

---

## Architectural Patterns

### Pattern 1: Agent Session as a Resumable State Machine

**What:** The Claude conversation for a single design task is a session with a persistent ID. Each HTTP request to `/chat` resumes the session (via `options=ClaudeAgentOptions(resume=session_id)`). The wizard progresses over multiple turns until Claude emits a `emit_job_spec` tool call.

**When to use:** Any multi-turn LLM interaction where intermediate state matters (wizard, clarification loops, guided parameter collection).

**Trade-offs:**
- Claude Agent SDK manages conversation history natively; no custom message reconstruction needed
- Session IDs must be stored server-side (Redis) and associated with the authenticated user to prevent cross-user session access
- Sessions have a token budget; very long wizard conversations will eventually truncate context

**Implementation note:** Store `{session_id: user_id}` in Redis with a TTL. On every `/chat` request, verify the session_id belongs to the requesting user before resuming.

### Pattern 2: Fire-and-Poll over Long-Polling or WebSocket

**What:** Job submission returns immediately with a job_id (202 Accepted). Status is retrieved via SSE stream that polls Redis for updates published by a background poller. The poller hits the GPU provider API every 30–60 seconds.

**When to use:** Jobs with runtimes measured in minutes to hours. The client must not block an HTTP connection for the duration.

**Trade-offs:**
- Simpler than WebSocket (no upgrade, unidirectional, browser reconnect is automatic)
- Requires Redis for pub/sub to avoid N SSE handlers each polling the GPU provider directly
- SSE is sufficient here; WebSocket would be overkill since status flow is server-to-client only

### Pattern 3: Presigned URL for Large File Delivery

**What:** GPU containers write PDB outputs directly to S3. When a job completes, the backend generates time-limited presigned URLs (24 hr expiry). The frontend fetches these URLs from the API and uses them directly for download and 3D viewer loading.

**When to use:** Any binary output file too large to stream through the backend API, or where you want to avoid egress costs from proxying.

**Trade-offs:**
- Files never traverse the backend — reduces compute and egress cost
- Presigned URLs expose S3 key structure; use opaque paths (job UUID-based) to avoid enumeration
- File access can be audited by logging presigned URL generation, not S3 access directly

### Pattern 4: Abstract Provider Interface Verified at Boot

**What:** `GPUProvider` ABC with two concrete implementations. At application startup, the configured provider is instantiated and a connectivity check is performed. If the provider fails the check, the app does not start (fail fast).

**When to use:** Any system that must swap out an external dependency without changing calling code.

**Trade-offs:**
- Forces discipline on provider implementations (must implement all abstract methods)
- Makes testing easy: swap in a `MockProvider` that returns canned results for unit and integration tests
- The cost estimate field in `SubmitResult` is `None` for Modal (cost known post-job) and estimated for RunPod; billing logic must handle both cases

---

## Build Order (Dependency-Driven)

Build these components in this sequence. Each phase depends on the previous being stable.

```
Phase 1: Data Layer
  PostgreSQL schema (jobs, users)
  Redis setup (local docker-compose)
  S3 bucket + IAM policies
  Alembic migrations

Phase 2: Auth
  User registration / login (JWT)
  Session association model
  Required before any user-scoped routes

Phase 3: GPU Provider Interface + One Concrete Implementation
  GPUProvider ABC
  MockProvider (returns fake job_id, fake status sequence)
  ModalProvider (real integration)
  This unlocks all job dispatch development

Phase 4: Agent Service (Claude wizard)
  Claude Agent SDK session management
  Tool definitions: fetch_structure, emit_job_spec
  Wizard conversation flow (tested against MockProvider)
  No real GPU needed at this phase

Phase 5: Job Service + Polling + SSE
  Job dispatch using validated JobSpec from agent
  Background poller loop
  SSE endpoint
  Frontend can now show live status

Phase 6: Frontend (Chat + Monitor + Viewer)
  Chat UI consuming agent API
  Job monitor consuming SSE
  Structure viewer (Mol*)
  Download links from presigned URLs

Phase 7: Billing
  Stripe integration
  Cost recording per job
  Pay-per-job flow

Phase 8: RunPodProvider + Hardening
  Second provider implementation
  Provider selection by env var
  Load and chaos testing
```

**Critical path:** Phase 3 (MockProvider) unblocks Phase 4 and 5 simultaneously. Build MockProvider first — it is the most important enabler in the whole system.

---

## Anti-Patterns

### Anti-Pattern 1: Holding an HTTP Connection Open for a GPU Job

**What people do:** Submit job, return a streaming HTTP response, keep connection alive for 30–120 minutes waiting for GPU output.

**Why it's wrong:** HTTP proxies, load balancers, and browsers enforce connection timeouts (typically 30–60s). A 2 hr job will always fail this way. It also pins a server thread or async context for the full duration.

**Do this instead:** Return 202 Accepted with a job_id immediately. Use SSE on a separate endpoint for status. Design the client to tolerate disconnects and reconnects (EventSource does this automatically).

### Anti-Pattern 2: Storing Agent Conversation in the Browser

**What people do:** Keep the full Claude message history in frontend state (localStorage, React state) and send it back to the server on each turn.

**Why it's wrong:** The Claude Agent SDK session is server-side. Message history can grow large (thousands of tokens). Sending it back and forth is wasteful and creates desync bugs. Client-side storage also means session loss on tab close.

**Do this instead:** Store session_id in the browser. The server holds conversation history (Redis or the SDK's session mechanism). Every `/chat` request sends only the new user message plus session_id.

### Anti-Pattern 3: GPU Provider Logic Inside the Job Service

**What people do:** Put Modal-specific or RunPod-specific API calls directly in the job service or router.

**Why it's wrong:** Switching providers requires modifying core job logic. Testing requires a live GPU provider. The provider-specific retry behavior, authentication, and polling logic pollutes business logic.

**Do this instead:** All provider calls go through the `GPUProvider` interface. The job service only knows `provider.submit()`, `provider.get_status()`, `provider.cancel()`.

### Anti-Pattern 4: Polling the GPU Provider from the SSE Handler

**What people do:** Each active SSE connection polls the GPU provider API directly to get status updates.

**Why it's wrong:** If 20 users are watching jobs, the backend makes 20 concurrent provider API calls every 30s. Provider rate limits will trigger. Response latency on SSE events becomes coupled to GPU provider API latency.

**Do this instead:** One background poller polls all active jobs and publishes to Redis pub/sub. SSE handlers subscribe to Redis. Provider load is O(active_jobs), not O(active_connections).

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| **Anthropic Claude API** | Claude Agent SDK (Python); async session with `resume=session_id` | Store API key in env. Rate limits apply per account — monitor token usage per session. |
| **Modal** | `modal` Python SDK; `Function.from_name().spawn()` for async dispatch | Deploy GPU workers separately with `modal deploy`. Workers must have S3 credentials as Modal Secrets. |
| **RunPod** | `runpod` Python SDK; `run_async(endpoint_id, input)` → job_id | Each tool needs a deployed RunPod endpoint. Result retention is 30 min; must copy outputs to S3 before expiry. |
| **S3 / Cloudflare R2** | `boto3` for writes from GPU workers; presigned URLs for reads | R2 has no egress cost (preferred over S3 for large PDB downloads). S3-compatible API means zero code change to switch. |
| **Stripe** | Webhook receiver at `POST /billing/webhook`; Customer Portal for self-serve | Record GPU cost per job in jobs table. Stripe billing period summarizes cost. Idempotency key = job_id. |
| **RCSB PDB API** | `GET https://files.rcsb.org/download/{pdb_id}.pdb` | No auth required. Cache downloaded structures in S3 to avoid repeat fetches. |
| **UniProt API** | `GET https://rest.uniprot.org/uniprotkb/{accession}.pdb` | Some entries lack experimental structures; fall back to AlphaFold DB. |

### Internal Boundaries

| Boundary | Communication | Rule |
|----------|---------------|------|
| Agent Service → Job Service | `JobSpec` Pydantic model (in-process function call) | Agent never writes to DB or calls GPU. It only produces a validated `JobSpec`. |
| Job Service → GPU Provider | `GPUProvider` interface method calls | Only Job Service imports from `providers/`. Router and Agent never call providers directly. |
| GPU Workers → Object Storage | Direct S3 write via boto3 | Workers have no knowledge of the database. They write outputs to a deterministic S3 path and exit. |
| Backend → Frontend (job status) | Redis pub/sub → SSE | No shared state except via API. Frontend is fully stateless. |
| Backend → Frontend (files) | Presigned S3 URLs | Files never proxy through backend. Backend only generates the URL. |

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 0–50 concurrent users | Single FastAPI process, asyncio background poller, Redis on same server, one PostgreSQL instance. Modal handles GPU scaling automatically. |
| 50–500 concurrent users | Move background poller to Celery beat worker (separate process). Add read replica for PostgreSQL. Rate-limit Claude API calls per user to prevent token exhaustion. |
| 500+ concurrent users | Separate SSE service (it holds long-lived connections, don't co-locate with request handlers). Add job priority queue. Consider multi-region S3 if users are geographically distributed. |

**First bottleneck:** Claude API token rate limits, not backend compute. At moderate scale, if many users run long wizard sessions simultaneously, the backend will hit Anthropic's rate limits before it hits CPU/memory limits. Mitigation: per-user token budgets enforced in Agent Service.

**Second bottleneck:** SSE connections. FastAPI with asyncio handles thousands of concurrent SSE connections, but they consume file descriptors. Set OS file descriptor limits appropriately in production.

---

## Sources

- Claude Agent SDK Overview — https://platform.claude.com/docs/en/agent-sdk/overview (HIGH confidence — official, current)
- RunPod Serverless Job Operations — https://docs.runpod.io/serverless/endpoints/job-operations (HIGH confidence — official)
- RunPod Python SDK — https://github.com/runpod/runpod-python (HIGH confidence — official)
- Modal Webhooks & Functions — https://modal.com/docs/guide/webhooks (MEDIUM confidence — overview only, async spawn verified via SDK docs pattern)
- FastAPI SSE via sse-starlette — https://pypi.org/project/sse-starlette/ (HIGH confidence — official PyPI, well-established)
- FastAPI Background Tasks with Celery/Redis — https://testdriven.io/blog/fastapi-and-celery/ (MEDIUM confidence — community, consistent with FastAPI official docs)
- S3 Presigned URLs — https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html (HIGH confidence — official AWS)
- LLM State Machine pattern for wizard UX — https://github.com/jsz-05/LLM-State-Machine (LOW confidence — community example; pattern is sound but implementation specifics vary)
- SSE for agent streaming — https://akanuragkumar.medium.com/streaming-ai-agents-responses-with-server-sent-events-sse-a-technical-case-study-f3ac855d0755 (MEDIUM confidence — community verified against FastAPI docs)

---

*Architecture research for: LLM-agent + async GPU job SaaS (protein design)*
*Researched: 2026-03-18*
