# Phase 11 Gate 2 — Paste-Ready Manifest

Source-of-truth for click-through work on Railway, Vercel, and GitHub. References to live secret values are by line number in `11-02-PROVISIONING.md` (gitignored). DO NOT echo raw values into chat, logs, or PRs.

Generated 2026-05-25. Owner: leo (single dev).

> **Session marker 2026-05-26 (continued)** — Block D §3.2/§3.3 PARTIALLY DONE: master branch protection ACTIVE but with `required_status_checks: null` (no contexts) because PR #1 (the "trigger PR" that was supposed to register the 4 check-context names) was closed without merging — pre-existing CI debt (~30 frontend TS errors + 2 backend auth-test flakes) made gating on the 4 check contexts unworkable. Add the 4 status-check requirements (PUT body in §3.3) back later, once a dedicated CI-cleanup session has all four contexts going green. Block B Railway: backend prod + worker prod DONE; staging services + worker startCommand override remain. Block C Vercel: wholly pending. See `~/vault/Claude Vault/projects/kendrew/phase11-handoff.md` → "2026-05-26 (autonomous drive — continued)".

---

## 0. Open decisions surfaced (resolve before clicking)

| # | Decision | Recommendation | Where it lands |
|---|----------|----------------|----------------|
| D1 | **Sentry frontend env name**: code reads `VITE_SENTRY_DSN`, plan/docs say `VITE_SENTRY_DSN_FRONTEND` | Set `VITE_SENTRY_DSN_FRONTEND` in Vercel AND fix `frontend/src/lib/sentry.ts:8` to read `VITE_SENTRY_DSN_FRONTEND` (one-line code change). Single source of truth. | Vercel env + 1-line code fix |
| D2 | **`SUPABASE_SERVICE_KEY` (no `_ROLE_`) value for GitHub Actions** | Set the GH secret to **kendrew-staging service_role** (PROVISIONING.md L33). CI runs against a local `supabase start` instance, so the value just needs to be present and JWT-shaped to pass secret resolution. | `gh secret set SUPABASE_SERVICE_KEY` |
| D3 | **Stripe keys**: not yet provisioned (Block E gate) | Defer. Leave `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` **empty** in Railway. Backend will boot; `/checkout` and `/webhooks/stripe` will 500 until Block E completes. Block E is a separate Gate 2 step. | Skip in this pass |
| D4 | **Anthropic API key**: not yet in PROVISIONING.md | Check if backend has `Optional[ANTHROPIC_API_KEY]` in `backend/config.py`. If yes, leave empty for now. If hard-required at boot, pull existing key from local dev `.env` and add to PROVISIONING.md as a Gate-1 retroactive entry. | Verify, then either skip or capture |
| D5 | **Default branch is `master`, not `main`** | Workflow triggers + remote default confirm `master`. Protect `master` in step 4. | GitHub branch protection |

---

## 1. Railway — 4 services (Block B)

**Order:** `backend-prod` → `backend-staging` → `worker-prod` → `worker-staging`.

Backends first so Railway assigns `*.up.railway.app` hostnames, then add `app.bindwave.com` / `app-staging.bindwave.com` Cloudflare CNAMEs (proxied=false, Pitfall 3) while LetsEncrypt issues certs (~5 min).

Common settings per service:
- Builder: Dockerfile at `backend/Dockerfile`
- Root dir: repo root (do NOT set per-service)
- Railway env: production for both prod services, staging for both staging services (use Railway environments feature)
- `railway.toml` at repo root provides backend-prod defaults; override per-service for the other 3

### 1.1 `backend-prod`

