# Phase 11: Deployment - Context

**Gathered:** 2026-04-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Get Kendrew live on `kendrew.ai` and reachable by external users on production infrastructure. Hosting, DNS, SSL, secrets, CI/CD deploy flow, webhook secret rotation, and monitoring wiring built on Phase 5 (hardening), Phase 9 (testing/CI), and Phase 10 (legal). No new product features. Modal is the primary GPU provider (per Phase 10); the ROADMAP line naming RunPod is out of date and must be corrected as part of this phase.

</domain>

<decisions>
## Implementation Decisions

### Domains + Environments

- **D-01:** Production DNS layout: `kendrew.ai` serves the frontend (Vercel), `app.kendrew.ai` serves the backend API (Railway). Matches existing Phase 3 research references and email sender (`jobs@kendrew.ai`).
- **D-02:** DNS provider is Cloudflare — reuses the account already handling R2 and cloudflared tunnels. `kendrew.ai` apex proxied (orange cloud). `app.kendrew.ai` DNS-only (grey cloud) so Railway's LetsEncrypt validation and SSE streams work unmodified.
- **D-03:** Full prod + staging split. Each platform gets a staging instance alongside prod:
  - Railway: `kendrew-backend-staging` + `kendrew-backend-prod` services (and matching worker services).
  - Vercel: staging preview alias on a non-prod domain (e.g. `staging.kendrew.ai` CNAME) + prod on `kendrew.ai`.
  - Modal: `staging` + `main` environments (draft `deploy-modal.yml` already separates these).
  - Supabase Cloud: separate `kendrew-staging` and `kendrew-prod` projects (each Pro plan).
  - Upstash Redis: separate `kendrew-staging` and `kendrew-prod` databases, both TLS.
  - Cloudflare R2: separate buckets per env (`kendrew-staging`, `kendrew-prod`) to prevent cross-env PDB leakage.
- **D-04:** SSL certs are platform-managed. Vercel and Railway auto-issue and renew LetsEncrypt. No Cloudflare full-strict — Cloudflare stays DNS-only for `app.kendrew.ai`.

### CI/CD Deploy Flow

- **D-05:** Deploys from `main` are gated by Phase 9's `test.yml` green status. Railway and Vercel auto-deploy on push to main only after the CI gates (backend tests, frontend tests, E2E, lint+typecheck, coverage) pass. Use Railway/Vercel GitHub checks so a red CI blocks the platform deploy.
- **D-06:** Alembic migrations run automatically as a Railway predeploy command (`alembic upgrade head`) before the new container receives traffic. Failed migration aborts the deploy. Prod and staging backends both run this.
- **D-07:** Flip on the drafted `.github/workflows/deploy-modal.yml` workflow: PRs touching `infrastructure/modal/**`, `docker/**`, or `backend/pipelines/**` deploy to Modal `staging`; push to `main` deploys to Modal prod. Remove the `if: false` guard, set `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` secrets, and confirm the five app paths exist at HEAD.
- **D-08:** Post-deploy smoke test (Phase 9 D-11 — `.github/workflows/smoke.yml`) is informational. Runs after deploy completes, posts failures to Sentry + `#kendrew-alerts`. Human decides rollback via Railway/Vercel deploy history (Phase 9 D-12). No auto-rollback.

### Secrets + Env Management

