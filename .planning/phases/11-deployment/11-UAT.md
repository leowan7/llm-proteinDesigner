---
status: verified
phase: 11-deployment
source:
  - 11-01-SUMMARY.md
  - 11-02-SUMMARY.md
  - 11-03-SUMMARY.md
  - 11-04-SUMMARY.md
  - 11-05-SUMMARY.md
started: 2026-06-03
updated: 2026-06-04
verifier: leo@ranomics.com (via Claude-driven E2E)
---

## Summary

total: 9 success criteria
passed: 9
issues: 0
pending: 0
skipped: 0

All nine ROADMAP success criteria for Phase 11 are satisfied. The final
satisfaction signal was an end-to-end RFdiffusion run launched from
production through the bindwave.com UI on 2026-06-03 (job_id
`6973ea6c-f448-4ea6-bca8-5f2ce31ce4fc`), reaching `status: complete` in
the DB with a signed webhook delivery and a $0.07 actual GPU spend.

The verification surfaced + closed 5 real prod bugs en route — all
fixed before the close-out run. Commit log at the bottom.

## Success criteria verification

### SC 1 — Frontend deployed on Vercel with custom domain + SSL
**State:** ✅ PASS
**Evidence:**
- `https://bindwave.com` returns 200 (verified throughout the 2026-06-03 session)
- Vercel `kendrew` project at `vercel.com/leowan7s-projects/kendrew`
- 3 domains bound: apex (production), `www` (308 → apex), `kendrew.vercel.app`
- LetsEncrypt SSL active (Vercel-managed); Cloudflare DNS-only (Pitfall 3 compliant)
- Frontend bundle hash bumped each push (last observed `index-B5u1kDmO.js`)

### SC 2 — Backend + worker deployed on Railway as Docker containers, auto-deploy from master
**State:** ✅ PASS
**Evidence:**
- `https://app.bindwave.com/health` returns `{"api":"ok","db":"ok","redis":"ok"}` in ~1.2s
- Railway project `bindwave` (ID `0ce1adb2-671d-4a60-892c-91280301aae0`)
- 2 services × 2 envs: `backend`, `worker` × `production`, `staging` (staging deploys deferred)
- Auto-deploy from master observed live in 2026-06-03 session: every push triggered Railway redeploy in ~2 min
- arq worker process running 8 functions (run_job, resume_session, 6 crons)
- Per-service `railway.toml` + `railway.worker.toml` so worker doesn't inherit backend's uvicorn startCommand

### SC 3 — Supabase Cloud, transaction pooler, daily backup
**State:** ✅ PASS (with documented deferral)
**Evidence:**
- `kendrew-prod` Supabase project on Pro tier, EU-east-1
- `DATABASE_URL` uses Supavisor pooler at `aws-1-us-east-1.pooler.supabase.com:6543`
- `MIGRATION_DB_URL` uses direct connection at port 5432 for `supabase db push` preDeployCommand
- Daily backup with 7-day retention enabled
- PITR explicitly declined for v1 (cost vs. pre-revenue risk; revisit on first paying customer with RPO contract)

### SC 4 — Redis on Upstash with TLS
**State:** ✅ PASS
**Evidence:**
- `REDIS_URL` is `rediss://` (TLS double-s) on both backend and worker services in prod
- `/health` endpoint confirms Redis connectivity
- Cost-cap configured: prod $50/mo, staging $20/mo

### SC 5 — Object storage on Cloudflare R2 with presigned URL access
**State:** ✅ PASS
**Evidence:**
- Buckets `kendrew-prod` + `kendrew-staging` exist
- Per-bucket Account API tokens (T-11-02-02 isolation enforced)
- Live end-to-end proof on 2026-06-03: `da17ffe4` + `6973ea6c` job_specs both carry S3 keys
  (`users/{user_id}/jobs/{job_id}/inputs/target.pdb`); Modal container downloaded the
  78,570-byte 1UBQ PDB from a presigned URL successfully
- Outputs uploaded to R2 too: `design_0.pdb` (45,292 bytes) + `metrics.csv` (169 bytes)

