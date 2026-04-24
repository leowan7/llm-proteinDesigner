# Phase 11: Deployment - Research

**Researched:** 2026-04-24
**Domain:** Production platform deployment (Vercel + Railway + Cloudflare + Supabase + Upstash + R2 + Modal + Sentry)
**Confidence:** HIGH for platform-specific "how" details (verified against 2026 docs); MEDIUM for one-off config choices where user discretion applies.

## Summary

Phase 11 is infrastructure-only. Every code concern (HMAC webhook, Sentry SDK, arq cron, Pydantic settings, /health endpoint, Stripe meter events) is already built — this phase wires existing code into production platforms. The work decomposes cleanly into five waves: (1) external resource provisioning (Supabase Pro projects, Upstash, R2, Modal env), (2) DNS + SSL, (3) platform deploys (Railway + Vercel with CI gates), (4) observability wiring (Sentry DSN + UptimeRobot + Slack), (5) webhook secret rotation and `.env.example` audit.

Three material items diverge from the locked decisions as written and need the planner's attention:

1. **D-06 says `alembic upgrade head` — the codebase does not use Alembic.** Migrations live in `supabase/migrations/*.sql` (16 files, `20260318000000_init.sql` through `20260424000005_export_url_cleanup.sql`) and are applied via the Supabase CLI. The Railway predeploy hook must run `supabase db push` (or equivalent) against the Supabase Pro project's Postgres URL, not Alembic. `[VERIFIED: ls supabase/migrations/ + grep -i alembic in repo shows no alembic config, no alembic directory, no alembic in requirements.txt]`

2. **Stripe meter event summary API is per-customer, not global.** `GET /v1/billing/meters/{id}/event_summaries` requires a `customer` parameter, so D-16's "sum daily meter events" cannot be a single API call. The existing implementation in `backend/worker/cleanup.py::check_daily_gpu_spend` already avoids this by summing `gpu_cost_usd` out of the `public.jobs` table — keep that approach. Phase 11 just wires the cron on Railway and sets `GPU_DAILY_SPEND_ALERT_USD` + `RESEND_API_KEY` in Railway Variables. `[VERIFIED: backend/worker/cleanup.py lines 297-343 + Stripe docs: docs.stripe.com/api/billing/meter-event-summary/list]`

3. **Vercel Deployment Checks + GitHub branch protection have a documented race-condition collision.** Per Vercel: "Due to GitHub's implementation of Check Runs, these will collide and introduce race conditions when used with GitHub branch protection rules, GitHub rulesets, and Vercel Deployment Checks." `[CITED: vercel.com/docs/deployment-checks]` Recommended: use Vercel Deployment Checks to wait on `test.yml` (single source of truth), not GitHub branch protection + Vercel checks simultaneously.

**Primary recommendation:** Treat Phase 11 as five waves of mostly-configuration work. Wave 1 provisions external resources in parallel; Wave 2 locks DNS; Wave 3 cuts over Railway + Vercel; Wave 4 wires monitoring; Wave 5 closes out secret rotation + smoke tests. Flag #1 above (supabase db push vs alembic) to the user during planning — D-06's wording is wrong in a way that changes the predeploy command.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Domains + Environments**
- **D-01:** Production DNS — `kendrew.ai` → Vercel frontend, `app.kendrew.ai` → Railway backend. Matches `jobs@kendrew.ai` email sender.
- **D-02:** DNS provider is Cloudflare. Apex (`kendrew.ai`) proxied (orange cloud). `app.kendrew.ai` DNS-only (grey cloud) for LetsEncrypt validation + SSE streams.
- **D-03:** Full prod + staging split across every platform (Railway, Vercel, Modal, Supabase, Upstash, R2). Each env gets its own isolated resources.
- **D-04:** SSL certs platform-managed (Vercel + Railway auto-issue LetsEncrypt). No Cloudflare full-strict. Cloudflare stays DNS-only for `app.kendrew.ai`.

**CI/CD Deploy Flow**
- **D-05:** Deploys from `main` gated by `test.yml` green status. Railway + Vercel auto-deploy on push to main only after CI passes.
- **D-06:** Migrations run automatically as Railway predeploy (`alembic upgrade head`) before traffic. **[Planner note: codebase uses Supabase SQL migrations, not Alembic — see Summary point 1.]**
- **D-07:** Flip on `deploy-modal.yml`. Remove `if: false` guard. PRs touching `infrastructure/modal/**`, `docker/**`, or `backend/pipelines/**` → Modal `staging`; push to main → Modal `main`.
- **D-08:** Post-deploy `smoke.yml` is informational. Fails post to Sentry + `#kendrew-alerts`. Manual rollback via Railway/Vercel deploy history. No auto-rollback.

**Secrets + Env Management**
- **D-09:** Prod secrets live in platform-native stores only: Railway Variables, Vercel Env Vars, Modal Secrets. No Doppler / 1Password.
- **D-10:** Webhook secret dual-secret rotation. Backend accepts `RUNPOD_WEBHOOK_SECRET` + `_PREV` and `MODAL_WEBHOOK_SECRET` + `_PREV`. Runbook in `docs/deploy.md`.
- **D-11:** Full `.env.example` audit. Every prod key with placeholder + runtime-scope comment.
- **D-12:** Env vars scoped explicitly per runtime (Backend Railway / Frontend Vercel / Modal apps) via matrix table in `docs/deploy.md`.

**Monitoring**
- **D-13:** UptimeRobot (5 min ping `/health`) + Sentry Python+JS SDKs → `#kendrew-alerts` Slack. No PagerDuty / Opsgenie.
- **D-14:** Sentry Performance on hot paths only: `POST /agent/*`, `POST /jobs/launch`, `POST /webhooks/runpod`, `POST /webhooks/heartbeat`, `POST /jobs/{id}/upload-urls`.
- **D-15:** Modal observability = Modal dashboard + heartbeat endpoint only. No Sentry inside Modal containers, no log shipper.
- **D-16:** arq cron GPU spend alert. Email Leo via Resend if daily spend > `GPU_SPEND_ALERT_THRESHOLD_USD` (default 50). **[Already implemented in `check_daily_gpu_spend`; phase wires the env vars.]**

**ROADMAP corrections required (not scope changes):**
- SC 6: replace "GPU jobs dispatch to RunPod" with "Modal primary, RunPod quarantined fallback."
- SC 8: replace "PagerDuty/Opsgenie" with "UptimeRobot + Sentry + Slack."

### Claude's Discretion

- Exact Railway service layout (one service per role vs. monorepo multi-target).
- Vercel project settings (monorepo root, build command, output dir) — follow existing `frontend/` Vite config.
- UptimeRobot monitor configuration details (interval, alert contacts).
- Cloudflare DNS record TTLs.
- Whether staging uses `staging.kendrew.ai` / `app-staging.kendrew.ai` subdomains or Vercel/Railway default URLs — prefer custom subdomains for clarity.
- Structure of `docs/deploy.md` (table, runbook, or both).
- Supabase connection pooling mode — use Supavisor transaction mode (port 6543) unless research flags a blocker.

### Deferred Ideas (OUT OF SCOPE)

- PagerDuty / Opsgenie on-call rotation (punt until paying customer with uptime SLO).
- OpenTelemetry / self-hosted APM.
- Doppler / 1Password Secrets Automation.
- Modal stdout → Sentry shipping.
- Axiom / Better Stack log aggregation.
- Real-time Stripe meter-event watcher.
- Blocking auto-rollback on smoke failure.
- Monthly RunPod/Modal invoice reconciliation report (Phase 7).
- Staging Stripe test-mode billing reconciliation.
- Supabase connection pooling deep-dive beyond transaction-mode default.
- Frontend CSP/HSTS tightening.
- Full DMARC-to-reject progression.
</user_constraints>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| — | Phase 11 has no new REQ-IDs. All v1 requirements map to earlier phases. | N/A — research informs HOW to deploy existing behavior, not what feature bar to hit. Success criteria come from ROADMAP.md §Phase 11 (as corrected by CONTEXT.md). |