- **D-09:** Prod secrets live in platform-native stores only: Railway Variables (backend + worker), Vercel Env Vars (frontend), Modal Secrets (GPU apps). No Doppler / 1Password sync — too much vendor drift for a solo-engineer launch. Each secret scoped per environment.
- **D-10:** Webhook secret rotation (deferred from Phase 5): implement dual-secret grace-period support. Backend accepts both `RUNPOD_WEBHOOK_SECRET` / `MODAL_WEBHOOK_SECRET` and a `_PREV` variant during a rotation window so secrets can rotate without downtime. Documented rotation runbook lives in `docs/deploy.md`.
- **D-11:** Full `.env.example` audit. Every prod-relevant key must appear with a placeholder and a comment explaining which runtime needs it: Sentry DSN (backend + frontend), Stripe live keys (backend), Resend API key (backend), Upstash Redis URL (backend + worker), R2 credentials + bucket (backend + worker + modal), UptimeRobot monitor ID (optional), webhook secrets + _PREV variants, Modal tokens (worker + CI), Supabase service role (backend only — never frontend).
- **D-12:** Env vars scoped explicitly per runtime in `docs/deploy.md`:
  - Backend/worker (Railway): all secret keys (Stripe secret, Anthropic, Supabase service role, Modal tokens, R2 credentials, webhook secrets, Resend).
  - Frontend (Vercel): public values only (Supabase anon key, `APP_BASE_URL` = `https://app.kendrew.ai`, Stripe publishable key, frontend Sentry DSN).
  - Modal apps: R2 credentials (for result upload), backend job-token auth, heartbeat endpoint URL.
  A matrix table in `docs/deploy.md` is the source of truth.

### Monitoring Reconciliation

- **D-13:** On-call stack stays as Phase 5 decided: UptimeRobot free tier pings `/health` every 5 min; Sentry alerts (Python + JS SDK) route to `#kendrew-alerts` Slack; no PagerDuty/Opsgenie in v1 (no formal on-call rotation yet — Slack mobile push substitutes for pager). **ROADMAP.md Phase 11 success criterion 8 must be updated to reflect this reality** (remove PagerDuty/Opsgenie reference).
- **D-14:** Sentry Performance transactions enabled on hot paths only: `POST /agent/*`, `POST /jobs/launch`, `POST /webhooks/runpod`, `POST /webhooks/heartbeat`, `POST /jobs/{id}/upload-urls`. Free-tier budget covers this; no OTel / self-hosted APM.
- **D-15:** Modal-side observability uses Modal's built-in dashboard logs + the heartbeat endpoint (Phase 5 decision) for user-facing progress. No sentry-sdk inside Modal containers, no external log shipper — revisit only if a GPU incident forces it.
- **D-16:** GPU spend alert: arq cron sums Stripe meter events per day; if > $50/day (configurable via `GPU_SPEND_ALERT_THRESHOLD_USD`), Resend sends an email to Leo. Matches Phase 5 decision — this phase wires it up.

### ROADMAP Corrections Required

- Phase 11 success criterion 6 ("GPU jobs dispatch to RunPod from production backend") → replace with Modal primary, RunPod quarantined fallback (per Phase 10 subprocessor list).
- Phase 11 success criterion 8 ("uptime monitoring with PagerDuty/Opsgenie alerting") → replace with UptimeRobot + Sentry + Slack (per Phase 5 and D-13 above).

These are edits to `.planning/ROADMAP.md`, not scope changes — they bring the document into agreement with decisions already made.

### Claude's Discretion

- Exact Railway service layout (one service per role vs. monorepo-style multi-target); choose based on Railway's current Nixpacks/Docker ergonomics.
- Vercel project settings (monorepo root, build command, output dir) — follow existing `frontend/` Vite config.
- UptimeRobot monitor configuration details (interval, alert contacts).
- Specific Cloudflare DNS record TTLs.
- Whether staging uses a subdomain (`staging.kendrew.ai`, `app-staging.kendrew.ai`) or Vercel/Railway default `*.vercel.app` / `*.up.railway.app` URLs — pick the simpler option if DNS is cheap.
- Structure of `docs/deploy.md` (table, runbook style, or both).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase Dependencies
- `.planning/ROADMAP.md` §Phase 11 — deployment goals and success criteria (note: SC 6 and SC 8 to be corrected, see D-13 and ROADMAP Corrections Required)
- `.planning/REQUIREMENTS.md` — v1 requirements (all covered by prior phases; Phase 11 requirement IDs: TBD, infrastructure-only)
- `.planning/phases/05-production-hardening/05-CONTEXT.md` — Sentry, UptimeRobot, rate limiting, webhook HMAC, `/health` endpoint, spend alerts, stale-job detection
- `.planning/phases/09-testing-ci-cd/09-CONTEXT.md` — `test.yml` gates, `smoke.yml` post-deploy workflow, manual rollback policy (D-12 there)
- `.planning/phases/10-legal-and-compliance/10-CONTEXT.md` — subprocessor list (Modal primary GPU, not RunPod), data retention 90d, ToS acceptance, cookie consent

