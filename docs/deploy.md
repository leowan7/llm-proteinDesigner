# Kendrew Deployment Runbook

Last updated: 2026-04-24 (Phase 11).

This document is the operational source of truth for deploying, rotating
secrets, and rolling back the Kendrew production platform. Update it any time
a deploy concern surfaces that would not be obvious to the next on-call.

## Environments

| Environment | Frontend | Backend | Database | Redis | R2 Bucket | Modal Env |
|-------------|----------|---------|----------|-------|-----------|-----------|
| Production | https://bindwave.com | https://app.bindwave.com | kendrew-prod (Supabase Pro) | kendrew-prod (Upstash, TLS) | kendrew-prod | main |
| Staging | https://staging.bindwave.com | https://app-staging.bindwave.com | kendrew-staging (Supabase Pro) | kendrew-staging (Upstash, TLS) | kendrew-staging | staging |
| Local | http://localhost:5173 | http://localhost:8000 | local Supabase CLI :54322 | Docker redis :6379 | MinIO :9000 | n/a |

## Env Variable Matrix (Phase 11 D-12)

Where each env var lives in production. The source of truth is `.env.example`
with `# runtime:` comments; this table is a reading-friendly summary.

| Key | Railway Backend | Railway Worker | Vercel | Modal Secret | Notes |
|-----|:---------------:|:--------------:|:------:|:------------:|-------|
| SUPABASE_URL | Yes | Yes | Yes (as VITE_SUPABASE_URL) | - | Public |
| SUPABASE_ANON_KEY | Yes | Yes | Yes (as VITE_SUPABASE_ANON_KEY) | - | Public |
| SUPABASE_SERVICE_ROLE_KEY | Yes | Yes | **NO - never Vercel** | - | Pitfall 5 |
| SUPABASE_JWT_SECRET | Yes | - | - | - | Backend auth |
| DATABASE_URL | Yes | Yes | - | - | Supavisor transaction pooler, pooler.supabase.com:6543 |
| S3_ENDPOINT_URL | Yes | Yes | - | kendrew-r2 | R2 account endpoint |
| S3_ACCESS_KEY | Yes | Yes | - | kendrew-r2 | Per-bucket token |
| S3_SECRET_KEY | Yes | Yes | - | kendrew-r2 | Per-bucket token |
| S3_BUCKET_NAME | Yes | Yes | - | kendrew-r2 | kendrew-prod or kendrew-staging |
| REDIS_URL | Yes | Yes | - | - | rediss:// (TLS) |
| CSRF_SECRET | Yes | - | - | - | openssl rand -hex 32 |
| COOKIE_SECURE | Yes (true) | - | - | - | prod only |
| CORS_ORIGINS | Yes | - | - | - | exact-match list |
| DEBUG | Yes (false) | Yes (false) | - | - | prod MUST be false |
| SENTRY_DSN | Yes | Yes | - | - | backend project DSN |
| VITE_SENTRY_DSN_FRONTEND | - | - | Yes | - | frontend project DSN |
| ANTHROPIC_API_KEY | Yes | - | - | - | agent |
| STRIPE_SECRET_KEY | Yes | - | **NO** | - | live key in prod, test in staging |
| STRIPE_WEBHOOK_SECRET | Yes | - | - | - | |
| VITE_STRIPE_PUBLISHABLE_KEY | - | - | Yes | - | public |
| GPU_PROVIDER | Yes (modal) | Yes (modal) | - | - | |
| MODAL_TOKEN_ID | Yes | Yes | - | - | also in GitHub Actions secrets |
| MODAL_TOKEN_SECRET | Yes | Yes | - | - | also in GitHub Actions secrets |
| MODAL_ENVIRONMENT | Yes | Yes | - | - | main or staging |
| RESEND_API_KEY | Yes | Yes | - | - | worker sends completion emails + spend alerts |
| RESEND_FROM_EMAIL | Yes | Yes | - | - | `Bindwave <jobs@bindwave.com>` |
| WEBHOOK_HMAC_SECRET | Yes | - | - | - | Phase 11 D-10 - canonical name; source of truth |
| WEBHOOK_HMAC_SECRET_PREV | Yes | - | - | - | rotation window only |
| RUNPOD_WEBHOOK_SECRET | **NO (backend config.py alias only)** | - | - | - | deprecated alias - backend resolves via model_post_init; NOT set in Railway |
| APP_BASE_URL | Yes | - | - | - | `https://app.bindwave.com` |
| VITE_APP_BASE_URL | - | - | Yes | - | same value |
| GPU_DAILY_SPEND_ALERT_USD | Yes | Yes | - | - | cron threshold (Phase 11 D-16) |

