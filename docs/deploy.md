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
2. In Railway -> kendrew-backend-prod -> Variables:
   - Set `WEBHOOK_HMAC_SECRET_PREV` equal to the CURRENT value of `WEBHOOK_HMAC_SECRET`.
   - Set `WEBHOOK_HMAC_SECRET` equal to the new value from step 1.
   - Save. Railway restarts the backend service automatically.
3. Update Modal secret in both environments:
   ```
   modal secret create --env main kendrew-webhook WEBHOOK_SECRET=<new value>
   modal deploy --env main infrastructure/modal/<each app>_app.py
   ```
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
   - `railway deployments --service kendrew-backend-prod --limit 2`
   - `vercel ls kendrew --limit 2`
2. Railway rollback:
   - Railway dashboard -> kendrew-backend-prod -> Deployments -> previous deploy -> "Redeploy"
   - OR CLI: `railway rollback --service kendrew-backend-prod`
3. Vercel rollback:
   - Vercel dashboard -> kendrew project -> Deployments -> previous deploy -> three dots -> "Promote to Production"
   - OR CLI: `vercel rollback https://bindwave.com`
4. Verify `/health` returns 200:
   - `curl -sI https://app.bindwave.com/health` should return 200 within 2 minutes of rollback.
5. Record incident in `docs/incidents/YYYY-MM-DD-<slug>.md` (create if it doesn't exist).

### Last Drill

| Date | Target | Wall clock to /health green | Blockers |
|------|--------|-----------------------------|----------|
| TBD | staging | pending | - |

Run the drill at least once against staging before declaring SC 9 green. The
drill does NOT require introducing an intentional break - rolling back to any
prior-known-good deploy proves the < 5-min target.

## Monitoring

| Signal | Tool | Channel |
|--------|------|---------|
| Application errors | Sentry (kendrew-backend, kendrew-frontend) | #kendrew-alerts Slack + email |
| External liveness | UptimeRobot (app.bindwave.com/health, app-staging.bindwave.com/health) | #kendrew-alerts + email |
| Daily GPU spend | arq cron `check_daily_gpu_spend` | Resend email to leo@ranomics.com |
| Post-deploy smoke | GitHub Actions smoke.yml | Sentry + #kendrew-alerts on fail |

Sentry Performance traces sample 100% of the 5 D-14 hot paths:
- `POST /agent/message`
- `POST /jobs/launch`
- `POST /webhooks/runpod`
- `POST /webhooks/heartbeat`
- `POST /jobs/{job_id}/upload-urls`

## Deployment Flow

1. PR opened targeting `main`. GitHub Actions `test.yml` runs 4 gates (backend, frontend, E2E, lint+typecheck+coverage) plus a frontend-bundle secret-leak grep (Phase 11 SC 7). Branch protection blocks merge on any failure.
2. PR touching `infrastructure/modal/**`, `docker/**`, or `backend/pipelines/**` also triggers `deploy-modal.yml` against Modal `staging` env.
3. On merge to `main`:
   - Railway auto-deploys kendrew-backend-prod + kendrew-worker-prod. `railway.toml` runs `supabase db push` as preDeployCommand; failure aborts the deploy.
   - Vercel auto-deploys the frontend to https://bindwave.com.
   - `deploy-modal.yml` deploys all 5 Modal apps to `main` env (if PR touched Modal paths).
   - `smoke.yml` runs after deploy completes (informational - D-08). Failures post to Sentry + #kendrew-alerts. Human decides rollback.

## Subprocessors (Phase 10 reference)

See `frontend/src/content/legal/subprocessors.mdx` - the authoritative list.
Summary: Supabase, Cloudflare (R2 + DNS), Modal, RunPod (quarantined), Stripe,
Anthropic, Resend, Sentry.