### Existing Code + Config
- `backend/config.py` — Pydantic Settings, CSRF middleware env gate, `COOKIE_SECURE`, CORS origins, `APP_BASE_URL`
- `.env.example` — current env template (needs full audit per D-11)
- `backend/Dockerfile` — backend container image used by Railway
- `infrastructure/modal/` — five Modal app definitions (`bindcraft_app.py`, `boltzgen_app.py`, `pxdesign_app.py`, `rfantibody_app.py`, `rfdiffusion_app.py`) + `base_image.py`
- `.github/workflows/deploy-modal.yml` — DRAFT Modal deploy workflow (D-07 flips this on)
- `.github/workflows/smoke.yml` — post-deploy smoke test workflow (Phase 9 D-11)
- `.github/workflows/test.yml` — Phase 9 CI gates (blocks deploys per D-05)
- `.github/workflows/docker-*.yml` — per-tool Docker image builds (5 workflows, already in use)
- `docker-compose.yml` — local dev stack (MinIO, Redis) — reference for parity when naming prod services
- `supabase/` — migration history + local Supabase config
- `backend/webhooks/router.py` — HMAC validation (webhook secret rotation D-10 plugs in here)
- `backend/worker/cleanup.py` — orphan pod cleanup cron (extend for spend alert D-16)
- `backend/worker/tasks.py` — arq task patterns (spend alert cron lives here)

### External Documentation
- Railway Docker/Nixpacks deploy docs, Railway predeploy hook docs
- Vercel custom domains + env vars per environment
- Cloudflare DNS setup for Vercel + Railway (grey-cloud for app subdomain)
- Supabase Cloud Pro — connection pooling (Supavisor transaction mode recommended for FastAPI workers)
- Upstash Redis TLS URL format (`rediss://...`)
- Cloudflare R2 bucket creation + API token with object-scope
- Modal environments + deploy flow (`modal environment create`, `modal deploy`)
- Sentry: Python SDK + React SDK + Performance transactions
- UptimeRobot: monitor setup + alert contact
- Resend: domain verification + `jobs@kendrew.ai` sender setup (SPF/DKIM/DMARC)
- Stripe live keys + metered billing idempotency (Phase 5 D-2A already uses `job_id`)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`backend/Dockerfile`** — production-ready container for Railway backend deploy.
- **`infrastructure/modal/*`** — five Modal apps already defined; the draft workflow just needs to be enabled.
- **`.github/workflows/test.yml` + `smoke.yml`** — Phase 9 left CI gates and smoke test ready to wire up.
- **`backend/webhooks/router.py`** — HMAC verification already in place; D-10 dual-secret support is an additive change.
- **`backend/worker/cleanup.py`** and **`tasks.py`** — arq cron scaffolding exists; D-16 spend alert fits the pattern.
- **`backend/config.py`** — Pydantic Settings with `COOKIE_SECURE` and `CORS_ORIGINS` already environment-gated.

### Established Patterns
- **Platform-style container deploy** — backend and worker both containerized; Railway is a drop-in for existing Dockerfile.
- **HMAC-verified webhooks** — Phase 5 locked this in; dual-secret rotation adds an env var path, not a new verification flow.
- **arq cron for background jobs** — GPU spend alert (D-16) and future retention jobs (Phase 10) share this pattern.
- **Pydantic Settings + .env.example** — single source for env var contract; D-11 extends it with prod keys.