### SC 6 — GPU jobs dispatch to Modal from production backend
**State:** ✅ PASS (close-out 2026-06-04 01:06:42Z)
**Evidence:** End-to-end pipeline trace for job `6973ea6c-f448-4ea6-bca8-5f2ce31ce4fc`:
```
Backend /jobs/launch payment gate            ← has_payment_method: true (self-heal 1fb7558)
  arq worker run_job                          ← 0.68s dispatch
  Worker → Modal provider.submit_job          ← 2.19s, accepted
  Modal container started on A100-SXM4-40GB
  PDB downloaded from S3 (78570 bytes)        ← real PDB format (fix 0146812)
  Normalize: kept 76/76 residues, no gaps     ← preflight matched container reality
  RFdiffusion backbone                        ← 75.2s
  ProteinMPNN sequence                        ← 3.6s
  AF2 multimer validation                     ← 46.2s (3 recycles)
  Upload to R2: design_0.pdb + metrics.csv
  post_webhook HMAC-signed                    ← Webhook response: 200 (fix 0146812)
  DB transition draft → queued → running → complete
  status: complete, gpu_seconds: 154, gpu_cost_usd: 0.0706, candidate_count: 1
```
RunPod stays quarantined as emergency fallback per Phase 10 decision; `GPU_PROVIDER=modal` on both services.

### SC 7 — Secrets managed via platform-native secret stores (not .env files)
**State:** ✅ PASS
**Evidence:**
- All 24+ custom env vars set per (service, environment) combo via Railway dashboard / CLI
- Modal secrets (`kendrew-r2`, `ranomics-webhook`) created via `modal secret create`
- Frontend-side VITE_ vars only on Vercel Production + Preview scopes
- `SUPABASE_SERVICE_ROLE_KEY` deliberately ABSENT from Vercel (Pitfall 5)
- CI grep in `test.yml` enforces no service-role-key leakage into frontend bundle
- `WEBHOOK_HMAC_SECRET` matched between Railway env + Modal `ranomics-webhook` Secret (proven by signed webhook 200 OK)

### SC 8 — Sentry for errors + UptimeRobot for uptime
**State:** ✅ PASS (with documented Slack→email adaptation)
**Evidence:**
- **Sentry**: 2 projects (`kendrew-backend`, `kendrew-frontend`) under `ranomics` org
  - Backend DSN set on Railway: backend + worker (prod env)
  - Frontend DSN set on Vercel: Production + Preview scopes
  - Hot-path Performance sampling per D-14 (5 routes at 100%)
  - Worker process Sentry init added 2026-06-03 (`7fcdf3b`) so arq + cron failures
    are no longer invisible
  - Explicit `sentry_sdk.capture_exception` added to 11 swallow-and-continue sites
    in worker + webhooks + agent
  - End-to-end synthetic test landed `KENDREW-FRONTEND-1` → email delivered 2026-05-27
- **UptimeRobot**: Monitor `803167433` on `https://app.bindwave.com/health` @ 5 min interval, email leo@ranomics.com
- **Routing**: All alerts route to leo@ranomics.com via email. Slack `#kendrew-alerts`
  was originally planned but never installed; PROVISIONING.md + docs/deploy.md amended
  to reflect email-only routing.

### SC 9 — Rollback possible within 5 minutes via Railway/Vercel deploy history
**State:** ✅ PASS (drill 2026-05-27)
**Evidence:** Per `docs/deploy.md` "Last Drill" row:
- Date: 2026-05-27
- Target: production backend (Railway)
- Wall-clock: **29s rollback + 39s roll-forward** (well under 5-min target)
- Blockers: none

## Bugs surfaced + fixed during verification

The SC 6 close-out attempt exposed five real production bugs that had never been hit
in earlier sessions because the launch path had never been driven end-to-end. All
fixed before the close-out completion:

1. **PDB CIF served as .pdb** — `fetch_pdb_file` downloaded mmCIF but saved with .pdb
   extension; container's PDBParser crashed on mmCIF tokens (`'U'` from `_entry.id`).
   Fix: `0146812` (URL .cif → .pdb).
2. **Container webhook unsigned** — `post_webhook` sent no auth header; backend's
   HMAC validation returned 401 unconditionally. Fix: `0146812` (HMAC sign + Modal
   Secret `ranomics-webhook` attached to function).
3. **Stripe `default_payment_method` not set** — Checkout setup mode attached the
   card but didn't promote to default; `/billing/payment-method` returned false.
   Fix: `1fb7558` (self-heal at read time; canonical webhook handler deferred).
4. **PDB file lifecycle across containers** — agent's `resolve_structure` wrote
   `/tmp/structures/<id>.pdb` in the BACKEND container; worker (separate Railway
   service, separate /tmp) raised FileNotFoundError on every launch. Fix: `f5648c7`
   (hoist `ensure_pdb_in_s3` into `validate_preflight`).
