---
phase: 11-deployment
plan: 04
subsystem: backend
tags: [deployment, webhook, config, asyncpg, security, supavisor]

requires:
  - phase: 11-deployment
    plan: "01"
    provides: RED scaffold for validate_webhook_signature dual-secret (xfail)
provides:
  - Dual-secret webhook verification (validate_webhook_signature) at /webhooks/runpod and /webhooks/heartbeat (D-10)
  - backend/config.py rename webhook_hmac_secret + webhook_hmac_secret_prev with runpod_webhook_secret deprecated alias (D-10)
  - asyncpg pool hardened for Supavisor transaction mode (statement_cache_size=0, max_inactive_connection_lifetime=0) (Pitfall 2)
  - .env.example full prod-ready template — 29 runtime-scope tags, 4 distinct scopes (D-11, D-12)
affects: [11-05-deploy, Plan 11-05 rotation runbook in docs/deploy.md]

tech-stack:
  added: []
  patterns:
    - "Pydantic v2 model_post_init hook to resolve deprecated env-var aliases into canonical fields (backwards-compat on rename)"
    - "Dual-path HMAC verification — primary secret then _PREV with WARNING log on prev-match so rotation operator sees live traffic"
    - "asyncpg create_pool(statement_cache_size=0, max_inactive_connection_lifetime=0) for Supavisor transaction-mode compatibility"

key-files:
  created: []
  modified:
    - backend/config.py
    - backend/webhooks/router.py
    - backend/db/connection.py
    - backend/tests/webhooks/test_dual_secret.py
    - backend/tests/webhooks/test_router.py
    - .env.example

key-decisions:
  - "Kept validate_runpod_signature as a thin backwards-compat shim (delegates to validate_webhook_signature) rather than deleting it outright, so any out-of-tree caller still links; shim is documented for removal after next phase."
  - "model_post_init resolves runpod_webhook_secret -> webhook_hmac_secret only when the new field is empty; this makes the rename a zero-downtime change if Railway Variables are still RUNPOD_WEBHOOK_SECRET at deploy time."
  - "Set WEBHOOK_HMAC_SECRET_PREV empty (not a placeholder) in .env.example so an operator who never rotates never silently accepts a prev-secret. _PREV is opt-in to the rotation flow."
  - "Deferred switching the class-based `class Config:` to Pydantic v2 ConfigDict — deprecation warning is harmless and out of scope for 11-04 (logged as deferred)."

patterns-established:
  - "Webhook dual-secret rotation: primary secret matched first, then previous secret; prev-match emits structured WARNING so Sentry catches stale-traffic during the rotation window."
  - ".env.example is the single source of truth for prod config surface — every Pydantic Settings field that matters in prod appears here with a runtime-scope comment."

requirements-completed: []

duration: 6min
completed: 2026-04-24
---

# Phase 11 Plan 04: Dual-Secret Webhook + Supavisor asyncpg + .env.example Audit Summary

**Dual-secret HMAC rotation (D-10) wired at both webhook endpoints, config renamed to webhook_hmac_secret with backwards-compat alias, asyncpg pool hardened for Supavisor transaction mode, .env.example rewritten as a prod-ready template with 29 runtime-scope comments.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-04-24T16:16:39Z
- **Completed:** 2026-04-24T16:22:06Z
- **Tasks:** 2
- **Files modified:** 6 (6 modified, 0 created)

## Accomplishments

- `validate_webhook_signature(body, signature, current_secret, prev_secret)` implemented in `backend/webhooks/router.py` with dual-path HMAC: primary then _PREV. Prev-match returns "prev" AND logs WARNING `"Webhook signed with PREV secret — rotation window active"` so the operator sees live stale-secret traffic during rotation.
- Both `/webhooks/runpod` and `/webhooks/heartbeat` now accept either `X-RunPod-Signature` or `X-Modal-Signature` headers — Modal- and RunPod-origin webhooks both validate against the single shared `webhook_hmac_secret`.
- `backend/config.py` exposes new fields `webhook_hmac_secret` and `webhook_hmac_secret_prev`; `runpod_webhook_secret` retained as DEPRECATED alias; `model_post_init` fills `webhook_hmac_secret` from `runpod_webhook_secret` when the new field is empty — zero-downtime rename.
- `backend/db/connection.py` rewritten with `statement_cache_size=0` + `max_inactive_connection_lifetime=0` on `asyncpg.create_pool` (Pitfall 2 / RESEARCH Pattern 5). Module docstring explains the Supavisor transaction-mode rationale and links the Supabase GitHub issue.
- `.env.example` full rewrite: 29 `# runtime:` tags across 4 distinct runtime scopes (Backend, Worker, Backend+Worker, Frontend). Adds `WEBHOOK_HMAC_SECRET` + `WEBHOOK_HMAC_SECRET_PREV` (D-10), `GPU_DAILY_SPEND_ALERT_USD` (D-16), `VITE_SENTRY_DSN_FRONTEND`, `RESEND_API_KEY`, Modal tokens, explicit `# NEVER put SUPABASE_SERVICE_ROLE_KEY in Vercel — backend only` comment.
- Wave-0 RED scaffold flipped GREEN: 3 xfail tests in `test_dual_secret.py` now pass. Added 3 new tests (missing-signature 401, empty-secret dev-skip, deprecated alias resolution) — 6 total pass.