### Integration Points
- **Railway predeploy hook** — runs `alembic upgrade head` before traffic shifts (D-06).
- **Vercel build env** — frontend Sentry DSN and Supabase anon key inlined at build time.
- **Modal deploy environments** — existing `staging` / `main` split in the draft workflow becomes the hard environment boundary.
- **Cloudflare DNS** — adds `kendrew.ai` (apex A/CNAME to Vercel), `www` redirect, `app.kendrew.ai` (CNAME to Railway, DNS-only), plus SPF/DKIM/DMARC TXT records for Resend.
- **GitHub Actions** — six existing workflows (`test.yml`, `smoke.yml`, `deploy-modal.yml` draft, 4x docker-*.yml) provide the CI spine; D-05 wires Railway/Vercel checks to depend on `test.yml`.

</code_context>

<specifics>
## Specific Ideas

- Production apex is `kendrew.ai` (matches the email sender `jobs@kendrew.ai`). Do not use `kendrew.app`, `kendrew.bio`, or any other variant — those are not owned.
- Backend production base URL: `https://app.kendrew.ai`. Replace the current Cloudflare tunnel placeholder (`bobby-functions-easier-methodology.trycloudflare.com`) in `.env.local` and in any hardcoded references during this phase.
- Staging base URLs: use short, obviously-non-prod names. Suggestion: `staging.kendrew.ai` (frontend) + `app-staging.kendrew.ai` (backend). Final call is Claude's Discretion (D-12 list) but pick names that will not be mistaken for prod.
- Modal environments stay `staging` and `main` (the draft workflow's names). Do not rename.
- Webhook secret env var naming: `RUNPOD_WEBHOOK_SECRET` + `RUNPOD_WEBHOOK_SECRET_PREV`, `MODAL_WEBHOOK_SECRET` + `MODAL_WEBHOOK_SECRET_PREV`. Backend tries current secret first, then `_PREV`, for a dual-signature grace window.
- `#kendrew-alerts` is the existing Slack channel for Sentry + smoke + spend alerts. Do not create a new channel.

</specifics>

<deferred>
## Deferred Ideas

- **PagerDuty / Opsgenie on-call rotation** — punt until there's a paying customer with an uptime SLO. Slack + email is sufficient for pre-revenue launch.
- **OpenTelemetry / self-hosted APM** — skip in v1; Sentry Performance covers hot-path needs.
- **Doppler / 1Password Secrets Automation** — revisit when more than one engineer needs prod access.
- **Modal stdout → Sentry** — defer until a GPU-side incident forces better signal.
- **Axiom / Better Stack log aggregation** — defer; Modal dashboard + Railway logs are enough for v1 triage.
- **Real-time Stripe meter-event watcher** — skip; daily cron spend alert is adequate.
- **Blocking auto-rollback on smoke failure** — Phase 9 already locked manual rollback; revisit only if smoke flakiness becomes rare enough to trust automation.
- **Monthly RunPod/Modal invoice reconciliation report** — already deferred to Phase 7 (admin dashboard).
- **Staging Stripe test-mode isolation** — Phase 11 assumes staging uses Stripe test keys while prod uses live keys; formal billing reconciliation between the two is post-launch concern.
- **Supabase connection pooling mode deep-dive** (Supavisor transaction vs session) — not explicitly discussed; Claude's Discretion during research, using transaction mode for FastAPI async pool unless research surfaces a blocker.
- **Frontend CSP / security headers tightening** — Phase 5 covered CSRF + cookie scoping; full CSP/HSTS review deferred to post-launch unless research flags an obvious gap.
- **Email DNS deep-dive (SPF/DKIM/DMARC)** — will be wired up as part of Resend domain verification, but no policy tuning beyond what Resend requires. Full DMARC-to-reject progression is post-launch.

</deferred>

---

*Phase: 11-deployment*
*Context gathered: 2026-04-24*