## Project Constraints (from CLAUDE.md)

Workspace-level constraints from `C:\Users\lab\Documents\Claude_projects\CLAUDE.md` (project-level `CLAUDE.md` not present in `llm-proteinDesigner/`):

- **Brand:** Kendrew is a separate brand from Ranomics. Ranomics pixel / analytics must NOT appear on `kendrew.ai` domain properties.
- **Python style:** PEP 8, Google-style docstrings. Fail fast with informative messages, never silently pass exceptions. (Existing `webhooks/router.py` and `worker/cleanup.py` already conform — Phase 11 changes must preserve.)
- **Python runtime on dev machine:** `venv\Scripts\python.exe` to avoid PATH issues. Railway + GH Actions use Python 3.11 (`.github/workflows/test.yml`) and 3.12 (`backend/Dockerfile`) — standardize on 3.11 across CI + Railway to match `test.yml`.
- **Node runtime on dev machine:** `/c/Program Files/nodejs`. GH Actions uses Node 20.
- **File paths:** relative, not absolute. No hardcoded paths — everything via config/env.
- **Dependencies:** pin in `requirements.txt` / `package-lock.json`. (Already done — `backend/requirements.txt` has pinned versions including `modal==1.4.2`, `arq==0.27.0`, `sentry-sdk[fastapi]==2.20.0`, `resend==2.25.0`.)
- **Content:** no emojis in written artifacts / docs. Deploy runbook in `docs/deploy.md` must follow.

## Standard Stack

### Core Platforms (all decided, versions current as of 2026-04-24)

| Platform | Role | Verified Version / Date | Why Standard |
|----------|------|-------------------------|--------------|
| Vercel | Frontend (Vite + React build) | Current — docs updated 2026 | `[VERIFIED: vercel.com/docs/domains/troubleshooting]` Dominant host for Vite/React; first-class GitHub integration, per-env env vars, deployment checks tied to GH Actions. |
| Railway | Backend + worker (Docker) | Pre-deploy command GA 2025-01-10 | `[VERIFIED: railway.com/changelog/2025-01-10-pre-deploy-command]` Docker-first, supports predeploy hooks via `railway.toml`, Nixpacks optional. |
| Cloudflare | DNS + R2 + tunnels | Current — account already in use | Already the account managing `kendrew.ai` DNS and R2. Orange/grey cloud per hostname. |
| Supabase Cloud Pro | Postgres + Auth | Pro plan required for each env | `[CITED: supabase.com/docs/guides/database/connecting-to-postgres]` — Pro plan unlocks point-in-time backups, higher connection limits, non-paused DBs. |
| Upstash Redis | Queue (arq) + pub/sub + session cache | Global — TLS by default | `[CITED: upstash.com/docs/redis/howto/connectclient]` — TLS by default, `rediss://` URL scheme works unchanged with redis-py and arq. |
| Cloudflare R2 | Object storage (PDB files, results) | S3-compat — already in config via `s3_endpoint_url` | Per-env buckets (`kendrew-staging`, `kendrew-prod`) prevent cross-env PDB leakage. |
| Modal | GPU execution | `modal==1.4.2` in `requirements.txt`; pinned to `>=0.63,<1` in `deploy-modal.yml` | `[VERIFIED: modal.com/docs/guide/environments]` — supports named environments (`staging`, `main`) via `--env` flag. **Version conflict:** CI installs Modal `>=0.63,<1` but `requirements.txt` pins `modal==1.4.2`. Align to 1.4.x in CI. |
| Sentry | Error tracking + Perf | `sentry-sdk[fastapi]==2.20.0` (backend), `@sentry/react==8.55.1` (frontend) | `[CITED: docs.sentry.io/platforms/python/integrations/fastapi/]` FastAPI integration auto-enables with the SDK when `fastapi` is in deps. |
| UptimeRobot | External liveness ping | Free tier: 50 monitors at 5-min intervals | `[VERIFIED: uptimerobot.com/pricing/]` — free tier supports Slack/email notifications. |
| Resend | Transactional email | `resend==2.25.0` in requirements, `jobs@kendrew.ai` sender already configured | `[CITED: resend.com/docs/dashboard/domains/dmarc]` Domain verification via SPF/DKIM/DMARC TXT records. |

### Version Verification

The `backend/requirements.txt` pins are current (Sentry 2.20.0, Modal 1.4.2, Resend 2.25.0, arq 0.27.0, slowapi 0.1.9). No upgrades required for Phase 11 — the phase is configuration, not dependency bumps. One alignment needed:

- `.github/workflows/deploy-modal.yml` installs `modal>=0.63,<1` (line 93). The codebase uses `modal==1.4.2`. The draft Modal workflow must be updated to `modal>=1.4,<2` before flipping the `if: false` guard. `[VERIFIED: backend/requirements.txt + .github/workflows/deploy-modal.yml line 93]`

### Alternatives Considered (all rejected per CONTEXT.md)

| Instead of | Could Use | Why Rejected |
|------------|-----------|--------------|
| Railway | Fly.io, Render | Decided in ROADMAP. |
| Vercel | Netlify, Cloudflare Pages | Decided. |
| Modal | RunPod primary | RunPod quarantined to emergency fallback per Phase 10. |
| Platform-native secrets | Doppler, 1Password | D-09 — vendor drift too high for solo-engineer launch. |
| UptimeRobot + Slack | PagerDuty / Opsgenie | D-13 — no on-call rotation yet. |
| Cloudflare proxy for app.* (orange) | — | D-02 — proxy breaks LetsEncrypt DNS-01 and interferes with SSE streaming. |

## Architecture Patterns

### Recommended Wave / Task Structure

```
Wave 0 (foundation, no external writes):
  - RESEARCH validated, docs/deploy.md skeleton committed

Wave 1 (provision external resources — parallel):
  - Supabase Pro project: kendrew-prod + kendrew-staging
  - Upstash Redis: kendrew-prod + kendrew-staging (TLS enabled)
  - R2 buckets: kendrew-prod + kendrew-staging + API token scoped per bucket
  - Modal environments: create `staging` (main exists); issue MODAL_TOKEN_ID/SECRET
  - Resend domain verification for kendrew.ai (SPF/DKIM/DMARC)

Wave 2 (DNS + SSL):
  - Cloudflare records: apex (A 76.76.21.21 orange), www (CNAME -> apex), app (CNAME to Railway grey), app-staging (CNAME grey), staging (CNAME to Vercel, proxied choice)
  - Railway custom domain: app.kendrew.ai, app-staging.kendrew.ai
  - Vercel custom domain: kendrew.ai + www redirect + staging.kendrew.ai

Wave 3 (platform deploys):
  - Railway service config: backend-prod + worker-prod (+ staging twin). railway.toml with predeploy hook.
  - Vercel project config: root=frontend/, build=npm run build, output=dist, env-per-environment wiring
  - Deployment checks: Vercel reads test.yml status; Railway uses GitHub integration gated on main branch push
  - Modal: remove `if: false` from deploy-modal.yml; bump modal>=1.4,<2; set MODAL secrets

Wave 4 (observability):
  - Sentry project: kendrew-backend + kendrew-frontend (separate DSNs). Slack integration → #kendrew-alerts.
  - UptimeRobot monitors: app.kendrew.ai/health (5 min), app-staging.kendrew.ai/health (5 min)
  - Sentry Performance: @sentry.start_transaction on 5 hot paths (or traces_sampler with route filter)
  - Wire smoke.yml workflow_run trigger off Railway deploy (or deployment_status webhook)

Wave 5 (secret rotation + audit):
  - webhooks/router.py: accept current + _PREV secret in verify_runpod_signature + heartbeat verify
  - .env.example full rewrite per D-11 (prod placeholders, runtime-scope comments)
  - docs/deploy.md: env matrix table + secret rotation runbook + rollback runbook
  - Manual validation: smoke.yml run against prod + staging, rotation dry-run on staging
```

