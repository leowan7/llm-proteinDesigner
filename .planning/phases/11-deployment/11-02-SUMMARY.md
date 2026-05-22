---
phase: 11-deployment
plan: 02
subsystem: infra
status: blocked-on-external-provisioning
tags: [deployment, provisioning, infrastructure, checkpoint-human-action]

requires:
  - phase: 11-deployment
    plan: 01
    provides: Supabase CLI baked into backend/Dockerfile; RED test scaffolds for dual-secret + /health; Modal BindCraft smoke + rollback drill scripts
provides:
  - .planning/phases/11-deployment/11-02-PROVISIONING.md skeleton (gitignored, local-only) — ready for Leo to paste real secret values from six external dashboards
  - PENDING: Supabase Pro projects (kendrew-prod, kendrew-staging) with PITR
  - PENDING: Upstash Redis databases (prod + staging, TLS)
  - PENDING: Cloudflare R2 buckets + per-bucket API tokens (prod + staging)
  - PENDING: Modal staging environment + MODAL_TOKEN_ID/SECRET
  - PENDING: Cloudflare DNS API token (Zone:DNS:Edit for kendrew.ai)
  - PENDING: Resend verified kendrew.ai domain + RESEND_API_KEY
affects: [11-03-deploy (Railway predeploy + staging wire-up), 11-04-deploy (Resend + asyncpg Supavisor), 11-05-deploy (Sentry + UptimeRobot)]

tech-stack:
  added: []
  patterns:
    - "Gitignored local-only secret matrix: Plan writes the skeleton, human fills values, Plan 11-03 consumes then deletes file (T-11-02-01 mitigation)"

key-files:
  created:
    - .planning/phases/11-deployment/11-02-PROVISIONING.md (gitignored; local-only skeleton)
  modified: []

key-decisions:
  - "Claude executes only the skeleton half of Plan 11-02; the six-service provisioning gate is a type=checkpoint:human-action step that Leo must complete in external dashboards"
  - ".gitignore line 36 already covered 11-02-PROVISIONING.md (added during Plan 11-01 prep) — no .gitignore modification was required, T-11-02-01 already satisfied at repo level before this plan ran"
  - "PROVISIONING.md contains placeholder values only (no real secrets). All lines of the template from the plan's <how-to-verify> block are preserved verbatim, including the `(paste)` markers and `[y/n]` checkboxes, so Leo's fill-in pass is mechanical"

requirements-completed: []

duration: ~3 min (Claude-side skeleton only)
completed: 2026-04-24 (Claude-side portion)
completion: BLOCKED on external-service provisioning by Leo
---

# Phase 11 Plan 02: Wave 1 Provisioning — Status: BLOCKED on External Provisioning

**Autonomous prep done. Plan 11-02 is NOT complete: its single task is `type="checkpoint:human-action"` and the six-service provisioning gate (Supabase Pro x2, Upstash x2, Cloudflare R2 x2, Modal staging env, Cloudflare DNS token, Resend domain) can only be done by Leo in external dashboards.**

## Status

- **Claude-side (autonomous prep): DONE**
  - `.planning/phases/11-deployment/11-02-PROVISIONING.md` created as a skeleton with every section from the plan's template.
  - `.gitignore` already covered the file path at line 36 (entry added during Plan 11-01 prep) — acceptance criterion T-11-02-01 already satisfied at the repo level.

- **Human-action gate (external provisioning): NOT STARTED**
  - Leo must execute Blocks A through F from `11-02-PLAN.md §how-to-verify` in external dashboards.
  - Estimated wall-clock: ~45-50 minutes active + up to 1 hour passive DNS propagation for Resend DKIM.
  - Until the six blocks are complete and PROVISIONING.md is populated with real values, **Plan 11-03 (Wave 2: Railway/Vercel deploy flip) is blocked** — it cannot paste secrets it does not have.

## Performance

- **Duration:** ~3 min (Claude-side skeleton only)
- **Started:** 2026-04-24T16:30:07Z
- **Claude-side completed:** 2026-04-24T16:30:54Z
- **Full plan completion:** pending Leo's dashboard work
- **Tasks executed autonomously:** 0 of 1 (the one task is a human-action checkpoint)

## Autonomous Work Completed

### PROVISIONING.md skeleton

Created `.planning/phases/11-deployment/11-02-PROVISIONING.md` with every heading from the plan's `<how-to-verify>` template, verbatim. Sections:

1. **Supabase (D-03)** — `kendrew-prod` and `kendrew-staging` subsections, each with Project URL, anon key, service_role key, JWT secret, DATABASE_URL (with `pooler.supabase.com:6543` pattern for Supavisor transaction mode), and PITR checkbox.
2. **Upstash Redis (D-03)** — `kendrew-prod` and `kendrew-staging` REDIS_URL slots, both using `rediss://` TLS scheme.
3. **Cloudflare R2 (D-03, D-11)** — bucket names, S3 endpoint URL, per-bucket token name, access key, secret key. Staging block explicitly flags "DIFFERENT token scoped to staging bucket" per T-11-02-02.
4. **Modal (D-07)** — MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, and an environment-existence checkbox for `main` and `staging`.
5. **Cloudflare DNS (D-02)** — API token slot and Zone ID slot.
6. **Resend (D-11)** — RESEND_API_KEY slot, domain-verified checkbox, SPF/DKIM/DMARC checkbox.

All `(paste)` markers and `[y/n]` checkboxes are preserved verbatim from the plan template so Leo's fill-in pass is mechanical.

### Acceptance-criteria grep verification (Claude-side, pre-fill-in)

On the skeleton file, all `<verify><automated>` grep checks pass:

- `grep "pooler.supabase.com:6543"` → 2 matches (prod + staging DATABASE_URL lines)
- `grep "rediss://"` → 2 matches (prod + staging REDIS_URL lines)
- `grep "kendrew-prod"` → 5 matches
- `grep "kendrew-staging"` → 5 matches
- `git check-ignore .planning/phases/11-deployment/11-02-PROVISIONING.md` → exit 0, matched by `.gitignore:36`

The remaining acceptance criteria (`modal environment list` showing `main` + `staging`; Resend dashboard showing kendrew.ai `Verified`) cannot pass until Leo completes the external provisioning.

## Deviations from Plan

### Auto-noted