| Group | Env var | Source |
|-------|---------|--------|
| Supabase | `SUPABASE_URL` | PROVISIONING.md L17 |
|  | `SUPABASE_ANON_KEY` | L18 |
|  | `SUPABASE_SERVICE_ROLE_KEY` | L19 |
|  | `SUPABASE_JWT_SECRET` | L20 |
|  | `DATABASE_URL` | L23 (dedicated pooler; fall back to L25 if Railway IPv6 fails) |
| Upstash | `REDIS_URL` | L73 (must be `rediss://`) |
| R2 | `S3_ENDPOINT_URL` | L85 |
|  | `S3_ACCESS_KEY` | L90 |
|  | `S3_SECRET_KEY` | L91 |
|  | `S3_BUCKET_NAME` | literal `kendrew-prod` |
| Modal | `MODAL_TOKEN_ID` | L107 |
|  | `MODAL_TOKEN_SECRET` | L108 |
|  | `MODAL_ENVIRONMENT` | literal `main` |
|  | `MODAL_WORKSPACE` | literal `leowan7` |
|  | `GPU_PROVIDER` | literal `modal` |
| Resend | `RESEND_API_KEY` | L169 |
|  | `RESEND_FROM_EMAIL` | literal `Kendrew.AI <jobs@bindwave.com>` |
| Webhook HMAC | `WEBHOOK_HMAC_SECRET` | Generate locally: `openssl rand -hex 32`. **Store back in PROVISIONING.md** under a new "Webhook HMAC" section so worker-prod can use the same value. |
|  | `WEBHOOK_HMAC_SECRET_PREV` | leave empty (PLAN L266) |
| Stripe (D3 deferred) | `STRIPE_SECRET_KEY` | EMPTY this pass |
|  | `STRIPE_WEBHOOK_SECRET` | EMPTY this pass |
| Anthropic (D4 pending) | `ANTHROPIC_API_KEY` | per D4 decision |
| App config | `APP_BASE_URL` | literal `https://app.bindwave.com` |
|  | `CORS_ORIGINS` | literal `["https://bindwave.com"]` (JSON array string, brackets literal) |
|  | `CSRF_SECRET` | Generate: `openssl rand -hex 32`. Independent from WEBHOOK_HMAC_SECRET. Store in PROVISIONING.md. |
|  | `COOKIE_SECURE` | literal `true` |
|  | `DEBUG` | literal `false` |
|  | `TESTING` | literal `false` |
|  | `SENTRY_DSN` | EMPTY (Plan 11-05 fills) |

**NEVER set `RUNPOD_WEBHOOK_SECRET` on Railway.** Deprecated alias resolved by `backend/config.py` `model_post_init`; setting it causes silent rotation drift.

### 1.2 `backend-staging`

Same shape as backend-prod with these substitutions:

| Var | Source change |
|-----|---------------|
| `SUPABASE_URL` | PROVISIONING.md L31 |
| `SUPABASE_ANON_KEY` | L32 |
| `SUPABASE_SERVICE_ROLE_KEY` | L33 |
| `SUPABASE_JWT_SECRET` | L34 |
| `DATABASE_URL` | L37 (fallback L39) |
| `REDIS_URL` | L78 |
| `S3_ACCESS_KEY` | L97 |
| `S3_SECRET_KEY` | L98 |
| `S3_BUCKET_NAME` | literal `kendrew-staging` |
| `MODAL_ENVIRONMENT` | literal `staging` |
| `WEBHOOK_HMAC_SECRET` | **Independent** `openssl rand -hex 32` — do NOT reuse prod value |
| `CSRF_SECRET` | Independent `openssl rand -hex 32` |
| `APP_BASE_URL` | literal `https://app-staging.bindwave.com` |
| `CORS_ORIGINS` | literal `["https://staging.bindwave.com"]` |
| Stripe | TEST-mode keys when D3 unblocks |

All other vars identical to backend-prod.

### 1.3 `worker-prod`

**Copy ALL backend-prod variables** (Railway → service Variables → "Reference variables from another service" or paste).