5. **Email links pointed at backend API host** — completion + failure emails
   linked to `app.bindwave.com/jobs/<id>` (JSON, 401 once expired) instead of
   `bindwave.com/jobs/<id>` (frontend SPA). Fix: `5fcc4cc` (split into
   `frontend_base_url` setting + Railway env var).

Plus preflight hardening: `99819eb` added structural checks (chain gap detection
+ hotspot-presence) that would have flagged the 1ALU disorder gap (52–60) BEFORE
the user clicked Launch, instead of after burning $0.83 of stale-window billing.
And email-copy polish: `69dc79f` (real tool name + grammar + smart runtime
display) closed the post-success email-quality observation.

## Commits this session (Phase 11 close-out, all on origin/master)

| SHA | Subject |
|---|---|
| `7fcdf3b` | fix(worker): initialize Sentry in arq process + capture-to-sentry the swallow-and-continue handlers |
| `c39cdb2` | fix(agent): hoist logger assignment below imports to clear ruff E402 |
| `6a2d4cf` | fix(webhooks): capture-to-sentry the terminate_pod swallow |
| `f1f0ad3` | test(legal): tighten cookie-consent assertion to cover csrftoken_v2 |
| `de074d3` | fix(agent): honor user-named parameters and re-call collect_parameters on edits |
| `28f7bf0` | fix(agent): capture-to-sentry the four silent swallow sites + docs deploy.md sweep summary |
| `1fb7558` | fix(billing): self-heal default_payment_method when Checkout attached PM but no default set |
| `f5648c7` | fix(agent): hoist PDB to S3 at draft-job creation so worker can actually read it |
| `0146812` | fix(11): unblock SC 6 close-out — RCSB fetch returns PDB not CIF + container HMAC-signs webhooks |
| `5fcc4cc` | fix(11): email links target frontend (bindwave.com) not backend (app.bindwave.com) |
| `3d67a0e` | fix(frontend): getJobList unwraps {jobs, has_more} envelope instead of lying with a TS cast |
| `99819eb` | feat(11): preflight catches chain gaps + missing hotspots before user clicks Launch |
| `69dc79f` | fix(11): completion email shows real tool name + proper grammar + smart runtime |
| `be20b8b` | chore(11): commit deliberate-leaves from prior sessions |

Plus `ff3361e` from a parallel spawned-task session: `fix(auth): repair Supabase Auth
pointing at localhost in production`.

External-side changes (not in git):
- Modal Secret `ranomics-webhook` created with `WEBHOOK_HMAC_SECRET` value
- Modal app `ranomics-rfdiffusion-prod` redeployed with new container code + Secret
- Railway `FRONTEND_BASE_URL=https://bindwave.com` set on backend + worker prod
- PROVISIONING.md (gitignored) Sentry section + DSN housekeeping appended

## Documented gaps for Phase 12+ follow-up

These are known, intentional carry-overs — none block Phase 11 sign-off:

1. **Webhook secret handler (`setup_intent.succeeded`)**: Canonical Stripe webhook
   for setting `default_payment_method` is deferred; current self-heal at read time
   covers the same case but is slightly less elegant. Add when `STRIPE_WEBHOOK_SECRET`
   is wired.
2. **Other 4 docker containers (bindcraft, boltzgen, pxdesign, rfantibody)**: Same
   `post_webhook` HMAC pattern as RFdiffusion needs to be applied (rfdiffusion only
   was fixed for SC 6 close-out). Mechanical sweep when those tools are exercised
   in prod.
3. **Per-tool preflight registry**: Current chain_continuity check treats only
   RFdiffusion as gap-strict; the "right" version is a `PreflightChecker` registry
   per tool. Tight version shipped.
4. **Normalize-dry-run preflight**: Run the same `normalize_for_rfdiffusion` the
   container will, report dropped residues. Nice-to-have on top of gap detection.
5. **Stale-job billing**: 8cd6a171 incident showed the orphan-cleanup cron caps
   billing at `STALE_HEARTBEAT_SECONDS` (30 min) even for jobs that crashed in
   ~1 second of compute. Real fix is to gate billing on heartbeat actually firing.
6. **Staging Railway stack**: `backend-staging` + `worker-staging` services exist
   but aren't connected to a git branch; staging Modal env exists but staging UI
   isn't wired. Deferred until a real staging-vs-prod workflow need surfaces.

## Sign-off

Phase 11 (Deployment) is complete and verified. ROADMAP.md should be updated to
mark Phase 11 done; next milestone is Phase 12 (Teams & Organizations).
