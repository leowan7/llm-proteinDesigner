# Phase 11: Deployment - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-24
**Phase:** 11-deployment
**Areas discussed:** Domains + environments, CI/CD deploy flow, Secrets + env management, Monitoring reconciliation

---

## Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Domains + environments | DNS layout, provider, SSL, prod/staging split | ✓ |
| CI/CD deploy flow | Auto-deploy gating, migrations, Modal CI, smoke test | ✓ |
| Secrets + env management | Secret store, webhook rotation, .env audit, runtime scoping | ✓ |
| Monitoring reconciliation | PagerDuty vs UptimeRobot, APM, Modal logs, spend alerts | ✓ |

**User's choice:** "discuss everything" (free text) → all four areas.

---

## Domains + Environments

### URL layout for kendrew.ai?

| Option | Description | Selected |
|--------|-------------|----------|
| kendrew.ai + app.kendrew.ai | Apex for frontend, subdomain for backend. Matches email sender and Phase 3 research. | ✓ |
| www.kendrew.ai + api.kendrew.ai | Conventional www/api split. | |
| kendrew.ai only | Backend on Railway internal, reached via Vercel rewrites. | |

**User's choice:** kendrew.ai + app.kendrew.ai (Recommended)

### DNS provider for kendrew.ai?

| Option | Description | Selected |
|--------|-------------|----------|
| Cloudflare | Already in stack for R2. Free DDoS/WAF, easy CNAMEs. | ✓ |
| Vercel DNS | Simpler for Vercel, messier for Railway/Modal. | |
| Registrar nameservers | No CDN/WAF. | |

**User's choice:** Cloudflare (Recommended)

### Environments — prod only or prod + staging?

| Option | Description | Selected |
|--------|-------------|----------|
| Prod only in v1 | Vercel previews + Modal staging env cover validation. Cheaper. | |
| Full prod + staging split | Railway + Vercel + Modal + Supabase + Upstash all have staging counterparts. | ✓ |
| PR-preview only | Railway PR environments + Vercel previews, no long-lived staging. | |

**User's choice:** Full prod + staging split

### SSL strategy?

| Option | Description | Selected |
|--------|-------------|----------|
| Platform-managed | Vercel + Railway auto-issue LetsEncrypt. Cloudflare stays grey-cloud. | ✓ |
| Cloudflare full-strict | CF terminates TLS, origin cert between CF ↔ platform. | |

**User's choice:** Platform-managed (Recommended)

---

## CI/CD Deploy Flow

### Auto-deploy trigger from main?

| Option | Description | Selected |
|--------|-------------|----------|
| Gated by test.yml green | CI must pass before Railway/Vercel deploy. | ✓ |
| Push to main auto-deploys immediately | Parallel with CI; rollback on failure. | |
| Manual approval button | CI runs, deploy requires manual promote. | |

**User's choice:** Gated by test.yml green (Recommended)

### Alembic migrations — when do they run?

| Option | Description | Selected |
|--------|-------------|----------|
| Automatic pre-deploy hook on Railway | `alembic upgrade head` before traffic shifts. | ✓ |
| Separate GitHub Action job, run before deploy | Explicit workflow step with GH secrets. | |
| Manual — run alembic from local machine | Leo runs manually. | |

**User's choice:** Automatic pre-deploy hook on Railway (Recommended)

### Modal apps deploy workflow?

| Option | Description | Selected |
|--------|-------------|----------|
| Flip on deploy-modal.yml as drafted | PR → staging, main → prod. Matrix per app. | ✓ |
| Manual `modal deploy` only | No CI involvement. | |
| Single workflow, prod-only, push-to-main | Skip staging Modal env for v1. | |

**User's choice:** Flip on deploy-modal.yml as drafted (Recommended)

### Post-deploy smoke test — blocking or informational?

| Option | Description | Selected |
|--------|-------------|----------|
| Informational — failure alerts Sentry + Slack | Matches Phase 9 D-11/D-12. | ✓ |
| Blocking — smoke failure triggers auto-rollback | Higher automation risk. | |
| Smoke only on tagged releases | Reduces noise. | |

**User's choice:** Informational (Recommended, matches Phase 9 D-11)

---

## Secrets + Env Management

### Where do prod secrets live?

| Option | Description | Selected |
|--------|-------------|----------|
| Platform-native stores | Railway + Vercel + Modal secrets. Three places, zero vendors. | ✓ |
| Doppler as single source | One source of truth, external vendor. | |
| 1Password Secrets Automation | Same tradeoff, different vendor. | |