## Task Commits

Each task was committed atomically:

1. **Task 1: Dual-secret webhook rotation + config rename (D-10)** — `c53f9f2` (feat)
2. **Task 2: Harden asyncpg for Supavisor + full .env.example audit** — `157909c` (feat)

## Files Created/Modified

- `backend/config.py` — modified: added `webhook_hmac_secret` and `webhook_hmac_secret_prev` fields after `runpod_api_key`, kept `runpod_webhook_secret` as a documented DEPRECATED alias, added `model_post_init` hook just above `class Config` to resolve the alias into `webhook_hmac_secret` when the new field is empty.
- `backend/webhooks/router.py` — modified: replaced `validate_runpod_signature` (single-secret, RunPod-only) with `validate_webhook_signature` (dual-secret, Modal + RunPod). Kept the old name as a 1-line backwards-compat shim. Updated both call sites (`runpod_webhook` line 89, `heartbeat_webhook` line 302) to accept either `X-RunPod-Signature` or `X-Modal-Signature` and pass current + prev secrets explicitly.
- `backend/db/connection.py` — full file rewrite: added module docstring referencing Phase 11 D-06 + RESEARCH Pitfall 2, added `statement_cache_size=0` and `max_inactive_connection_lifetime=0` to `asyncpg.create_pool` call.
- `backend/tests/webhooks/test_dual_secret.py` — modified: removed module-level `pytest.mark.xfail`, moved imports to module scope (no longer xfail-guarded), added 3 new tests (`test_missing_signature_raises_401`, `test_empty_both_secrets_returns_dev_skip`, `test_deprecated_runpod_webhook_secret_alias_resolves`).
- `backend/tests/webhooks/test_router.py` — modified (Rule 1 deviation): 2 existing tests (`test_webhook_signature_validation`, `test_webhook_valid_signature`) set `mock_settings.runpod_webhook_secret` only; because the new router reads `settings.webhook_hmac_secret` and MagicMock auto-attrs are truthy non-strings, these would have broken. Added explicit `mock_settings.webhook_hmac_secret = secret` + `mock_settings.webhook_hmac_secret_prev = ""` in both tests. Kept the old alias line to document intent.
- `.env.example` — full file rewrite per D-11: 141 lines, every Pydantic Settings prod field has a placeholder and a `# runtime:` comment, 29 runtime tags total, 4 distinct scopes (Backend, Worker, Backend+Worker, Frontend).

## Decisions Made

- **Keep `validate_runpod_signature` as a backwards-compat shim (not delete).** A thin 1-line delegator to `validate_webhook_signature` is cheap to retain for one release cycle and guards against any out-of-tree caller we did not grep up. Marked for removal in next phase.
- **`model_post_init` alias resolution is strictly opt-in.** Only fills `webhook_hmac_secret` when it is empty AND `runpod_webhook_secret` is set. Prevents accidental downgrade if the new field is deliberately set to an empty-string override.
- **`WEBHOOK_HMAC_SECRET_PREV` ships empty in `.env.example`.** No placeholder like `whsec_prev_` — an empty value routes through the current-only path and the operator explicitly opts into rotation by setting the prev value during a rotation window. Prevents accidental "always accept prev" behavior in a greenfield deploy.
- **Both webhook endpoints accept `X-Modal-Signature` OR `X-RunPod-Signature`.** Current RunPod fallback still uses X-RunPod-Signature; future Modal signed webhooks will use X-Modal-Signature. Accepting either keeps the endpoint provider-agnostic with a single shared secret (per D-10 amended 2026-04-24).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_router.py signature tests for config rename**
- **Found during:** Task 1 verification
- **Issue:** `test_webhook_signature_validation` and `test_webhook_valid_signature` use `with patch("webhooks.router.settings") as mock_settings` and set only `mock_settings.runpod_webhook_secret = secret`. The new router reads `settings.webhook_hmac_secret`, which on a `MagicMock` auto-resolves to a truthy `MagicMock` object (not the real secret string), so `hmac.new(secret.encode(), ...)` would fail on `.encode()` attribute of MagicMock and the signature equality would not match the test expectations.
- **Fix:** Explicitly set `mock_settings.webhook_hmac_secret = secret` and `mock_settings.webhook_hmac_secret_prev = ""` in both tests. Kept the existing `mock_settings.runpod_webhook_secret = secret` line for documentation of the deprecated alias contract.
- **Files modified:** `backend/tests/webhooks/test_router.py`
- **Commit:** `c53f9f2` (Task 1)

No other deviations. Plan executed as written; all acceptance grep/pytest checks passed on first attempt after the Rule-1 fix above.

## Deferred Issues

