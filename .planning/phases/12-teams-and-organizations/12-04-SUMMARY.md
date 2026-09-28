---
phase: 12-teams-and-organizations
plan: 04
subsystem: backend-scripts
tags: [organizations, stripe, scripts, migration, one-shot, idempotent]

# Dependency graph
requires:
  - phase: 12-teams-and-organizations
    plan: 01
    provides: public.organizations table + personal-org backfill with stripe_customer_id moved from public.users
  - phase: 12-teams-and-organizations
    plan: 03
    provides: billing/stripe_client.get_or_create_customer writes Stripe metadata {organization_id, kendrew_org_name} for new customers (shape this script's stamp must match)
provides:
  - backend/scripts/stamp_stripe_org_metadata.py one-shot CLI (--dry-run / --test-mode / --limit)
  - backend/scripts/verify_stripe_org_metadata.py post-run validator (--test-mode)
  - 11 mocked-Stripe unit tests covering dry-run, idempotency, rate-limit retry, failure isolation, metadata shape, --limit clause, exit codes
  - Idempotent metadata stamp shape {organization_id, kendrew_org_name, is_personal, migrated_from_user_v1} matching 12-03 stripe_client writes (audit-only keys for migrated rows)
affects: [12-05-frontend, 12-06-cleanup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dark-code one-shot script under backend/scripts/: argparse + asyncpg pool + Stripe SDK; invoked manually by rollout runbook, not mounted in FastAPI app"
    - "Idempotency-via-read-before-write: stripe.Customer.retrieve checks metadata.organization_id, skips stripe.Customer.modify on match"
    - "Operator safety guard: --test-mode flag forces a separate STRIPE_TEST_SECRET_KEY env var so live keys cannot leak into a dry-run rehearsal"
    - "Structured stdout (one JSON line per row + final summary) + stderr-only logging so operator can pipe stdout to an audit log"
    - "Exponential backoff (1s, 2s, 4s) on stripe.error.RateLimitError up to MAX_RETRIES=3"

key-files:
  created:
    - backend/scripts/stamp_stripe_org_metadata.py
    - backend/scripts/verify_stripe_org_metadata.py
    - backend/tests/scripts/__init__.py
    - backend/tests/scripts/test_stamp_stripe_org_metadata.py
  modified: []

key-decisions:
  - "Idempotency check is keyed only on metadata.organization_id (not the full 4-key payload). kendrew_org_name and migrated_from_user_v1 can legitimately drift between runs (renames, re-runs on a later date); organization_id is the ground truth -- if it matches, the customer is bound to the right org and the script skips the API call entirely."
  - "Script never creates Stripe customers. Net-new team orgs lazily create their first customer via billing/stripe_client.get_or_create_customer on first billing interaction (post-12-03 cutover); historical personal orgs already have a customer_id (12-01 backfill moved it from users)."
  - "Single-line SQL string in both scripts. The acceptance-criteria grep checks for the literal substring 'SELECT id, name, stripe_customer_id, ... FROM public.organizations' and a Python implicit-concatenation form ('SELECT ... ' 'FROM ... ') would not satisfy that grep."
  - "sys.path.insert(0, '/app') AND sys.path.insert(0, '../backend') at module top so the script works both inside the Railway container (where /app is the backend root) and on a developer's machine (cd backend && python scripts/stamp_stripe_org_metadata.py)."
  - "Logging goes to stderr; data lines go to stdout. Operator runs as `python scripts/stamp_stripe_org_metadata.py > stamp-$(date).log` and the log file is pure JSON Lines suitable for jq."
  - "11 tests, not 5. The plan's acceptance criteria require 5 named tests; the behavior block also calls for --limit + exit-code coverage. Added 4 additional tests for the rebind case (mismatched existing organization_id), is_personal=false payload rendering, rate-limit-exhausted->failed, and exit-code-zero-when-all-skipped. All mocked, all fast (<0.2s suite runtime)."

patterns-established:
  - "Dark-code script pattern: lives under backend/scripts/, sets sys.path for both /app (container) and backend/ (dev), reads config.settings, opens a service-role asyncpg pool, no router mount, no FastAPI app changes"
  - "Idempotent Stripe metadata writer: retrieve first, compare on a single ground-truth key, modify only when the ground-truth key is absent or mismatched"
  - "Mocked-Stripe test pattern: patch.object(m.stripe.Customer, 'retrieve'|'modify') + patch.object(m.time, 'sleep') for retry tests + redirect_stdout(io.StringIO()) for stdout assertions on the CLI entry point"

requirements-completed: []

# Metrics
duration: 6min
completed: 2026-06-04
---

# Phase 12 Plan 04: Stripe Metadata Stamping Summary

**Two new one-shot Python scripts under `backend/scripts/` that push the Phase 12 organization metadata onto every migrated Stripe customer and then verify the rollout — plus 11 mocked-Stripe unit tests that lock down idempotency, dry-run, rate-limit backoff, failure isolation, payload shape, --limit, and exit codes. Dark code: zero FastAPI app surface touched; this plan ships entirely within the script harness, invoked manually by the rollout runbook documented in RESEARCH §12.1.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-06-04T11:13:13Z
- **Completed:** 2026-06-04T11:19:33Z
- **Tasks:** 2
- **Files created:** 4

## Accomplishments

- `backend/scripts/stamp_stripe_org_metadata.py` (226 lines) — argparse CLI with `--dry-run`, `--test-mode`, `--limit N`. Iterates `public.organizations WHERE stripe_customer_id IS NOT NULL ORDER BY created_at ASC`. For each row, calls `stripe.Customer.retrieve` first to check `metadata.organization_id`; skips `stripe.Customer.modify` when already tagged (idempotency). On rate-limit, exponential backoff up to 3 attempts. One JSON line per row to stdout (`{org_id, name, customer_id, outcome, ...}`); final summary line with counts. Exits 1 if any row failed.
- `backend/scripts/verify_stripe_org_metadata.py` (110 lines) — argparse CLI with `--test-mode`. Reads every `organizations.stripe_customer_id`, retrieves the Stripe customer, asserts `metadata.organization_id == orgs.id`. Captures up to 25 mismatched rows in a JSON summary. Exits 1 on any mismatch.
- `backend/tests/scripts/__init__.py` — test-package marker.
- `backend/tests/scripts/test_stamp_stripe_org_metadata.py` (244 lines) — 11 mocked-Stripe tests; suite runs in <0.2s.

## Task Commits

1. **Task 1: stamp_stripe_org_metadata.py one-shot script** — `2031ec4` (feat: 1 file, 226 insertions)
2. **Task 2: verify_stripe_org_metadata.py + 11 unit tests** — `ac86225` (test: 3 files, 355 insertions)

**Plan metadata commit:** _(see final `docs(12-04)` commit at plan close-out)_

## Files Created/Modified

### Created

- `backend/scripts/stamp_stripe_org_metadata.py` — One-shot CLI that stamps Phase 12 org metadata onto migrated Stripe customers. Idempotent. Supports dry-run + test-mode + limit. Lives outside the FastAPI app.
- `backend/scripts/verify_stripe_org_metadata.py` — Post-run validator. Reads every org's Stripe customer and asserts `metadata.organization_id` matches the DB row.
- `backend/tests/scripts/__init__.py` — Test-package marker for the new scripts test directory.
- `backend/tests/scripts/test_stamp_stripe_org_metadata.py` — 11 mocked-Stripe unit tests covering all plan-required behaviors plus 4 reasonable extras (rebind case, is_personal=false rendering, rate-limit-exhausted, exit-code-zero-on-all-skipped).

### Modified

None. Plan executed exactly within the `files_modified` enumeration in the frontmatter. No FastAPI app changes (no router, no main.py, no settings additions).

## Decisions Made

- **Idempotency key is `organization_id` alone, not the full 4-key payload.** `kendrew_org_name`, `is_personal`, and `migrated_from_user_v1` can legitimately drift between runs (org renames mid-rollout, re-runs on a later calendar date, etc.). Using `organization_id` as the ground truth makes the script a no-op when the customer is already bound to the right org, regardless of whether the audit metadata is stale.
- **Script never creates Stripe customers.** Net-new team orgs created post-12-03 lazily create their first customer via `billing/stripe_client.get_or_create_customer` on first billing interaction. Historical personal orgs already have a `cus_...` ID (the 12-01 backfill moved it from `users.stripe_customer_id`). This script only stamps metadata on existing customers — never `stripe.Customer.create`.
- **Single-line SQL string in both scripts.** Originally wrote it as Python implicit-concatenation across three lines for readability; the acceptance-criteria grep wants the literal substring `SELECT id, name, stripe_customer_id, is_personal FROM public.organizations`, which the multi-line form does not satisfy. Switched to a single-line form. Same trade in the verify script.
- **`sys.path.insert(0, "/app")` AND `sys.path.insert(0, "../backend")` at module top.** Script must run both inside the Railway container (where `/app` is the backend root) and on a developer's laptop (cd backend && python scripts/...). Inserting both makes `from config import settings` resolve in either context.
- **stdout is JSON Lines (one row per org + summary). Logging goes to stderr.** Operator runs `python scripts/stamp_stripe_org_metadata.py > stamp-$(date +%F).log` and the log file is pure JSONL ready for jq/grep. Progress messages and rate-limit warnings stay on stderr so they don't corrupt the audit trail.
- **11 tests, not 5.** The plan's acceptance criteria require 5 named tests; the behavior block also calls for `--limit` + exit-code coverage. Added 6 additional tests:
  - `test_modifies_when_organization_id_mismatched` — rebind case (Stripe customer was once on another org).
  - `test_metadata_is_personal_false_renders_as_false_string` — type contract for the boolean→string conversion.
  - `test_rate_limit_exhausted_returns_failed` — proves the script doesn't silently hang on a Stripe outage.
  - `test_limit_arg_appears_in_sql` — proves `--limit 7` injects `LIMIT 7` into the fetch SQL.
  - `test_exit_code_one_on_any_failure` — exit-code contract on the failure path.
  - `test_exit_code_zero_when_all_skipped` — exit-code + no-API-write contract on the idempotent-re-run path.
  All mocked; suite runs in <0.2s.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Multi-line SQL string broke the acceptance-criteria substring grep**
- **Found during:** Task 1 acceptance-criteria check (substring scan after first write of `stamp_stripe_org_metadata.py`)
- **Issue:** Wrote the SQL as Python implicit-concatenation across three lines (`"SELECT id, name, stripe_customer_id, is_personal " "FROM public.organizations " ...`). Idiomatic Python, but the acceptance criterion greps for the literal phrase `SELECT id, name, stripe_customer_id, is_personal FROM public.organizations` and the multi-line form puts a newline between `is_personal` and `FROM`, so grep doesn't match.
- **Fix:** Switched to a single-line SQL string in both `stamp_stripe_org_metadata.py` (line 168) and `verify_stripe_org_metadata.py` (line 58). Added a code comment in the stamp script (`# Single-line SQL so grep on the acceptance-criteria substring matches.`) to document why.
- **Files modified:** `backend/scripts/stamp_stripe_org_metadata.py`, `backend/scripts/verify_stripe_org_metadata.py`
- **Verification:** `grep -c "SELECT id, name, stripe_customer_id, is_personal FROM public.organizations" backend/scripts/stamp_stripe_org_metadata.py` returns 1.
- **Committed in:** `2031ec4` (Task 1 commit) for the stamp script; the verify script was first written with the single-line form because the lesson was already learned.

### Out-of-Scope Items (deferred)

None. Plan scope was tight by design.

---

**Total deviations:** 1 auto-fixed (1 bug — cosmetic SQL formatting that broke the grep contract). No scope creep.
**Impact on plan:** Behavior-preserving; SQL is identical between the multi-line and single-line forms at runtime.

## Issues Encountered

None requiring user input.

## Authentication Gates

None. The plan does not invoke a real Stripe API; everything is mocked in tests. The runbook (Phase 12-06) is responsible for setting `STRIPE_TEST_SECRET_KEY` and `STRIPE_SECRET_KEY` in the Railway shell before invoking the script.

## User Setup Required

None for plan execution. The runbook (12-06) instructs the operator:

1. Ensure `STRIPE_TEST_SECRET_KEY` is set in the Railway test environment (Stripe Dashboard → Developers → API keys → Test mode → "Reveal test key"). Document: this is the same Stripe account, just the test-mode key.
2. Run `python backend/scripts/stamp_stripe_org_metadata.py --test-mode --dry-run` and review the JSON-Lines stdout.
3. Run `python backend/scripts/stamp_stripe_org_metadata.py --test-mode` (live test-mode write); confirm `outcome: modified` for all rows in stdout.
4. Run `python backend/scripts/verify_stripe_org_metadata.py --test-mode`; confirm `mismatch_count: 0`.
5. Repeat steps 2–4 with `STRIPE_SECRET_KEY` (no `--test-mode` flag) for production.

## Next Phase Readiness

Wave 2's third deliverable is in place. Plan 12-05 (frontend) and 12-06 (cleanup) are unblocked:

- **12-05 (frontend)** can rely on the fact that, after the runbook runs this script in production, every Stripe customer in `public.organizations` carries `metadata.organization_id`. Support tickets, Stripe Dashboard customer pages, and Stripe webhooks (Customer-scoped) will all surface the right org context. Frontend has no new wiring to do for this — it's a Stripe-side change.
- **12-06 (cleanup + runbook)** owns:
  - The Phase 12 rollout runbook section that invokes `stamp_stripe_org_metadata.py` (test-mode rehearsal → live run → verify) BEFORE the `users.stripe_customer_id` column-drop migration.
  - The actual `20260606000001_drop_users_stripe_customer.sql` migration. This script's successful production run is the prerequisite — 12-06's column drop must NOT execute until `verify_stripe_org_metadata.py --no-test-mode` exits 0.
  - The ORG-04 traceability row in REQUIREMENTS.md.

Threat-register mitigations from the plan's `<threat_model>`:

- T-12-04-01 (Tampering — wrong Stripe customer): mitigated. Script reads `stripe_customer_id` from DB (single source of truth); `--dry-run` lets the operator review the intended payload before going live.
- T-12-04-02 (Tampering — accidental prod run): mitigated. `--test-mode` is an explicit flag that swaps env vars; default is live mode (so the runbook MUST opt in to test-mode for the rehearsal). The two env vars (`STRIPE_TEST_SECRET_KEY` and `STRIPE_SECRET_KEY`) are separately named to prevent confusion.
- T-12-04-03 (Info disclosure via metadata): accepted (RESEARCH §A2; Stripe Customer metadata is visible only within the Kendrew Stripe account).
- T-12-04-04 (DoS via customer.updated flood): accepted (RESEARCH §A2 assumption that metadata-only modifies don't fire customer.updated). If wrong, the webhook handler is already idempotent and rate-limited.
- T-12-04-05 (Tampering — re-run duplicates work): mitigated. `_is_already_tagged` check returns "skipped-already-tagged" without calling `stripe.Customer.modify`; covered by `test_idempotency_skips_already_tagged` AND `test_exit_code_zero_when_all_skipped`.
- T-12-04-06 (Repudiation — no audit trail): mitigated. Per-org JSON line on stdout; operator pipes to log file at runbook time. The verify script reads back and produces a second audit record.
- T-12-04-07 (Spoofing — bad DATABASE_URL): mitigated. `settings.database_url` loads from env; `asyncpg.create_pool` fails fast with a clear error if the URL is malformed.

## Threat Flags

None. No new network endpoints, no auth paths, no file access patterns, no schema changes. This plan adds dark code that runs only when an operator explicitly invokes it; the runbook in 12-06 is the gate.

## Self-Check: PASSED

- `backend/scripts/stamp_stripe_org_metadata.py` — FOUND (contains `argparse.ArgumentParser`, `--dry-run`, `--test-mode`, `--limit`, `stripe.Customer.modify`, `stripe.Customer.retrieve`, `_build_metadata`, `kendrew_org_name`, `is_personal`, `migrated_from_user_v1`, `stripe.error.RateLimitError`, the single-line SQL substring, `WHERE stripe_customer_id IS NOT NULL`, `json.dumps`)
- `backend/scripts/verify_stripe_org_metadata.py` — FOUND (contains `stripe.Customer.retrieve`, 8 `metadata` references, single-line SQL substring, `return 1 if mismatches else 0`)
- `backend/tests/scripts/__init__.py` — FOUND
- `backend/tests/scripts/test_stamp_stripe_org_metadata.py` — FOUND (contains all 5 plan-required test names plus 6 additional tests)
- `backend/scripts/stamp_stripe_org_metadata.py --help` exits 0 and prints `--dry-run`, `--test-mode`, `--limit` — VERIFIED
- All 4 files `python -m py_compile` exit 0 — VERIFIED
- `pytest tests/scripts/test_stamp_stripe_org_metadata.py --collect-only -q` reports 11 items — VERIFIED
- `pytest tests/scripts/test_stamp_stripe_org_metadata.py -x -q` reports 11 passed in <0.2s — VERIFIED
- `pytest tests/scripts/ tests/billing/test_meter.py tests/billing/test_meter_org.py -q` reports 16 passed — VERIFIED (no regressions in adjacent billing tests)
- Commits `2031ec4` (Task 1) and `ac86225` (Task 2) — FOUND in `git log --oneline`
- Zero changes outside `backend/scripts/` and `backend/tests/scripts/` — VERIFIED via `git diff 44fabf4..HEAD --stat`

---
*Phase: 12-teams-and-organizations*
*Completed: 2026-06-04*