### Pattern 1: Railway Predeploy via `railway.toml`

**What:** Railway runs a `preDeployCommand` between build and deploy. Failure aborts the rollout.

**When to use:** Schema migrations must land before new code starts handling traffic.

**Example:**
```toml
# railway.toml at repo root (or per-service in Railway UI)
[build]
builder = "DOCKERFILE"
dockerfilePath = "backend/Dockerfile"

[deploy]
preDeployCommand = ["supabase", "db", "push", "--db-url", "$DATABASE_URL", "--yes"]
# Or, if we install psql + a helper script:
# preDeployCommand = ["./scripts/run_migrations.sh"]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
```

**Source:** `[CITED: docs.railway.com/deployments/pre-deploy-command + docs.railway.com/config-as-code/reference]`

**Critical gotcha:** D-06 specifies `alembic upgrade head`, but the codebase uses Supabase SQL migrations. Options for the planner:
- **Option A (recommended):** Use Supabase CLI. Bundle the CLI into the Docker image or install during `preDeployCommand`. Supabase CLI can apply migrations from `supabase/migrations/` against any Postgres URL via `supabase db push --db-url $DATABASE_URL`.
- **Option B:** Use a one-liner wrapper that `psql -f` each migration file in sorted order, tracking applied migrations in a table (reinvents `schema_migrations`).
- **Option C:** Introduce Alembic now and migrate existing SQL files — scope creep, rejected.

Pick Option A and flag to user for confirmation.

### Pattern 2: Vercel Deployment Checks on GitHub Actions

**What:** Vercel waits on a named GitHub Actions check before promoting to production.

**When to use:** Enforce CI gates at the platform level (D-05).

**Approach:**
1. Vercel Project → Settings → Deployment Checks → Enable → Provider: GitHub.
2. Select the `test.yml` jobs to require: `Backend Tests`, `Frontend Unit Tests`, `E2E Tests`, `Lint & Type Check`.
3. Vercel gates the deployment on those four check runs green.

**Source:** `[CITED: vercel.com/docs/deployment-checks]`

**Critical caveat — do not double-gate:** "Due to GitHub's implementation of Check Runs, these will collide and introduce race conditions when used with GitHub branch protection rules, GitHub rulesets, and Vercel Deployment Checks." `[CITED: vercel.com/docs/deployment-checks]` Choose ONE: either GitHub branch protection with "Require status checks before merging" (merging gates; Vercel then always deploys main), OR Vercel Deployment Checks (Vercel waits post-merge). The simpler pattern: GitHub branch protection as the source of truth, `main` only accepts PRs with test.yml green, Railway + Vercel deploy on push to main without additional check gates.

### Pattern 3: Cloudflare DNS with Mixed Proxy Modes

**What:** Apex proxied (Cloudflare CDN), app subdomain DNS-only (direct to Railway).

**When to use:** Vercel apex benefits from Cloudflare CDN; Railway backend needs direct LetsEncrypt validation and unproxied SSE streams.

**DNS records (concrete):**

| Name | Type | Value | Proxy | TTL | Purpose |
|------|------|-------|-------|-----|---------|
| `kendrew.ai` (`@`) | A | `76.76.21.21` | Proxied (orange) | Auto | Vercel apex |
| `www` | CNAME | `cname.vercel-dns.com` | Proxied (orange) | Auto | www → apex redirect |
| `app` | CNAME | `<railway-provided-target>.up.railway.app` | DNS only (grey) | Auto | Railway backend prod |
| `app-staging` | CNAME | `<railway-staging-target>.up.railway.app` | DNS only (grey) | Auto | Railway backend staging |
| `staging` | CNAME | `cname.vercel-dns.com` | Proxied (orange) | Auto | Vercel frontend staging |
| `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@kendrew.ai` | — | Auto | Resend DMARC |
| `@` | TXT (SPF) | `v=spf1 include:amazonses.com ~all` (or Resend-provided value) | — | Auto | Resend SPF |
| `resend._domainkey` | TXT (DKIM) | `<Resend-provided DKIM value>` | — | Auto | Resend DKIM |
| `send` (or Resend-provided hostname) | MX | `<Resend-provided mail server>` + priority 10 | DNS only (grey) | Auto | Bounce handling |
| `@` | CAA | `0 issue "letsencrypt.org"` and `0 issue "pki.goog"` | — | Auto | LetsEncrypt allow (Railway), Google Trust (Vercel). |

**Source:** `[CITED: docs.railway.com/networking/domains/working-with-domains + vercel.com/kb/guide/a-record-and-caa-with-vercel + resend.com/docs/dashboard/domains/dmarc]`

**Gotchas:**
- MX and mail-related records **must** be grey cloud — proxying breaks SMTP. `[CITED: cloudflare Resend docs guidance]`
- If Railway cert stuck on "Validating Challenges": toggle Cloudflare proxy OFF, wait for green check in Railway, then leave OFF (D-02 says `app.kendrew.ai` stays DNS-only permanently). `[CITED: docs.railway.com/networking/troubleshooting/ssl]`
- Vercel recommends `76.76.21.21` for apex; still works in 2026 but Vercel is shifting to dynamic records. `[CITED: vercel.com/kb/guide/a-record-and-caa-with-vercel]`

### Pattern 4: Modal Multi-Environment Deploys

**What:** `modal deploy --env staging|main infrastructure/modal/<app>_app.py` deploys the same app definition to isolated environments.

**When to use:** Gate GPU infra behind PR/staging before production promotion.

**Source:** `[CITED: modal.com/docs/guide/environments + modal.com/docs/reference/cli/deploy]`

**Deploy-modal.yml workflow (flip-on plan):**
1. Remove `if: false` guard at `.github/workflows/deploy-modal.yml` line 60 and the comment-on-pr guard at line 138.
2. Bump Modal pin from `modal>=0.63,<1` to `modal>=1.4,<2` (line 93) to match `requirements.txt`.
3. Set GH repo secrets: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`.
4. Pre-create `staging` environment: `modal environment create staging` (one-time, run by Leo locally).
5. Confirm five app files exist at HEAD (they do as of 2026-04-24): `bindcraft_app.py`, `boltzgen_app.py`, `pxdesign_app.py`, `rfantibody_app.py`, `rfdiffusion_app.py`. `[VERIFIED: ls infrastructure/modal/]`
6. Each Modal env needs its own R2 credentials + backend webhook URL injected via `modal secret create kendrew-{env}`.

**Source:** `[VERIFIED: .github/workflows/deploy-modal.yml lines 60, 93, 138]`

### Pattern 5: Supabase Connection Pooling with FastAPI + asyncpg

**What:** Route backend DB connections through Supavisor transaction-mode pooler (port 6543) instead of direct Postgres (port 5432).

**When to use:** Any FastAPI async app running on a multi-replica platform (Railway). Transaction mode maximizes concurrency and supports many short-lived connections.

**Source:** `[CITED: supabase.com/docs/guides/database/connecting-to-postgres + supabase.com/docs/guides/troubleshooting/supavisor-faq-YyP5tI]`

**Critical gotcha — prepared statements:** Transaction mode reassigns server connections between transactions, so prepared statements aren't guaranteed to persist. asyncpg creates prepared statements by default; this breaks under Supavisor transaction mode. `[CITED: github.com/supabase/supabase/issues/39227 + supabase.com/docs/guides/troubleshooting/disabling-prepared-statements-qL8lEL]`

**Fix for the Kendrew backend:**
```python
# backend/db/connection.py — asyncpg pool creation
import asyncpg
pool = await asyncpg.create_pool(
    dsn=settings.database_url,
    statement_cache_size=0,           # disable asyncpg prepared statement cache
    max_inactive_connection_lifetime=0,
    min_size=2, max_size=10,
)
```

**Pool sizing (for Railway 2-replica backend + 2-replica worker):**
- Backend: `min_size=2, max_size=10` per replica → 20 connections under heavy load.
- Worker (arq, single job at a time): `min_size=1, max_size=3` per replica → 6 connections.
- Supabase Pro plan allows 40 concurrent direct connections + higher via Supavisor. With transaction pooling, effective ceiling is much higher.
- Total provisioned ≤ 30 connections; well under Pro plan ceiling.

**DATABASE_URL format (prod):**
```
# Supavisor transaction pooler (port 6543)
postgresql://postgres.<project-ref>:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