## Secret Rotation Runbook (Webhook HMAC - D-10)

Rotates `WEBHOOK_HMAC_SECRET` without dropping any in-flight webhook. Expect
60-90 min total including propagation wait. Runbook applies to production;
staging follows the same steps against its own Railway service.

1. Generate new secret:
   ```
   openssl rand -hex 32
   ```
2. In Railway -> project `bindwave` -> environment `production` -> service
   `backend` -> Variables (staging is the same service under the `staging`
   environment):
   - Set `WEBHOOK_HMAC_SECRET_PREV` equal to the CURRENT value of `WEBHOOK_HMAC_SECRET`.
   - Set `WEBHOOK_HMAC_SECRET` equal to the new value from step 1.
   - Save. Railway restarts the backend service automatically.
3. Update the Modal secret in both environments:
   ```
   modal secret create --force --env main    ranomics-webhook WEBHOOK_HMAC_SECRET=<new value>
   modal secret create --force --env staging ranomics-webhook WEBHOOK_HMAC_SECRET=<new value>
   modal deploy --env main    infrastructure/modal/rfdiffusion_app.py
   modal deploy --env staging infrastructure/modal/rfdiffusion_app.py
   ```
   Three details this command is easy to get wrong, each failing silently or late:
   - The secret is named **`ranomics-webhook`**, not `kendrew-webhook` — it
     predates the Ranomics -> Kendrew rename and
     `infrastructure/modal/rfdiffusion_app.py` still looks it up under the old
     name. Renaming it means changing that line first.
   - The key inside it must be **`WEBHOOK_HMAC_SECRET`**. The container reads
     `os.environ.get("WEBHOOK_HMAC_SECRET", "")`
     (docker/rfdiffusion/run_pipeline.py). Any other key name reads empty, the
     body goes out unsigned, and the backend 401s AFTER the GPU time is spent.
   - `--force` is required. Without it `modal secret create` refuses to
     overwrite an existing secret, which during a rotation is always the case.

   Only `rfdiffusion_app.py` mounts this secret, so it is the only app that
   needs redeploying. NOTE: the other four tools call `post_webhook` too but
   reference `WEBHOOK_HMAC_SECRET` nowhere and declare no Modal secrets, so
   their completions are posted unsigned. That asymmetry is not intentional as
   far as this runbook knows; it wants resolving separately.
4. Wait 60 minutes AND observe Sentry for "Webhook signed with PREV secret" warnings.
   - Warnings expected: in-flight jobs using pre-rotation secret. Count should drop to 0 within 60 min.
   - If warnings persist past 60 min: some Modal function has not restarted with the new secret. Force redeploy that app.
5. Once warnings are zero for 10 consecutive minutes:
   - Railway -> Variables -> clear `WEBHOOK_HMAC_SECRET_PREV` (set to empty).
6. Record this rotation:
   - Add a dated entry below (Last Rotations table).

### Last Rotations

| Date | Secret | Operator | Notes |
|------|--------|----------|-------|
| 2026-04-24 | WEBHOOK_HMAC_SECRET | initial provisioning (Phase 11) | baseline |

## Rollback Drill (SC 9 - 5-minute rollback)

Target: restore `/health` green within 5 minutes of detecting a regression.

Scripted drill:
```
bash scripts/rollback_drill.sh --dry-run   # safe anytime
bash scripts/rollback_drill.sh              # interactive - will prompt before destructive ops
```

Manual steps (what the script does):
1. Identify current prod deploy SHAs:
   - `railway deployments --service backend --limit 2`
   - `vercel ls kendrew --limit 2`
2. Railway rollback:
   - Railway dashboard -> project `bindwave` -> environment `production` ->
     service `backend` -> Deployments -> previous deploy -> "Redeploy"
   - OR CLI: `railway rollback --service backend`
3. Vercel rollback:
   - Vercel dashboard -> kendrew project -> Deployments -> previous deploy -> three dots -> "Promote to Production"
   - OR CLI: `vercel rollback https://bindwave.com`
4. Verify `/health` returns 200:
   - `curl -sI https://app.bindwave.com/health` should return 200 within 2 minutes of rollback.
