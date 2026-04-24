---
phase: 11
slug: deployment
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-24
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Phase 11 is a deployment phase — validation leans heavily on smoke tests, `/health` checks, and CI job success rather than unit tests.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (backend) + vitest + Playwright (frontend, from Phase 9) + GitHub Actions (smoke + deploy workflows) |
| **Config file** | `backend/pytest.ini`, `frontend/vitest.config.*`, `frontend/playwright.config.ts`, `.github/workflows/*.yml` |
| **Quick run command** | `cd backend && pytest -x` (fast webhook + config tests) |
| **Full suite command** | `cd backend && pytest && cd ../frontend && npm run test && npm run test:e2e` then `gh workflow run smoke.yml -f url=<deploy-url>` |
| **Estimated runtime** | ~90s backend + ~120s frontend unit + ~5min E2E + ~2min smoke = ~9 min total |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && pytest -x backend/tests/webhooks backend/tests/config` (scoped to phase touch surface)
- **After every plan wave:** Full suite + smoke test against staging deploy
- **Before `/gsd-verify-work`:** All 9 ROADMAP success criteria pass their validation signal (see Per-SC Verification Map below)
- **Max feedback latency:** 180 seconds for backend unit tests; 15 minutes for a full deploy-staging-then-smoke validation loop

---

## Per-SC Verification Map

Phase 11 has no REQ-IDs (infrastructure phase) — map to the 9 ROADMAP success criteria instead, as amended by CONTEXT.md D-13 (UptimeRobot+Sentry+Slack, not PagerDuty) and Phase 10 (Modal primary, not RunPod).

| SC | Success Criterion | Validation Signal | Command / Check | Where It Lives |
|----|-------------------|-------------------|-----------------|----------------|
| 1 | Frontend on Vercel with custom domain + SSL | `curl -sI https://kendrew.ai` returns 200 with `strict-transport-security` and Vercel server header | `curl -fsSI https://kendrew.ai \| grep -i 'strict-transport'` | Manual rollout checklist + smoke.yml assertion |
| 2 | Backend + worker on Railway, Docker, auto-deploy from main | `curl https://app.kendrew.ai/health` returns 200 after a push to main | `gh run list --workflow test.yml --limit 1 --json conclusion -q '.[0].conclusion'` returns `success`, then `curl -fs https://app.kendrew.ai/health` | CI `test.yml` + Railway deploy status + smoke.yml |
| 3 | Supabase Cloud Pro with pooling + backups | `/health` returns `db: {status: ok, pool: connected}`; backup retention visible in Supabase dashboard | `curl -fs https://app.kendrew.ai/health \| jq .db.status` equals `ok` | `/health` endpoint + manual backup-config verification |
| 4 | Upstash Redis with TLS | `/health` returns `redis: {status: ok, tls: true}`; `REDIS_URL` uses `rediss://` | `curl -fs https://app.kendrew.ai/health \| jq .redis` | `/health` endpoint |
| 5 | Cloudflare R2 with presigned URL access | `/health` returns `r2: {status: ok}`; a scripted test uploads + downloads a 1KB object via presigned URL | `backend/tests/test_r2_smoke.py::test_r2_roundtrip` (Wave 0 test) | `/health` + `tests/test_r2_smoke.py` |
| 6 | GPU jobs dispatch to **Modal** from production backend (per Phase 10 — not RunPod) | Manual: launch a minimal BindCraft design on production staging, observe Modal run complete + heartbeat logged | `scripts/validate_prod_gpu.sh` (new, lives in `scripts/`) | Manual smoke test during rollout |
| 7 | Secrets managed via platform-native stores | No `.env` file in prod containers; `grep -r "^[A-Z_]*=.*" ` fails on deployed Dockerfile; Railway/Vercel/Modal secret lists match the matrix in `docs/deploy.md` | `docs/deploy.md` audit + `railway variables --service backend-prod list` + `vercel env ls production` | Manual rollout checklist + `docs/deploy.md` |
| 8 | Monitoring: Sentry + UptimeRobot + Slack (per D-13 — not PagerDuty/Opsgenie) | Sentry receives a synthetic error from `/debug/sentry-test`; UptimeRobot monitor reports "up"; `#kendrew-alerts` receives a test post | `curl -fs https://app.kendrew.ai/debug/sentry-test` (dev-only route added this phase) + UptimeRobot API check | Smoke.yml + manual on-call verification |
| 9 | Rollback possible within 5 minutes | Drill: Leo runs `railway rollback` and `vercel rollback` against staging, records wall-clock from command to `/health` green | Manual rollback drill, documented in `docs/deploy.md` | Manual rollout checklist |

---

## Per-Task Verification Map (filled by planner per plan)

The planner populates this table as plans are created. Rows follow the shape:

| Task ID | Plan | Wave | SC Ref | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|--------|------------|-----------------|-----------|-------------------|-------------|--------|
| 11-01-01 | 01 | 0 | — | — | Wave 0: test fixtures for webhook dual-secret + R2 roundtrip | unit | `pytest backend/tests/webhooks backend/tests/test_r2_smoke.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/webhooks/test_dual_secret.py` — current secret path, `_PREV` fallback path, both-invalid rejection path
- [ ] `backend/tests/test_r2_smoke.py` — R2 client happy path against `protein-designer-test` bucket (or minio in CI)
- [ ] `backend/tests/test_health_endpoint.py` — `/health` returns structured JSON with `db/redis/r2` sub-statuses
- [ ] `backend/tests/test_gpu_spend_cron.py` — daily spend cron: <$50 threshold sends no email, >$50 sends one email via stubbed Resend client
- [ ] `scripts/validate_prod_gpu.sh` — wrapper around a minimal BindCraft design job, returns 0 on complete + non-zero on failure
- [ ] `scripts/rollback_drill.sh` — documents rollback steps; no-op if `--dry-run`

---

## Manual-Only Verifications

| Behavior | SC | Why Manual | Test Instructions |
|----------|----|------------|-------------------|
| DNS records resolve correctly at Cloudflare apex (orange) + `app.*` grey-cloud | 1, 2 | DNS propagation is wall-clock dependent; cannot run in CI reliably | `dig kendrew.ai`, `dig app.kendrew.ai`, confirm CNAME targets and cloud flag expectations; verify LetsEncrypt cert issues for `app.kendrew.ai` (grey-cloud required) |
| Resend domain verification (SPF/DKIM/DMARC) | 7, 8 | TXT record propagation + Resend API verify button | Follow `docs/deploy.md` Resend section; confirm Resend dashboard shows "Verified" for `kendrew.ai` |
| Modal `staging` + `main` environment isolation | 6 | Cannot verify cross-env secret isolation without deploying both | Deploy to Modal staging with a canary secret, confirm Modal prod apps cannot see it |
| GPU spend alert email received | 8 | Requires simulated >$50 day in Stripe meter events + Resend delivery | Manually insert a $55 `gpu_cost_usd` total for a test user, trigger cron, verify email lands |
| Rollback drill completes in <5 min | 9 | Real Railway/Vercel rollback UI interaction | Document wall-clock: staging deploy → break it → rollback → `/health` green |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 180s for unit tests
- [ ] Manual-only items have documented runbook steps in `docs/deploy.md`
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
