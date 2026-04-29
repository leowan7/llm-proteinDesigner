---
phase: 05-production-hardening
type: context
created: 2026-04-06
---

# Phase 5: Production Hardening — Context

## Domain Boundary

Make the platform safe for external users. Security, idempotency, billing integrity, monitoring, and observability. No new features — harden what's built.

## Decisions

### 1. Rate Limiting

**Decision:** Implement per-IP and per-user rate limits using slowapi + Redis (already running).

| Endpoint | Limit | Scope |
|---|---|---|
| `POST /auth/login` | 5/min | Per IP |
| `POST /auth/signup` | 3/min, 10/hour | Per IP |
| `POST /auth/reset-password` | 3/min | Per IP |
| `POST /agent/*` | 20/min | Per user |
| `POST /jobs/launch` | 5/min | Per user |
| `GET /jobs/*/download` | 10/min | Per user |
| `POST /webhooks/runpod` | No rate limit | HMAC signature only |
| `GET /billing/estimate` | No limit | Public, read-only |
| Everything else | 60/min | Per IP (global fallback) |

**Additional mitigations (not rate limiting):**
- Max 3 concurrent SSE connections per user (server-side counter)
- CSRF token validation on all state-changing POST endpoints (frontend already sends X-CSRFToken)
- Webhook replay protection: reject payloads with timestamps older than 5 minutes

### 2. Billing Reconciliation

**Decision A: Double-billing prevention.**
Use `job_id` as the Stripe meter event idempotency key. Stripe ignores duplicate events with the same key within 24 hours. One-line change in `stripe_client.py`.

**Decision B: Cost calculation source.**
Use RunPod-reported runtime from webhook payload if available, fall back to our timestamps (started_at to now). Accept small discrepancies. Monthly reconciliation report deferred to Phase 7 (admin dashboard).

**Decision C: Queue time billing.**
Keep as-is — billing includes GPU provisioning time. The 2-3 minute cold start on a 10-60+ minute job is acceptable overhead. Document in pricing: "Billing includes GPU provisioning time."

### 3. Monitoring and Alerting

**Layer 1: Error tracking.**
Sentry (Python SDK + JS SDK) AND structured JSON logging to stdout. Both — Sentry for real-time alerts, structured logs for post-incident debugging.

**Layer 2: Uptime monitoring.**
New `GET /health` endpoint that checks DB + Redis connectivity. UptimeRobot (free tier) pings it every 5 minutes, alerts on downtime via email.

**Layer 3: Business alerting.**
- Orphan pod cleanup: already runs every 10 min via arq cron
- Daily GPU spend check: arq cron, alerts via Resend email if daily spend > $50 (configurable)
- Sentry with Slack integration (#kendrew-alerts channel) for real-time error notifications

**Layer 4: Container heartbeat + progress.**
Container sends heartbeat every 60 seconds with current stage and progress count:
```json
{"job_id": "...", "stage": "Running RFdiffusion", "designs_completed": 45, "designs_total": 100}
```
- Backend updates job stage column, publishes SSE → user sees live progress ("Running RFdiffusion — 45/100 designs")
- If no heartbeat for 5 minutes → mark job as stale
- If heartbeat arrives but progress unchanged for 10 minutes → alert via Sentry
- Requires new `POST /webhooks/heartbeat` endpoint and changes to `run_pipeline.py`

**Stale job detection:**
If job has been running with no heartbeat for > 10 minutes, mark as `failed` with `error_category = "Job timed out — no response from GPU"`. Terminate pod. Publish SSE event. Send failure email.

### 4. Presigned URL Security

**Decision A: User isolation.**
Already safe — presigned URLs are scoped to specific object keys, backend generates URLs only for jobs owned by the authenticated user. No change needed.

**Decision B: Upload URL expiry.**
Do NOT generate presigned upload URLs at dispatch time. Container requests fresh URLs on-demand when ready to upload via `POST /jobs/{job_id}/upload-urls`. Backend generates 1-hour expiry URLs on demand. This handles jobs of any duration (minutes to days).

New endpoint: `POST /jobs/{job_id}/upload-urls`
- Accepts: `{"filenames": ["design_001.pdb", "report.csv"]}`
- Returns: `{"urls": {"design_001.pdb": "https://...", "report.csv": "https://..."}}`
- Auth: Job-specific token passed to container as env var (not user JWT)
- Validates job exists and is in running state

Changes:
- `worker/tasks.py`: No longer generates presigned PUT URLs at dispatch
- `run_pipeline.py`: Calls upload-urls endpoint when ready to upload
- New lightweight auth mechanism for container → backend communication (job token)

**Decision C: Download URL sharing.**
Accept it. Download URLs have 1-hour expiry, generated on-demand. Scientists sharing PDB files with colleagues is expected. Legal protection handled in Phase 10 (ToS).

**Data retention:** Results stored in R2 for 30 days. ExpiryWarningBanner already exists in frontend. After 30 days, files deleted from R2, metadata stays in DB.

## Specifics

- Rate limiter: slowapi library (wraps limits), backed by Redis
- Error tracking: Sentry free tier (5K events/mo)
- Uptime: UptimeRobot free tier
- Alerts: Sentry → Slack (#kendrew-alerts), GPU spend → Resend email
- No APM/tracing for v1 — overkill for <100 users
- No custom dashboards — use Supabase dashboard + Stripe dashboard

## Canonical Refs

- `backend/auth/dependencies.py` — JWT validation patterns
- `backend/webhooks/router.py` — HMAC validation, pod termination
- `backend/jobs/router.py` — Payment gate, job ownership checks
- `backend/worker/tasks.py` — Idempotency guard, status updates
- `backend/worker/cleanup.py` — Orphan pod cleanup cron
- `backend/billing/stripe_client.py` — Meter event recording
- `backend/config.py` — All secrets and environment config
- `.planning/REQUIREMENTS.md` — v1 requirements (all complete)
- `.planning/phases/03-job-execution-frontend-and-billing/03-CONTEXT.md` — Billing model decisions

## Deferred Ideas

- Webhook secret rotation with grace period → Phase 11 (deployment) or post-launch
- Monthly RunPod invoice reconciliation report → Phase 7 (admin dashboard)
- IP-binding for JWTs / device fingerprinting → v2
- Stripe webhook listener for payment method changes → post-launch if needed