5. Record incident in `docs/incidents/YYYY-MM-DD-<slug>.md` (create if it doesn't exist).

### Last Drill

| Date | Target | Wall clock to /health green | Blockers |
|------|--------|-----------------------------|----------|
| 2026-05-27 | production backend (Railway) | 29s rollback + 39s roll-forward | none |

Run the drill at least once against production before declaring SC 9 green. The
drill does NOT require introducing an intentional break - rolling back to any
prior-known-good deploy proves the < 5-min target.

The 2026-05-27 drill rolled `b16f91ee` (current SUCCESS, ecfa2f9) back to
`b1909706` (REMOVED, same commit, built 3h earlier) via Railway's GraphQL API
mutation `deploymentRedeploy(id, usePreviousImageTag: true)` - the API
equivalent of the dashboard's "Redeploy" button on a prior deployment.
`/health` returned 200 throughout (zero-downtime swap). Then rolled forward
the same way (redeployed `b16f91ee`). Both halves used the same fast
image-promote path; expected behavior for a real bug rollback is the same
timing because the mechanism is identical regardless of code delta. Staging
drill remains DEFERRED until the staging Railway stack is wired.

## Monitoring

| Signal | Tool | Channel |
|--------|------|---------|
| Application errors | Sentry (kendrew-backend, kendrew-frontend) | email to leo@ranomics.com (Sentry default rule "high priority issues"; Slack not installed) |
| External liveness | UptimeRobot monitor 803167433 on app.bindwave.com/health (staging monitor DEFERRED) | email to leo@ranomics.com |
| Daily GPU spend | arq cron `check_daily_gpu_spend` | Resend email to leo@ranomics.com |
| Post-deploy smoke | GitHub Actions smoke.yml | Sentry + email on fail (Slack not installed) |
| Worker process exceptions | Sentry (kendrew-backend) via `backend/worker/main.py` `sentry_sdk.init` | email to leo@ranomics.com (added 2026-06-03 — arq worker is separate Railway service, not covered by FastAPI Sentry integration) |

Sentry Performance traces sample 100% of the 5 D-14 hot paths:
- `POST /agent/message`
- `POST /jobs/launch`
- `POST /webhooks/runpod`
- `POST /webhooks/heartbeat`
- `POST /jobs/{job_id}/upload-urls`

### Sentry coverage matrix (added 2026-06-03)

The FastAPI app initializes Sentry in `backend/main.py` with the
Starlette+FastAPI integrations, which auto-capture unhandled exceptions
from HTTP request handlers. Two extensions were added during the
post-Phase-11 sweep:

- **Worker process** (`backend/worker/main.py`): the arq worker runs as
  a separate Railway service with no HTTP surface, so the FastAPI
  integrations do not apply. `sentry_sdk.init` is called at module top
  (no integrations, traces+profiles=0) so any unhandled exception in
  `run_job` / `resume_session` / the 6 cron jobs reaches Sentry.
- **Explicit-capture sweep** for the "log + swallow + continue" pattern
  in all 4 modules (worker crons, webhooks/router, agent/router, agent/tools).
  Auto-capture only fires on unhandled exceptions, so any `except Exception:`
  block that returns gracefully to the caller was previously invisible to
  Sentry even though FastApiIntegration was active. The sweep added
  `logger.exception(...) + sentry_sdk.capture_exception(exc)` after the
  existing log line at every swallow site. Behavior unchanged; visibility
  added.

## Deployment Flow

1. PR opened targeting `main`. GitHub Actions `test.yml` runs 4 gates (backend, frontend, E2E, lint+typecheck+coverage) plus a frontend-bundle secret-leak grep (Phase 11 SC 7). Branch protection blocks merge on any failure.
2. PR touching `infrastructure/modal/**`, `docker/**`, or `backend/pipelines/**` also triggers `deploy-modal.yml` against Modal `staging` env.
3. On merge to `main`:
   - Railway auto-deploys the `backend` + `worker` services in project `bindwave`. `railway.toml` runs `supabase db push` as preDeployCommand; failure aborts the deploy.
   - Vercel auto-deploys the frontend to https://bindwave.com.
   - `deploy-modal.yml` deploys all 5 Modal apps to `main` env (if PR touched Modal paths).
   - `smoke.yml` runs after deploy completes (informational - D-08). Failures post to Sentry + #kendrew-alerts. Human decides rollback.

## Subprocessors (Phase 10 reference)

See `frontend/src/content/legal/subprocessors.mdx` - the authoritative list.
Summary: Supabase, Cloudflare (R2 + DNS), Modal, RunPod (quarantined), Stripe,
Anthropic, Resend, Sentry.