**1. [Rule 3 adjacent] .gitignore was already populated for 11-02-PROVISIONING.md — no edit required**
- **Found during:** pre-flight check
- **Observation:** `.gitignore:36` already contains `.planning/phases/11-deployment/11-02-PROVISIONING.md` (it was added during Plan 11-01's scaffolding work, ahead of schedule).
- **Action:** None. Verified via `git check-ignore -v` that the path is ignored and matches the existing rule. T-11-02-01 is satisfied at the repo level before this plan ran.
- **Files modified:** none (no change to `.gitignore`).

### Auto-fixed issues

None — no bugs or missing critical functionality discovered. The only work for this plan was mechanical skeleton creation.

## What Leo Must Do Next (Checkpoint: human-action)

Execute Blocks A-F from `.planning/phases/11-deployment/11-02-PLAN.md §how-to-verify`:

| Block | Dashboard                  | Est. Time | Paste into PROVISIONING.md section        | Status |
| ----- | -------------------------- | --------- | ----------------------------------------- | ------ |
| A     | supabase.com/dashboard     | ~15 min   | §Supabase (both `kendrew-prod` + staging) | DONE 2026-04-29 (driven via Claude_in_Chrome MCP — see §Block A delta below) |
| B     | console.upstash.com        | ~5 min    | §Upstash Redis (both envs)                | pending |
| C     | Cloudflare dashboard → R2  | ~10 min   | §Cloudflare R2 (both buckets + tokens)    | pending |
| D     | `modal` CLI                | ~5 min    | §Modal (tokens + env-exists checkbox)     | pending (modal CLI 1.4.2 installed locally; `main` env exists, `staging` not yet created) |
| E     | dash.cloudflare.com → tokens | ~3 min  | §Cloudflare DNS                           | pending |
| F     | resend.com/domains         | ~10 min + propagation | §Resend (API key + verified checkboxes) | pending |

## Block A delta (2026-04-29)

Both Supabase projects are now provisioned and PROVISIONING.md §Supabase is fully populated.

| Env             | Project ref          | Compute | Region    | DB password source              |
| --------------- | -------------------- | ------- | --------- | ------------------------------- |
| kendrew-prod    | omrhpkmgiqvuwpadhbsl | NANO    | us-east-1 | reset 2026-04-29 (original lost) |
| kendrew-staging | sqcrsvcrpqckrupztesf | MICRO   | us-east-1 | auto-generated at creation      |

DATABASE_URL: dedicated transaction pooler at `db.<ref>.supabase.co:6543` (Pro tier benefit, IPv6 default). PROVISIONING.md also records the shared Supavisor URL (`aws-0-us-east-1.pooler.supabase.com:6543`) under DATABASE_URL_SHARED as an IPv4-compatible fallback in case Railway IPv6 has issues.

Two deploy concerns surfaced and recorded in PROVISIONING.md:

1. **JWT signing migrated HS256 → ECC P-256.** Supabase auto-rotated the project signing key. Anon and service_role tokens (long-lived, HS256-signed) still verify against the legacy secret, but new end-user session JWTs from Supabase Auth will be ECC-signed. backend/auth.py currently calls `jwt.decode(..., algorithms=["HS256"])` — this will fail for end-user logins after deploy. **Plan 11-04 needs to either** (a) migrate to JWKS-based verification (preferred), (b) determine if Supabase exposes an "unrotate" path back to HS256, or (c) refactor to use Publishable + Secret API keys (sb_publishable_*, sb_secret_*). 11-CONTEXT.md D-03 has been amended to call this out.

2. **PITR is a separate Pro add-on (~$100/mo per project), NOT auto-included.** Both projects currently have daily backup with 7-day retention only. ROADMAP.md SC 3 has been updated to reflect this. Decision needed before public launch on whether to enable the add-on.

Acceptance grep checks all still pass on the populated file:
- `grep "pooler.supabase.com:6543"` → matches (DATABASE_URL_SHARED lines in both env blocks)
- `grep "rediss://"` → matches (template Upstash placeholders, will get real values in Block B)
- `grep "kendrew-prod"` → matches (multiple)
- `grep "kendrew-staging"` → matches (multiple)
- `git check-ignore` → exit 0, matched by `.gitignore:50`

After Blocks A-F are done and PROVISIONING.md is fully filled in, reply with `"provisioned"` (or a safe summary that omits real secrets) to the orchestrator, which will resume Plan 11-03.

**Security reminders for Leo's dashboard work:**
- R2 tokens scoped per-bucket (T-11-02-02): the `kendrew-prod-r2` token must not see the `kendrew-staging` bucket, and vice versa.
- Cloudflare DNS token scoped to `kendrew.ai` zone only, not "All Zones" (T-11-02-03).
- Supabase staging created fresh; never `supabase db dump` prod → staging (T-11-02-04).
- service_role key goes into Railway backend vars only, never Vercel (T-11-02-07). Plan 11-03 runs `grep -r SUPABASE_SERVICE_ROLE frontend/` and that must return zero hits.
- PROVISIONING.md stays local; Plan 11-03 deletes it after consumption (T-11-02-01).

## Issues Encountered

None during the Claude-side skeleton pass. Issues discovered during Leo's dashboard work (e.g., Resend DKIM failing to propagate — Pitfall 6 in 11-RESEARCH.md) should be added to this summary's "Issues Encountered" section or a follow-up note before Plan 11-03 runs.

## User Setup Required

**ALL external service provisioning is required user setup.** See "What Leo Must Do Next" above. No additional scripts, commands, or local config changes are required from Claude's side — PROVISIONING.md is ready to be filled in as-is.

## Next Phase Readiness

- **Plan 11-03 (Wave 2 — Railway predeploy + staging deploy):** BLOCKED until Leo completes Blocks A-F and PROVISIONING.md is populated. Plan 11-03's first action is to paste those values into Railway Variables, Vercel Env Vars, and Modal Secrets.
- **Plan 11-04, 11-05:** transitively blocked (they depend on 11-03's live staging stack for verification).

---
*Phase: 11-deployment*
*Claude-side prep completed: 2026-04-24*
*Full plan completion: PENDING external provisioning by Leo*

## Self-Check: PASSED (Claude-side scope)

- FOUND: .planning/phases/11-deployment/11-02-PROVISIONING.md
- VERIFIED: `git check-ignore .planning/phases/11-deployment/11-02-PROVISIONING.md` exits 0 (matched by .gitignore:36)
- VERIFIED: grep "pooler.supabase.com:6543" PROVISIONING.md → 2 matches
- VERIFIED: grep "rediss://" PROVISIONING.md → 2 matches
- VERIFIED: grep "kendrew-prod" PROVISIONING.md → 5 matches
- VERIFIED: grep "kendrew-staging" PROVISIONING.md → 5 matches
- VERIFIED: PROVISIONING.md contains zero emoji characters (Python regex scan)
- VERIFIED: PROVISIONING.md contains placeholder values only; no real secrets
- NOT APPLICABLE (external provisioning pending): `modal environment list` showing `staging`; Resend `kendrew.ai Verified`; per-bucket R2 token scopes