- Pydantic v2 deprecation warning: `config.py` uses class-based `class Config:` which is deprecated in Pydantic 2.0. Harmless — continues to work through Pydantic 3.0. Migration to `model_config = ConfigDict(...)` is a codebase-wide cleanup unrelated to 11-04 scope. Logged for a future hygiene phase.

## Issues Encountered

None.

## User Setup Required

None at code level. Downstream operational setup (to be done in Plan 11-05 or by operator before prod deploy):
- Set `WEBHOOK_HMAC_SECRET` in Railway Variables (backend + worker services). If `RUNPOD_WEBHOOK_SECRET` is already set, the `model_post_init` hook will resolve it — but setting the new name is the clean path.
- Leave `WEBHOOK_HMAC_SECRET_PREV` empty until the first rotation; populate only during a rotation window per the runbook landing in Plan 11-05.
- Confirm `DATABASE_URL` in prod points at Supavisor port 6543 (transaction pooler), not 5432 — the asyncpg hardening assumes it.

## Acceptance Verification

All Plan 11-04 acceptance grep and pytest checks pass:

- `grep -c "webhook_hmac_secret" backend/config.py` = 7 (>= 3)
- `grep -c "webhook_hmac_secret_prev" backend/config.py` = 2
- `grep -c "runpod_webhook_secret" backend/config.py` = 4 (alias retained)
- `grep -c "validate_webhook_signature" backend/webhooks/router.py` = 5 (>= 3: def + 2 call sites + 1 shim call + 1 docstring)
- `grep -c "PREV secret" backend/webhooks/router.py` = 2
- `grep -c "validate_runpod_signature(" backend/webhooks/router.py` = 1 (shim call inside deprecated wrapper — no live callers)
- `grep -c "X-Modal-Signature" backend/webhooks/router.py` = 3 (both endpoints + header parsing)
- `grep -c "pytest.mark.xfail" backend/tests/webhooks/test_dual_secret.py` = 0 (xfail removed)
- `grep -c "statement_cache_size=0" backend/db/connection.py` = 3
- `grep -c "max_inactive_connection_lifetime=0" backend/db/connection.py` = 3
- `grep -c "Supavisor" backend/db/connection.py` = 4
- `grep -c "WEBHOOK_HMAC_SECRET" .env.example` = 3 (>= 2)
- `grep -c "WEBHOOK_HMAC_SECRET_PREV" .env.example` = 1
- `grep -c "# runtime:" .env.example` = 29 (>= 15)
- `grep -c "GPU_DAILY_SPEND_ALERT_USD" .env.example` = 1
- `grep -c "VITE_SENTRY_DSN_FRONTEND" .env.example` = 2
- `grep -c "NEVER put SUPABASE_SERVICE_ROLE_KEY in Vercel" .env.example` = 1
- Distinct runtime scopes in .env.example = 4 (Backend, Backend+Worker, Frontend, Worker) (>= 4)
- `pytest tests/webhooks/test_dual_secret.py tests/webhooks/test_router.py -v` = 13 passed
- `python -c "from config import settings; print(settings.webhook_hmac_secret)"` = exits 0
- `python -c "from db.connection import get_db_pool"` = exits 0
- `python -c "from config import settings; assert hasattr(settings, 'webhook_hmac_secret')"` = exits 0

## Next Phase Readiness

- **Plan 11-05 (monitoring + docs + rollback, Wave 4):** Unblocked. The rotation runbook in `docs/deploy.md` now has a concrete code target: `WEBHOOK_HMAC_SECRET` (current) + `WEBHOOK_HMAC_SECRET_PREV` (grace-window). The `.env.example` template is ready to be the canonical paste-source for Railway Variables.
- **Plan 11-03 (Railway predeploy + staging, Wave 2, not executed per autonomous-only mode):** Unblocked by the Pydantic alias — any existing `RUNPOD_WEBHOOK_SECRET` env var resolves correctly.

---
*Phase: 11-deployment*
*Completed: 2026-04-24*

## Self-Check: PASSED

- FOUND: backend/config.py
- FOUND: backend/webhooks/router.py
- FOUND: backend/db/connection.py
- FOUND: backend/tests/webhooks/test_dual_secret.py
- FOUND: backend/tests/webhooks/test_router.py
- FOUND: .env.example
- FOUND: .planning/phases/11-deployment/11-04-SUMMARY.md
- FOUND commit: c53f9f2 (Task 1)
- FOUND commit: 157909c (Task 2)
- VERIFIED: pytest tests/webhooks/ -v = 13 passed, 0 failed
- VERIFIED: grep -c "statement_cache_size=0" backend/db/connection.py = 3 (>= 1)
- VERIFIED: grep -c "# runtime:" .env.example = 29 (>= 15)
- VERIFIED: grep -c "pytest.mark.xfail" backend/tests/webhooks/test_dual_secret.py = 0
- VERIFIED: python -c "from config import settings; assert hasattr(settings, 'webhook_hmac_secret')" exits 0
- VERIFIED: python -c "from db.connection import get_db_pool" exits 0
- VERIFIED: SUMMARY.md contains zero emoji characters