Per-service overrides:
- Start command: `arq worker.main.WorkerSettings`
- No `preDeployCommand`
- No healthcheck
- `numReplicas = 1` (override `railway.toml`'s 2)

Operationally unused but kept for parity: `CORS_ORIGINS`, `CSRF_SECRET`. KEEP `APP_BASE_URL` (worker uses for callback URL construction and emailed links).

### 1.4 `worker-staging`

Mirror `backend-staging` in full. Same per-service overrides as 1.3.

### 1.5 Gotchas (carry from PLAN.md Block B + threat model)

- **Pitfall 3:** `app.bindwave.com` + `app-staging.bindwave.com` CF CNAMEs MUST be **proxied=false (grey)**. Orange-cloud breaks LetsEncrypt issuance. Cert stuck on "Validating" = grey-cloud first.
- **`numReplicas`:** prod backend = 2 (from `railway.toml`). Workers + staging = 1 (override).
- **`DATABASE_URL` port 6543** = dedicated Supavisor transaction pooler. `statement_cache_size=0` is set in `backend/db.py`. Required for Supavisor regardless of dedicated/shared.
- **`MODAL_ENVIRONMENT` mismatch is silent.** Prod must say `main`; staging must say `staging`. Wrong value = staging deploys clobber prod Modal apps.
- **`DEBUG=false` in prod** (T-11-03-08). `/debug/sentry-test` returns 404 only when false.
- **CAA already correct** (PROVISIONING.md L143–144): letsencrypt.org for Railway, pki.goog for Vercel.

### 1.6 Second-pass CF DNS adds (after services exist)

Run via `bindwave-dns-edit` API token (PROVISIONING.md L118):

```
CNAME app.bindwave.com → <railway-backend-prod>.up.railway.app   proxied=false
CNAME app-staging.bindwave.com → <railway-backend-staging>.up.railway.app   proxied=false
```

---

## 2. Vercel — `kendrew` project (Block C)

### 2.1 Project settings

| Setting | Value |
|---------|-------|
| Project name | `kendrew` |
| Repo | `leowan7/llm-proteinDesigner` |
| Production branch | **`master`** (NOT `main` — verify; PLAN.md text says main but remote default is master) |
| Root directory | `frontend` |
| Framework preset | **Vite** (confirmed: `frontend/vite.config.ts`, Vite 8 in package.json) |
| Build command | `npm run build` (resolves to `tsc -b && vite build`) |
| Output directory | `dist` |
| Install command | `npm install` (default) |
| Node version | 20.x (default; no `engines` pin in package.json) |
| Vercel Deployment Checks | **OFF** (Pitfall 8 — race condition with GitHub branch protection) |

### 2.2 Environment variables — Production scope

| Env var | Source |
|---------|--------|
| `VITE_SUPABASE_URL` | PROVISIONING.md L17 |
| `VITE_SUPABASE_ANON_KEY` | PROVISIONING.md L18 (anon JWT — NEVER service_role) |
| `VITE_STRIPE_PUBLISHABLE_KEY` | Stripe dashboard → Developers → API keys → live mode `pk_live_...` (NOT in PROVISIONING.md; D3 deferred — set when Block E completes) |
| `VITE_SENTRY_DSN_FRONTEND` | EMPTY (Plan 11-05 fills). **Per D1, also fix `frontend/src/lib/sentry.ts:8` to read this name.** |
| `VITE_APP_BASE_URL` | literal `https://app.bindwave.com` — **deferred-set** until Railway backend-prod custom domain is live |

### 2.3 Environment variables — Preview scope

Identical keys, different values:

| Env var | Source change |
|---------|--------------|
| `VITE_SUPABASE_URL` | PROVISIONING.md L31 |
| `VITE_SUPABASE_ANON_KEY` | PROVISIONING.md L32 |
| `VITE_STRIPE_PUBLISHABLE_KEY` | Stripe test-mode `pk_test_...` (D3 deferred) |
| `VITE_APP_BASE_URL` | literal `https://app-staging.bindwave.com` — **deferred-set** |

### 2.4 Domains

| Domain | Action | DNS status |
|--------|--------|------------|
| `bindwave.com` | Primary (Production) | DNS already set: apex A → 76.76.21.21 (DNS-only). Vercel auto-detects. |
| `www.bindwave.com` | **Redirect → `bindwave.com`** (308; www→apex) | DNS already set: CNAME → `cname.vercel-dns.com` proxied=true (PROVISIONING L129) |
| `staging.bindwave.com` | Bind to git branch `staging` | **DNS NOT set yet.** Add CNAME → `cname.vercel-dns.com` proxied=false via CF API alongside this Vercel config |

After domain add, Vercel auto-issues a cert (~5 min). CAA records at PROVISIONING L143–144 cover both letsencrypt.org and pki.goog.

### 2.5 Pitfall 5 enforcement — verified absent from Vercel

These keys must NOT appear in any Vercel env scope. CI grep in `.github/workflows/test.yml:89` enforces:

`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `DATABASE_URL*`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `WEBHOOK_HMAC_SECRET`, `WEBHOOK_HMAC_SECRET_PREV`, `RESEND_API_KEY`, `ANTHROPIC_API_KEY`, `CSRF_SECRET`, `REDIS_URL`, CF API tokens (PROVISIONING.md L92, L99, L118).

All 5 keys in §2.2/2.3 start with `VITE_`. Whitelist holds.

### 2.6 Post-setup verification

```
curl -sI https://bindwave.com           # expect HTTP/2 200 + strict-transport-security
curl -sI https://www.bindwave.com       # expect 308 → bindwave.com
```

Vercel build log: `vite build` completes, `dist/` produced, CI grep step returns empty.

---

## 3. GitHub — secrets + branch protection (Block D)

**Repo slug:** `leowan7/llm-proteinDesigner`
**Default branch:** `master`

### 3.1 Repo-level secrets (exactly what CI consumes — verified via grep of `${{ secrets.X }}`)

5 user-provided secrets + 1 auto-injected `GITHUB_TOKEN`:

| Secret name | Used by | Source |
|-------------|---------|--------|
| `MODAL_TOKEN_ID` | `deploy-modal.yml:71` | PROVISIONING.md L107 |
| `MODAL_TOKEN_SECRET` | `deploy-modal.yml:72` | PROVISIONING.md L108 |
| `SUPABASE_SERVICE_KEY` | `test.yml:48, 152` | PROVISIONING.md L33 (kendrew-staging service_role, per D2) |
| `SMOKE_TEST_EMAIL` | `smoke.yml:50` | create `smoke@bindwave.com` on prod app after Block C; store back in PROVISIONING.md |
| `SMOKE_TEST_PASSWORD` | `smoke.yml:51` | same |
| `GITHUB_TOKEN` | all `docker-*.yml` | auto-injected; do not set |

Paste-ready commands (run from repo root, `gh` auth'd as `leowan7`):

```
gh secret set MODAL_TOKEN_ID         # paste from PROVISIONING.md L107
gh secret set MODAL_TOKEN_SECRET     # paste from PROVISIONING.md L108
gh secret set SUPABASE_SERVICE_KEY   # paste from PROVISIONING.md L33
gh secret set SMOKE_TEST_EMAIL       # set after Block C (smoke account exists on prod)
gh secret set SMOKE_TEST_PASSWORD    # same
```

### 3.2 Branch protection on `master`

Per PLAN.md L309–313 + Pitfall 8:

| Setting | Value |
|---------|-------|
| Require pull request before merging | yes |
| Required approving reviews | 1 |
| Require review from Code Owners | no (no CODEOWNERS file exists) |
| Require status checks to pass | yes |
| Require branches up to date before merging | yes |
| Required status check contexts | `Backend Tests`, `Frontend Unit Tests`, `E2E Tests`, `Lint & Type Check` |
| Require conversation resolution | yes |
| Restrict who can push | yes (empty list = block all direct pushes) |
| Allow force pushes | no |
| Allow deletions | no |
| Do not allow bypassing | yes (admin-bypass off) |

### 3.3 `gh api` command (run after first PR has produced check runs)

```
gh api -X PUT repos/leowan7/llm-proteinDesigner/branches/master/protection \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Backend Tests", "Frontend Unit Tests", "E2E Tests", "Lint & Type Check"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
EOF
```

If the API rejects context names with "Context not found": GitHub only accepts contexts it has seen at least once. Open a no-op PR first, let `test.yml` run, then re-run.

### 3.4 Pitfall 8 — DO NOT enable Vercel Deployment Checks

Under GitHub repo Settings → GitHub Apps → Vercel: leave the "Required Checks" / Deployment Protection feature DISABLED. Vercel marks its check ✓ when the deployment job completes, but the preview URL DNS can lag 10–60s. Gate strictly on the four `test.yml` jobs above.

### 3.5 Order of operations

1. Set 5 repo secrets (no platform deps; do now).
2. Push a no-op PR. First `test.yml` run registers the 4 check context names in GitHub's index.
3. Enable branch protection via §3.3.
4. Confirm Vercel app under repo Settings → Integrations has no required-check toggle on.
5. After Block C Vercel deploy is green, create smoke test account on prod, then set `SMOKE_TEST_EMAIL` + `SMOKE_TEST_PASSWORD`.

---

## 4. Recommended click sequence for this session

If executing all three blocks today, the dependency-respecting sequence:

1. **D2**: `gh secret set SUPABASE_SERVICE_KEY` (paste PROVISIONING.md L33). Quick win.
2. **GitHub Modal secrets**: `gh secret set MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`. Quick win.
3. **Railway** — create services in order (1.1 → 1.2 → 1.3 → 1.4). Capture each `*.up.railway.app` hostname as it appears.
4. **CF DNS second pass** — add `app.bindwave.com` + `app-staging.bindwave.com` CNAMEs (proxied=false) via API once Railway hostnames known.
5. **Vercel** — create project, paste env vars per §2.2/2.3, add domains per §2.4. (Can run in parallel with steps 3–4 once Railway hostnames are known.)
6. **D1 code fix** — edit `frontend/src/lib/sentry.ts:8` to read `VITE_SENTRY_DSN_FRONTEND`; commit on a branch.
7. **No-op PR** to trigger first `test.yml` run, register check contexts.
8. **Branch protection** via §3.3.
9. **Smoke test account creation** on prod → set last 2 GH secrets.

User reply at end of Gate 2: `deployed` → advances to Gate 3 (Sentry wiring, UptimeRobot, prod GPU smoke, rollback drill).