**Source:** `[CITED: supabase.com/docs/guides/database/connecting-to-postgres]`

### Pattern 6: Dual-Secret Webhook Rotation

**What:** Backend accepts signatures from either the current secret or the previous one during a rotation window.

**When to use:** Secret rotation without downtime — new secret deployed first, then pushed to Modal/RunPod, then PREV retired.

**Current code (backend/webhooks/router.py lines 50-76):**
```python
def validate_runpod_signature(body: bytes, signature: str | None) -> None:
    if not settings.runpod_webhook_secret:
        return
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")
    expected = hmac.new(settings.runpod_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
```

**Minimal diff for D-10:**
```python
def validate_webhook_signature(
    body: bytes,
    signature: str | None,
    current_secret: str,
    prev_secret: str | None = None,
) -> str:
    """Verify signature against current then _PREV secret. Return which one matched.

    Returns:
        "current" if the current secret matched, "prev" if the previous, else raises.
    """
    if not current_secret:
        return "dev-skip"  # local dev only
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")
    for label, secret in (("current", current_secret), ("prev", prev_secret)):
        if not secret:
            continue
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            if label == "prev":
                logger.warning("Webhook signed with PREV secret — rotation window active")
            return label
    raise HTTPException(status_code=401, detail="Invalid signature")
```

Then update the two call sites in `router.py` (line 89 runpod_webhook, line 302 heartbeat_webhook):
```python
which = validate_webhook_signature(
    body,
    request.headers.get("X-RunPod-Signature") or request.headers.get("X-Modal-Signature"),
    settings.runpod_webhook_secret,
    settings.runpod_webhook_secret_prev,  # new field
)
```

**New Pydantic Settings fields (backend/config.py):**
```python
runpod_webhook_secret_prev: str = ""
modal_webhook_secret: str = ""         # new — currently absent from config.py
modal_webhook_secret_prev: str = ""
```

Note: `config.py` currently has `runpod_webhook_secret` but **not** `modal_webhook_secret`. Modal apps today reach the backend via `/webhooks/runpod` (confirmed in `webhooks/router.py` — both providers POST to `/webhooks/runpod`). Phase 11 can either keep that single endpoint + single secret, or split. Recommendation: keep single endpoint, add provider-specific secret selection based on a header (`X-Webhook-Provider: modal|runpod`) if/when Modal ever sends a distinct signature — for v1 the RunPod secret path is sufficient since the container-posts-to-backend contract is shared.

**Rotation runbook (docs/deploy.md):**
1. Generate new secret: `openssl rand -hex 32`.
2. Set `RUNPOD_WEBHOOK_SECRET_PREV = <current value>` in Railway Variables.
3. Set `RUNPOD_WEBHOOK_SECRET = <new value>` in Railway Variables. Railway restarts backend.
4. Update Modal secret: `modal secret create --env main kendrew-webhook WEBHOOK_SECRET=<new value>`. Redeploy Modal apps.
5. Wait 1 hour (longest in-flight job chunk) + observe Sentry/logs for "PREV secret" warnings.
6. Clear `RUNPOD_WEBHOOK_SECRET_PREV` in Railway. Rotation complete.

### Anti-Patterns to Avoid

- **Auto-rollback on smoke failure.** D-08 locked manual. Smoke is informational; alert and let the human decide.
- **Cloudflare orange-cloud for `app.kendrew.ai`.** Breaks LetsEncrypt DNS-01 validation and interferes with SSE streams (Cloudflare enforces a 100-second timeout by default on free tier). D-02 locks this as DNS-only.
- **Sharing R2 bucket between prod and staging.** PDB data leakage across envs. D-03 requires separate buckets.
- **Putting `SUPABASE_SERVICE_ROLE_KEY` in Vercel.** Service role = admin-level DB access. Frontend must only get the anon key. D-12 is explicit.
- **Using Alembic.** No such config exists in the repo. See Summary point 1.
- **Running `supabase db push` from Railway without the CLI installed.** The Docker image does not bundle the Supabase CLI. Phase 11 must add it to `backend/Dockerfile` or use a sidecar migration job.
- **Proxy-on for MX / DKIM / mail hostnames.** Breaks SMTP. Grey cloud for every mail-related record.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LetsEncrypt cert issuance + renewal | Custom acme.sh on Railway | Railway auto-issue | Railway handles LE challenges internally; hand-rolled cert management fails renewal silently. |
| Postgres connection pooling | asyncpg with huge `max_size` | Supavisor transaction mode + asyncpg `statement_cache_size=0` | Per-connection limits blow up; platform pool handles scaling and resets. |
| DNS failover / health-routing | Custom Route 53 rules | UptimeRobot alerts + manual promote | Platform can't recover from Railway outage automatically at v1 scale. |
| Build cache for CI | Manual tar of deps | actions/cache with `hashFiles()` keys | Already in test.yml; reuse the same cache keys. |
| Secret rotation bookkeeping | Post-it note / spreadsheet | Dated entry in `docs/deploy.md` runbook + git commit | Audit trail + gets picked up by future you when debugging "why did the webhook fail?" |
| Stripe daily revenue sum across customers | Loop over `meter_event_summaries` per customer | Sum `public.jobs.gpu_cost_usd` in `check_daily_gpu_spend` (already implemented) | DB sum is O(1), already trusted by billing reconciliation. |
| Post-deploy rollback logic | Custom deployment state machine | Railway/Vercel "promote previous deployment" UI + manual | D-08 locks this. 5-minute manual rollback is acceptable. |
| CORS fine-tuning during deploy | Ad-hoc middleware patches | Already-environment-gated `CORS_ORIGINS` Pydantic setting | `backend/config.py:38` reads from env — just set per-env values. |

**Key insight:** Phase 11 is configuration, not new code. Where a platform offers a native mechanism (pre-deploy, domain check, built-in cert), use it. Every "clever" custom path becomes a 2am debugging session when it fails.

## Common Pitfalls

### Pitfall 1: Alembic-that-isn't

**What goes wrong:** Planner writes a `preDeployCommand = ["alembic", "upgrade", "head"]` per D-06. Railway deploy fails because Alembic isn't installed and no `alembic.ini` exists. Engineer debugs for an hour.

**Why it happens:** D-06 assumes Alembic based on FastAPI convention; this repo chose Supabase migrations.

**How to avoid:** Before writing the predeploy command, verify the migration tool: `ls supabase/migrations/` → SQL files → use `supabase db push`. Alternatively, add Alembic as a Wave 0 task if the team wants to switch — but that's scope creep.

**Warning signs:** `grep -ri alembic backend/` returns nothing.

### Pitfall 2: asyncpg prepared-statement collision with Supavisor

**What goes wrong:** Backend deploys, immediate flood of `DuplicatePreparedStatementError` from asyncpg. Production 500s.

**Why it happens:** Supavisor transaction mode reuses server connections; asyncpg assumes its own connections persist and caches prepared statements.

**How to avoid:** Set `statement_cache_size=0` in `asyncpg.create_pool()`. Add a smoke test that hits `/health` 20 times in rapid succession before declaring deploy green.