**User's choice:** Platform-native stores (Recommended)

### Webhook secret rotation (deferred from Phase 5)?

| Option | Description | Selected |
|--------|-------------|----------|
| Include dual-secret grace-period rotation | Backend accepts current + `_PREV` during rotation. | ✓ |
| Defer to post-launch | Single secret, manual rotation + brief downtime. | |
| Never rotate pre-launch | Explicit scope-out. | |

**User's choice:** Include dual-secret grace-period rotation (Recommended)

### .env.example audit for prod?

| Option | Description | Selected |
|--------|-------------|----------|
| Full audit + docs/deploy.md | Every prod key documented, runtime scoping table. | ✓ |
| Minimal update only | Add just new vars. | |
| Leave as-is | Rely on Railway/Vercel UI. | |

**User's choice:** Full audit + docs/deploy.md (Recommended)

### Runtime scoping of env vars?

| Option | Description | Selected |
|--------|-------------|----------|
| Scope explicitly per runtime | Backend / frontend / Modal each get only what they need. | ✓ |
| Single merged var set | Leaks secrets into Vercel build env. | |

**User's choice:** Scope explicitly per runtime (Recommended)

---

## Monitoring Reconciliation

### On-call paging — which monitoring stack wins?

| Option | Description | Selected |
|--------|-------------|----------|
| UptimeRobot + Sentry + Slack only | Reaffirm Phase 5. No formal on-call. Update ROADMAP. | ✓ |
| Add PagerDuty free tier | Phone-call escalation via Leo primary. | |
| Better Stack (bundled) | Replace UptimeRobot, adds log aggregation. | |

**User's choice:** UptimeRobot + Sentry + Slack only (Recommended, matches Phase 5)

### APM / performance tracing?

| Option | Description | Selected |
|--------|-------------|----------|
| Sentry Performance on hot paths only | `/agent/*`, `/jobs/launch`, webhooks. Free tier. | ✓ |
| Skip for v1 | Error capture only. | |
| Full OpenTelemetry + self-host | Overkill pre-launch. | |

**User's choice:** Sentry Performance on hot paths only (Recommended)

### Modal-side logging?

| Option | Description | Selected |
|--------|-------------|----------|
| Modal dashboard + heartbeat-derived progress | Built-in logs + Phase 5 heartbeat endpoint. | ✓ |
| Ship Modal stdout to Sentry in-container | Adds sentry-sdk, uses event budget. | |
| Forward to Axiom / Better Stack | Extra vendor. | |

**User's choice:** Modal dashboard + heartbeat-derived progress (Recommended)

### GPU spend alerting threshold (Phase 5 carry-over)?

| Option | Description | Selected |
|--------|-------------|----------|
| Daily cron, alert >$50/day via Resend email | arq cron sums Stripe meter events. Configurable. | ✓ |
| Real-time Stripe webhook listener | Reactive, more complex. | |
| Dashboard only, no alert | Not safe for first paying users. | |

**User's choice:** Daily cron, alert >$50/day via Resend email (Recommended, matches Phase 5)

---

## Claude's Discretion

- Exact Railway service layout (per-role vs monorepo-style).
- Vercel project settings (monorepo root, build command).
- UptimeRobot monitor interval + alert contact specifics.
- Cloudflare DNS record TTLs.
- Staging subdomain naming (`staging.kendrew.ai` vs platform defaults).
- Structure of `docs/deploy.md`.
- Supabase Supavisor pooling mode (transaction vs session) — research-gated.

## Deferred Ideas

- PagerDuty/Opsgenie on-call rotation (post-paying-customer).
- OpenTelemetry / self-hosted APM (v2).
- Doppler / 1Password Secrets Automation (multi-engineer trigger).
- Modal stdout → Sentry (GPU incident trigger).
- Axiom / Better Stack log aggregation (v2).
- Real-time Stripe meter-event watcher (post-launch).
- Blocking auto-rollback on smoke failure (needs rare-flake proof first).
- Monthly GPU invoice reconciliation (Phase 7 admin dashboard).
- Staging Stripe test-mode reconciliation (post-launch).
- Supabase pooling deep-dive (research-gated).
- Frontend CSP / security headers hardening (post-launch).
- Email DNS (SPF/DKIM/DMARC) deep-dive beyond Resend defaults (post-launch).