**Warning signs:** Sentry errors like `prepared statement "__asyncpg_stmt_1__" does not exist`.

### Pitfall 3: Cloudflare proxy breaking LetsEncrypt on app.kendrew.ai

**What goes wrong:** Railway shows "SSL pending" forever. Users see cert errors.

**Why it happens:** Cloudflare proxy intercepts Let's Encrypt HTTP-01/DNS-01 challenges, and Railway's validation path doesn't reach the origin.

**How to avoid:** Grey cloud from the start for `app.*`. If it's already orange, flip to grey, wait for Railway green checkmark, leave grey per D-02.

**Warning signs:** Cert "Validating Challenges" for >30 minutes in Railway dashboard.

### Pitfall 4: Frontend env vars at runtime vs. build time on Vercel

**What goes wrong:** `VITE_SENTRY_DSN` shows as empty in the browser even though it's set in Vercel.

**Why it happens:** Vite inlines `VITE_*` env vars at build time only. Changing them after a deploy has zero effect without a rebuild.

**How to avoid:** Any frontend env var change triggers a new Vercel deploy. Document this in `docs/deploy.md`. For non-`VITE_` prefixed vars, they're not exposed to the browser at all.

**Warning signs:** Frontend Sentry DSN updated in Vercel, but errors still go to old project or nowhere.

### Pitfall 5: Putting service-role key in Vercel

**What goes wrong:** Anyone with frontend access can admin the Supabase DB.

**Why it happens:** Copy-paste from Railway env matrix.

**How to avoid:** D-12 is explicit. Vercel gets ONLY: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_STRIPE_PUBLISHABLE_KEY`, `VITE_SENTRY_DSN_FRONTEND`, `VITE_APP_BASE_URL`. Document in `docs/deploy.md`, code-review any future changes.

**Warning signs:** `grep -r SUPABASE_SERVICE_ROLE frontend/` returns any hits.

### Pitfall 6: Cloudflare _dkim / _domainkey NS records blocking Resend

**What goes wrong:** Resend domain verification stuck on DKIM.

**Why it happens:** Cloudflare sometimes auto-adds NS records at `_dkim` or `_domainkey` subdomains that prevent TXT record resolution.

**How to avoid:** If DKIM won't verify in Resend, delete any `NS` records at `_dkim` and `_domainkey` in Cloudflare. `[CITED: community.cloudflare.com/t/i-have-set-up-dkim-as-presented-in-resend-but-it-is-not-verified/806974]`

**Warning signs:** Resend "DKIM not verified" after 1 hour of propagation.

### Pitfall 7: Modal CLI version mismatch in CI vs app

**What goes wrong:** `deploy-modal.yml` installs `modal>=0.63,<1` but apps were written against Modal 1.4.x APIs. Deploy fails with obscure import error.

**Why it happens:** Draft workflow was written when 0.x was current.

**How to avoid:** Update line 93 to `modal>=1.4,<2` before flipping the `if: false` guard.

**Warning signs:** CI fails with `ImportError` on `modal.environment` or similar.

### Pitfall 8: Vercel Deployment Checks + GitHub branch protection race

**What goes wrong:** Merges succeed but Vercel deploys a stale commit. Or: Vercel shows check pending forever.

**Why it happens:** Vercel's Check Runs and GitHub's Check Runs collide. Documented race condition.

**How to avoid:** Pick ONE gating mechanism. Recommended: GitHub branch protection only — Vercel and Railway always deploy on push to `main`, because only green PRs reach `main`. `[CITED: vercel.com/docs/deployment-checks]`

**Warning signs:** Intermittent deploy promotion failures after merge.

## Runtime State Inventory

*Phase 11 is greenfield deployment — no existing production state to migrate. The existing dev environment (`.env.local`, docker-compose, trycloudflare tunnel) does NOT need rename/migration; it stays as-is for local development.*

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None. No production DB exists yet. | None. |
| Live service config | `bobby-functions-easier-methodology.trycloudflare.com` hardcoded in `.env.local` as `APP_BASE_URL`. No production version of this resource yet. | Dev keeps trycloudflare; prod + staging get new Railway domains. Zero data migration. |
| OS-registered state | None (no tasks scheduled against `kendrew.ai` yet). | None. |
| Secrets / env vars | `.env.example` is dev-only; `.env.local` is dev-only. No prod secrets in git. | Wave 5: full `.env.example` rewrite per D-11. No existing prod secret key names change. |
| Build artifacts | None — no production builds have run. | None. |

**Canonical question:** After every file in the repo is updated, what runtime systems still have the old string cached, stored, or registered? → Nothing, because nothing is live yet. Phase 11 is initial cutover.

## Code Examples

### 1. Railway `railway.toml` (backend service)

```toml
# railway.toml — backend prod (mirror file or Railway UI for staging)
[build]
builder = "DOCKERFILE"
dockerfilePath = "backend/Dockerfile"

[deploy]
# D-06 implementation. See Pitfall 1 — use supabase CLI, not alembic.
# Requires supabase-cli installed in the Dockerfile (add RUN layer).
preDeployCommand = [
  "supabase", "db", "push",
  "--db-url", "$DATABASE_URL",
  "--yes"
]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'"
healthcheckPath = "/health"
healthcheckTimeout = 30
numReplicas = 2
restartPolicyType = "ON_FAILURE"
```

Source: `[CITED: docs.railway.com/config-as-code/reference]`

### 2. asyncpg pool init for Supavisor transaction mode

```python
# backend/db/connection.py (current file, modified)
import asyncpg
from config import settings

async def get_db_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        # Required for Supavisor transaction mode (port 6543).
        # See pitfall 2.
        statement_cache_size=0,
        max_inactive_connection_lifetime=0,
        min_size=2,
        max_size=10,
    )
```

Source: `[CITED: github.com/supabase/supabase/issues/39227]`

### 3. Sentry init with hot-path performance sampling

```python
# backend/main.py (or startup hook)
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from config import settings

_HOT_PATHS = {
    "POST /agent/message",
    "POST /jobs/launch",
    "POST /webhooks/runpod",
    "POST /webhooks/heartbeat",
    "POST /jobs/{job_id}/upload-urls",
}

def traces_sampler(sampling_context):
    txn = sampling_context.get("transaction_context", {}).get("name", "")
    # Sample 100% of hot paths, 0% of cold (per D-14, free-tier budget).
    return 1.0 if txn in _HOT_PATHS else 0.0

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[FastApiIntegration(), StarletteIntegration()],
        traces_sampler=traces_sampler,
        environment=settings.sentry_environment,  # "production" or "staging"
        release=settings.git_sha,  # set at deploy time
    )
```

Source: `[CITED: docs.sentry.io/platforms/python/integrations/fastapi/ + docs.sentry.io/platforms/python/guides/fastapi/performance/]`

### 4. Frontend Sentry with source maps (Vite)

```typescript
// frontend/src/main.tsx
import * as Sentry from "@sentry/react";

if (import.meta.env.VITE_SENTRY_DSN_FRONTEND) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN_FRONTEND,
    environment: import.meta.env.MODE,
    tracesSampleRate: 0.1,  // 10% sampling; hot-path tagging client-side if needed
    integrations: [Sentry.browserTracingIntegration()],
  });
}
```

### 5. Dual-secret webhook verification (diff to webhooks/router.py)

See Pattern 6 above.

### 6. UptimeRobot monitor configuration (creation via dashboard)

```
Monitor Type: HTTPS
Friendly Name: kendrew-prod-health
URL: https://app.kendrew.ai/health
Monitoring Interval: 5 minutes
Alert Contacts: Email (leo@ranomics.com), Slack (#kendrew-alerts via webhook)
HTTP Method: GET
Expected Status: 200
```

Source: `[CITED: uptimerobot.com/pricing/]` — free-tier supports 5-min intervals and Slack notifications.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Legacy Stripe Usage Records API | Stripe Billing Meters API (meter events) | Migration guide 2024; required by 2025 | Already using; `stripe_client.record_gpu_usage` posts meter events with `job_id` as idempotency key. No change for Phase 11. |
| Vercel `cname.vercel-dns.com` only | Apex A record `76.76.21.21` + dynamic records | 2024 | Use A record for apex; CNAME for www. Still current in 2026. `[CITED: vercel.com/kb/guide/a-record-and-caa-with-vercel]` |
| Alembic as default FastAPI migration tool | Supabase CLI SQL migrations (project-specific choice) | Project started with Supabase | Must adjust D-06 wording. |
| Railway Procfile start command only | `railway.toml` `[deploy]` block with `preDeployCommand` | GA 2025-01-10 | `[CITED: railway.com/changelog/2025-01-10-pre-deploy-command]` Use for migrations. |
| PgBouncer on dedicated VM | Supavisor (Supabase-native) | Supabase Cloud default 2024+ | Transaction mode requires `statement_cache_size=0` for asyncpg. |
| RunPod serverless endpoints | Modal dedicated GPU environments | Phase 10 decision | Already reflected in config.py (`gpu_provider: "modal"` default). |

**Deprecated / outdated:**
- `.github/workflows/deploy-modal.yml` pin `modal>=0.63,<1` — must bump to `>=1.4,<2`.
- Vercel legacy CAA pattern without `pki.goog` — add both LE and Google Trust CAA entries for future-proofing.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `supabase db push --db-url $DATABASE_URL --yes` works unchanged in Railway's container environment with only the Supabase CLI binary installed. | Pattern 1, Code Example 1 | Railway deploys fail at the predeploy step with "supabase: command not found". Mitigation: add CLI install layer to `backend/Dockerfile`; verify with a staging deploy first. |
| A2 | Railway's `preDeployCommand` runs in the same container image as the service (so Supabase CLI installed in Dockerfile is available). | Pattern 1 | If Railway runs predeploy in a separate runner, CLI must be bundled differently (e.g., Node-based action or separate image). Verify in Railway docs during Wave 3. |
| A3 | The current `/health` endpoint (Phase 5 D-3B Layer 2) returns 200 only when DB + Redis + R2 are all reachable. | Validation Architecture | Smoke test can't distinguish partial failures. Planner should audit `backend/main.py` for actual health check contents before promising this. |
| A4 | Modal webhooks post to `/webhooks/runpod` (same endpoint as RunPod) signing with `RUNPOD_WEBHOOK_SECRET`, so only RunPod secret rotation is needed for Modal-in-production. | Pattern 6 | If Modal later signs with its own secret, `MODAL_WEBHOOK_SECRET` + `_PREV` plumbing still needs adding to config.py. Check `infrastructure/modal/*_app.py` post-webhook code. |
| A5 | Cloudflare TXT record updates for Resend DKIM propagate within 1 hour; domain verification succeeds without manual retry. | Pattern 3, Pitfall 6 | Verification may take up to 48 hours in rare cases. Not a blocker; plan docs wait period. |
| A6 | UptimeRobot free tier Slack integration supports channel webhook URL (not just email). | Pattern 4 UptimeRobot example | If free tier restricts Slack, use email → Slack email integration as fallback. |
| A7 | `staging.kendrew.ai` + `app-staging.kendrew.ai` are acceptable staging subdomain names (no brand confusion with prod). | User Constraints / Claude's Discretion | User may prefer different naming. Not a technical risk. |
| A8 | Railway 2-replica backend + 2-replica worker deployment matches the load assumption. | Pattern 5 pool sizing | If user provisions more replicas, pool sizing needs recalculation. Document formula. |
| A9 | `backend/Dockerfile` can be extended with `RUN curl -fsSL https://supabase.com/install.sh | sh` (or a pinned binary) without bloating image size unreasonably (<50MB addition). | Pitfall 1 fix | If image gets too large, Railway cold starts slow down. Alternative: separate migration-only job. |
| A10 | `docs/deploy.md` does not yet exist — Phase 11 creates it. | User Constraints | Verified: `ls docs/` shows only `SMOKE-TEST-SPEC.md`, `blocker-pxdesign.md`, `blocker-rfdiffusion.md`. `[VERIFIED]` |

**User-confirmation asks (planner should surface in discuss-phase):**
- **A1 / A2:** Confirm Supabase CLI approach for predeploy migration (vs. adopting Alembic vs. psql-runner script).
- **A4:** Confirm the single-secret model (Modal reuses RUNPOD_WEBHOOK_SECRET) vs. introducing a separate MODAL_WEBHOOK_SECRET now.
- **A7:** Confirm staging subdomain names.

## Open Questions

1. **Supabase CLI in Railway's runtime image.**
   - What we know: `supabase db push` is the idiomatic migration command for this repo.
   - What's unclear: whether Railway's `preDeployCommand` executes inside the build image (includes all Dockerfile layers) or a stripped runtime context.
   - Recommendation: Wave 0 plan includes a sub-task to verify by deploying a trivial predeploy command to staging first. If Railway provides the full image: proceed. If not: either add CLI to startCommand's container and run migrations from a script on boot (with a distributed lock to prevent double-run across replicas), or adopt a one-shot `migrate` service.

2. **Modal webhook signing.**
   - What we know: Current code only handles RunPod signature header (`X-RunPod-Signature`).
   - What's unclear: Whether Modal containers (running `run_pipeline.py`) sign webhooks with `RUNPOD_WEBHOOK_SECRET` or a Modal-specific secret.
   - Recommendation: grep `infrastructure/modal/*_app.py` and `backend/pipelines/` for webhook POST code. If they share the secret, proceed with single-secret rotation (A4). If not, add MODAL_WEBHOOK_SECRET now.

3. **Vercel staging deploy gating.**
   - What we know: Vercel auto-deploys all non-main branches as preview deployments; main as production.
   - What's unclear: How to expose `staging.kendrew.ai` cleanly if it's supposed to serve a specific non-main branch's preview.
   - Recommendation: Either (a) alias `staging.kendrew.ai` to a dedicated `staging` branch that auto-deploys as production on that branch, or (b) pin the domain to a specific preview URL. Option (a) is cleaner and Vercel docs support it via "Deploy Hooks" + branch-to-domain mapping.

4. **Sentry release tagging.**
   - What we know: `release=` should be set per deploy for accurate stack-trace symbolication.
   - What's unclear: How Railway exposes the git SHA to the running container.
   - Recommendation: Railway exposes `RAILWAY_GIT_COMMIT_SHA` env var; read that into `settings.git_sha`. Vercel exposes `VERCEL_GIT_COMMIT_SHA` equivalent.

5. **Playwright in smoke.yml.**
   - What we know: `smoke.yml` currently runs `frontend/e2e/smoke.spec.ts`.
   - What's unclear: Does that file exist yet? Phase 9 may have left it as a TBD.
   - Recommendation: Wave 4 task verifies spec exists; if not, write a minimal one that loads `/` and asserts `<title>Kendrew` appears.

## Environment Availability

Phase 11 is largely external-service config, not local tooling. The one hard dependency for the CI workflow is:

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Supabase CLI (local) | Wave 1 project provisioning | Assumed — Leo already runs `supabase start` locally | Latest | Install fresh: `npm i -g supabase` |
| Modal CLI (local) | Wave 1 env creation (`modal environment create staging`) | `modal==1.4.2` installed via `backend/requirements.txt` | 1.4.2 | Pin, reinstall |
| Cloudflare account + API token | Waves 1-2 DNS + R2 | In use already (grep for R2 usage in repo) | N/A | None — blocking |
| Railway account + GitHub integration | Wave 3 backend deploy | Must be verified / signup if missing | N/A | None — blocking |
| Vercel account + GitHub integration | Wave 3 frontend deploy | Must be verified / signup if missing | N/A | None — blocking |
| GitHub repo secrets admin | Waves 3-4 (set MODAL_TOKEN_*, etc.) | Leo has admin | N/A | None |
| Upstash account | Wave 1 | Must signup if missing | N/A | Fly.io Redis as alt (rejected by CONTEXT.md) |
| Sentry account with Slack integration | Wave 4 | Leo already has Sentry DSN in config (empty default) | — | None — blocking |
| Resend account + verified domain record access | Wave 1 + 4 | Already in use (`jobs@kendrew.ai` sender configured) | — | None |
| `openssl` binary (for secret generation) | Wave 5 rotation runbook | Standard on Linux/macOS/Git Bash | — | `python -c "import secrets; print(secrets.token_hex(32))"` |

**Missing dependencies with no fallback:** None blocking research. Planner should confirm during Wave 1 that Leo has accounts on Railway, Vercel, Upstash, Sentry, and Cloudflare (likely yes — all referenced in prior phases).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.5 (backend) + Vitest 4.1 (frontend) + Playwright (E2E/smoke); curl + jq for deploy smoke. |
| Config file | `backend/pytest.ini`, `frontend/vite.config.ts`, `frontend/playwright.config.ts`, `.github/workflows/smoke.yml`. |
| Quick run command | `cd backend && pytest -x tests/` (existing) + `curl -sf https://app.kendrew.ai/health` (new smoke check). |
| Full suite command | `.github/workflows/test.yml` (already wired); `.github/workflows/smoke.yml` (wired post-deploy by Phase 11). |

### Phase 11 Success Criteria → Test Map

Phase 11 has 9 ROADMAP success criteria (as corrected by CONTEXT.md for SC 6 and SC 8). Each needs a concrete, executable validation signal:

| SC # | Behavior (corrected) | Test Type | Automated Command / Check | Where it Lives |
|------|----------------------|-----------|---------------------------|----------------|
| SC-1 | Frontend deployed on Vercel with custom domain + SSL | smoke | `curl -sI https://kendrew.ai | grep -E 'HTTP/2 200\|strict-transport-security'` | smoke.yml (new check) + manual DNS check in deploy.md |
| SC-2 | Backend + worker deployed on Railway as Docker containers, auto-deploy from main | smoke + manual | `curl -sf https://app.kendrew.ai/health` returns 200 AND Railway deploy history shows commit SHA == latest main SHA | smoke.yml existing + Railway dashboard observation |
| SC-3 | Database on Supabase Cloud (Pro) with connection pooling + backups | integration | `/health` endpoint DB check; Supabase dashboard shows Pro tier + PITR backups enabled; `asyncpg` smoke test hits DB 20x rapidly without prepared-statement errors | smoke.yml + manual Supabase config audit |
| SC-4 | Redis on Upstash with TLS | integration | `/health` endpoint Redis check; verify `REDIS_URL` in Railway starts with `rediss://` (TLS scheme) | smoke.yml + Railway Variables audit |
| SC-5 | Object storage on R2 with presigned URL access | integration | `/health` R2 ping; exercise `POST /jobs/{id}/upload-urls` from a test user; download a sample PDB via presigned URL | smoke.yml (R2 HEAD) + Playwright e2e |
| SC-6 | **[CORRECTED]** GPU jobs dispatch to Modal from production backend (RunPod quarantined fallback) | integration | Modal `staging` env: `modal run --env staging infrastructure/modal/bindcraft_app.py::run_tool` completes tiny smoke payload; backend `POST /jobs/launch` in staging produces a Modal function call ID visible in Modal dashboard | deploy-modal.yml PR gate + manual staging test |
| SC-7 | Env vars + secrets managed via platform-native secret stores | audit | `grep -ri "SUPABASE_SERVICE_ROLE\|STRIPE_SECRET\|ANTHROPIC_API_KEY" frontend/` returns zero hits; `.env.local` is in `.gitignore`; every entry in `.env.example` has a runtime-scope comment | Wave 5 audit script + CI check (add grep step to test.yml) |
| SC-8 | **[CORRECTED]** Monitoring: Sentry errors + UptimeRobot pings + `#kendrew-alerts` Slack | integration | Trigger a test 500 on `/debug/sentry-test` endpoint, verify Sentry event arrives in kendrew-backend project; UptimeRobot monitor status "Up" for both prod + staging health URLs; post-deploy Slack message arrives in #kendrew-alerts | Manual smoke on first deploy + periodic re-verify in runbook |
| SC-9 | Rollback possible within 5 minutes via Railway/Vercel deploy history | runbook drill | Document a rollback drill in `docs/deploy.md`: (1) note current deploy SHA, (2) click "Redeploy" on previous deployment in Railway+Vercel, (3) verify `/health` returns 200 and site loads, (4) timer stops; target ≤ 5 min | Manual drill in Wave 5; add timer to runbook |

**Additional Phase-11-specific signals (not SC-derived but planner should include):**

| Signal | Test Type | Automated Command |
|--------|-----------|-------------------|
| Dual-secret webhook rotation works | integration | Send a webhook signed with PREV secret, verify 200 + "PREV secret" warning in Sentry; rotate; send with new secret, verify 200; retire PREV; send with PREV, verify 401. | Manual + Wave 5 task |
| DNS resolves correctly from outside Cloudflare | smoke | `dig +short A kendrew.ai` returns Vercel IP; `dig +short CNAME app.kendrew.ai` returns Railway CNAME; `dig +short TXT _dmarc.kendrew.ai` returns DMARC | smoke.yml new check |
| `deploy-modal.yml` dry-run | CI | Open a PR touching `infrastructure/modal/` — workflow must trigger and deploy to `staging` | Part of deploy-modal.yml |
| Sentry Performance captures hot paths | integration | Hit `POST /jobs/launch` in staging, see transaction in Sentry Performance dashboard within 1 minute | Manual Wave 4 validation |

### Sampling Rate
- **Per task commit (dev loop):** `pytest -x -k <module>` for changed code; `npm run test -- <file>` for frontend. For deploy-related plans: `curl -sf https://app-staging.kendrew.ai/health` before merge.
- **Per wave merge:** Full `test.yml` green (auto-enforced).
- **Phase gate:** `smoke.yml` run against both staging and prod; rollback drill completed; all 9 SC signals green; `.env.example` audit review.

### Wave 0 Gaps

- [ ] `frontend/e2e/smoke.spec.ts` — referenced by `smoke.yml` line 62 but must verify it exists; if not, write a minimal frontend-load assertion.
- [ ] `backend/debug.py` or `/debug/sentry-test` route (dev-only) — for SC-8 test. Guard with `if settings.debug or settings.testing`.
- [ ] `docs/deploy.md` — does not exist yet (`ls docs/` confirmed). Create with env-matrix table + rotation runbook + rollback drill.
- [ ] `railway.toml` — does not exist yet. Create at repo root.
- [ ] `supabase/config.toml` `[db.pooler] enabled` is currently `false` locally — for prod, Supavisor is Supabase-managed, nothing in this file matters. Do not enable local pooler.

## Security Threat Model Seeds

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Partial | Supabase Auth (unchanged this phase). Phase 11: verify cookie secure=true in prod via `COOKIE_SECURE=true` setting. |
| V3 Session Management | Partial | HTTP-only cookies already scoped. Phase 11: verify `APP_BASE_URL=https://app.kendrew.ai` so Set-Cookie `Domain` directive is correct. |
| V4 Access Control | No net-new | Existing auth/admin dependencies unchanged. |
| V5 Input Validation | No net-new | Pydantic Settings + request models from prior phases. |
| V6 Cryptography | Yes | HMAC-SHA256 webhooks (existing); dual-secret rotation (D-10). Never hand-roll — use stdlib `hmac.compare_digest`. |
| V9 Communications | Yes | HTTPS everywhere (auto via platform certs). TLS for Redis (`rediss://`). Supabase TLS enforced by the pooler URL. |
| V10 Malicious Code | Yes (supply chain) | `requirements.txt` pinned; `package-lock.json` committed; CI uses pinned action versions (`@v4`, `@v5`). |
| V14 Configuration | Yes (central to phase) | D-09, D-11, D-12 — platform-native secrets, env-scoped, runtime matrix. |

### Threat Patterns for Phase 11

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Accidental secret leak into Vercel build env (service-role key bundled into frontend JS) | Information Disclosure | Explicit env matrix in `docs/deploy.md`; Wave 5 `grep` audit; Vercel project setting "Sensitive" flag; CI grep step to fail builds containing `SUPABASE_SERVICE_ROLE` or `STRIPE_SECRET_KEY` in `frontend/dist/`. |
| Webhook replay during rotation window | Spoofing | 5-minute timestamp check already in `webhooks/router.py:126-134`. Dual-secret support doesn't extend the replay window — each signature is still time-bounded. |
| DNS hijack via Cloudflare API token compromise | Spoofing / Tampering | Scope Cloudflare API tokens to zone-specific DNS edit only (not global). Enable 2FA on Cloudflare account. Monitor DNS change logs in Cloudflare audit. |
| `supabase db push` partially applied then interrupted → split schema state | Tampering | Supabase CLI uses transactional migrations per-file. Failure aborts mid-file transaction. Separate failure: one migration succeeds, next fails — partial state. Mitigation: smoke.yml verifies health post-deploy; if schema broken, manual rollback via previous deploy + manual SQL fix forward. |
| R2 bucket public misconfiguration | Information Disclosure | R2 buckets default-private. Verify in Cloudflare dashboard. Presigned URLs are signed + expire in 1 hour (Phase 5 D-4B unchanged). |
| Staging leaking prod user data | Information Disclosure | D-03 mandates separate Supabase projects + R2 buckets per env. Enforce by naming convention (`kendrew-staging` vs `kendrew-prod`). No seed data copied from prod to staging. |
| Sentry transmitting PII | Information Disclosure | Sentry `send_default_pii=False` (default). Verify `before_send` filter strips Authorization headers. PDB uploads should never appear in stack traces — Sentry redacts request bodies by default for POST. |
| Cert exhaustion / LetsEncrypt rate limit | DoS on ourselves | LE allows 50 certs per registered domain per week. Not a concern at Phase 11 scale (prod + staging = 2 certs). Don't run repeated cert issuance in a loop. |
| `modal deploy` running from PR → leaked code into staging Modal env | Information Disclosure / Tampering | `deploy-modal.yml` triggers on `pull_request`, deploying to `staging` only. Production `main` env requires push to main (merged PR, already reviewed). Don't grant Modal staging token to untrusted PR authors (GH Actions default: secrets not exposed to fork PRs). |
| arq cron GPU spend alert email exposes customer identifiers in plaintext | Information Disclosure | Email content summarizes totals only ("GPU spend $X in 24h"), not per-customer. Already the case in `check_daily_gpu_spend`. |
| Rotation runbook skipped / forgotten | Operational | docs/deploy.md includes dated "last rotated" entry per secret. Set calendar reminder at 90-day mark. |
| Stripe webhook receiving events meant for staging in prod (or vice versa) | Tampering | Stripe webhook endpoints are env-specific (different URLs). Stripe sends to the URL configured per account; Leo's Stripe live vs test accounts already isolated. Verify Railway Variables have correct `STRIPE_SECRET_KEY` per env. |
| SSRF via PDB fetch routes reaching internal Railway metadata | Server-side request forgery | `pdb_utils/fetch.py` only calls RCSB and UniProt; constrain URL allowlist (already the case via `rcsb_base_url` / `uniprot_base_url` in config.py). |
| Supabase service role key exposure in Railway env var leakage (shell history, CI logs) | Information Disclosure | Railway masks env vars in logs by default. Never `echo $SUPABASE_SERVICE_ROLE_KEY` in predeploy scripts. Train secret-echoing out of any diagnostic steps. |

## Sources

### Primary (HIGH confidence)
- `[CITED: docs.railway.com/deployments/pre-deploy-command]` — Railway predeploy mechanism
- `[CITED: docs.railway.com/config-as-code/reference]` — railway.toml schema
- `[CITED: railway.com/changelog/2025-01-10-pre-deploy-command]` — GA date
- `[CITED: docs.railway.com/networking/domains/working-with-domains]` — Railway custom domain
- `[CITED: docs.railway.com/networking/troubleshooting/ssl]` — Cloudflare proxy gotcha
- `[CITED: vercel.com/docs/deployment-checks]` — Vercel / GH Actions integration + branch-protection race warning
- `[CITED: vercel.com/kb/guide/a-record-and-caa-with-vercel]` — 76.76.21.21 apex A record
- `[CITED: vercel.com/docs/domains/troubleshooting]` — Vercel DNS troubleshooting
- `[CITED: supabase.com/docs/guides/database/connecting-to-postgres]` — Supavisor pooler modes
- `[CITED: supabase.com/docs/guides/troubleshooting/supavisor-faq-YyP5tI]` — Transaction vs session mode
- `[CITED: supabase.com/docs/guides/troubleshooting/disabling-prepared-statements-qL8lEL]` — asyncpg fix
- `[CITED: github.com/supabase/supabase/issues/39227]` — Concrete asyncpg config
- `[CITED: modal.com/docs/guide/environments]` — Modal env semantics
- `[CITED: modal.com/docs/reference/cli/deploy]` — `modal deploy --env` flag
- `[CITED: modal.com/docs/guide/continuous-deployment]` — CI/CD patterns
- `[CITED: docs.sentry.io/platforms/python/integrations/fastapi/]` — FastAPI integration
- `[CITED: docs.sentry.io/platforms/python/guides/fastapi/performance/]` — traces_sampler API
- `[CITED: upstash.com/docs/redis/howto/connectclient]` — TLS + rediss:// default
- `[CITED: docs.stripe.com/api/billing/meter-event-summary/list]` — per-customer scope
- `[CITED: resend.com/docs/dashboard/domains/dmarc]` — Resend DNS records
- `[VERIFIED: repo scan 2026-04-24]` — `backend/requirements.txt`, `supabase/migrations/`, `backend/webhooks/router.py`, `.github/workflows/*.yml`, `backend/config.py`, `backend/worker/cleanup.py`

### Secondary (MEDIUM confidence)
- `[CITED: community.cloudflare.com/t/i-have-set-up-dkim-as-presented-in-resend-but-it-is-not-verified/806974]` — DKIM NS record pitfall
- `[CITED: uptimerobot.com/pricing/]` — Free tier features
- `[CITED: dmarcdkim.com/setup/how-to-setup-resend-spf-dkim-and-dmarc-records]` — Resend + Cloudflare config guide

### Tertiary (LOW confidence — not relied upon)
- None. All claims either verified in-repo or cited to official docs.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every platform verified against 2026 docs + pinned in `requirements.txt` / `package.json`.
- Architecture patterns: HIGH — Railway predeploy, Vercel checks, Cloudflare DNS, Modal envs, Supavisor pooling all confirmed in current docs.
- Pitfalls: HIGH for items 1-5 (directly verified against repo code). MEDIUM for item 6 (Cloudflare DKIM NS issue is community-reported, not official docs).
- Validation Architecture: HIGH — built on existing test.yml + smoke.yml infrastructure with concrete `curl`/`pytest`/`Playwright` commands.
- Security Threat Model: MEDIUM — seeded from standard deployment-phase threats; planner should expand per-plan in `<threat_model>` blocks.

**Research date:** 2026-04-24
**Valid until:** 2026-05-24 (platform docs change fast — particularly Railway config schema and Modal CLI versions).
