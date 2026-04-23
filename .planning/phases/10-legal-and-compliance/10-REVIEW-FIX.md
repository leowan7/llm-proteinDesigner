---
phase: 10-legal-and-compliance
status: complete
source: 10-REVIEW.md
fixed: 2026-04-23
diff_base: 8e342c2
head: c911192
findings_addressed:
  critical: 2
  warning: 9
  info: 1
  total: 12
findings_deferred:
  info: 5
  total: 5
---

# Phase 10: Code Review Fix Report

**Scope:** Critical + Warning findings + IN-03 (addressed alongside CR-02).
**Strategy:** One atomic commit per finding ID. All 77 Phase 10 unit tests green after fixes.
**Migrations:** Two schema migrations applied to local Supabase before code fixes could land.

---

## Fixes Applied

### CR-01: audit_log FK relaxed to ON DELETE SET NULL

**Commit:** `6ac2d52`
**Files:** `supabase/migrations/20260424000004_audit_log_fk.sql`

Dropped `NOT NULL` constraint and changed FK policy from no-action to `ON DELETE SET NULL`.
Without this, `execute_hard_delete` would raise a FK violation the moment `delete_auth_user`
triggered the `public.users` cascade, leaving `auth.users` deleted but `public.users` intact.

Regression test added in `test_deletion.py::test_cr01_hard_delete_reaches_auth_delete_with_prior_audit_log_row`
documenting that the executor now completes and notes that a live-DB integration test
should additionally assert the audit_log row survives with `admin_user_id = NULL`.

---

### CR-02: Export presigned URL no longer persisted to DB

**Commits:** `36acab2` (migration), `c911192` (code)
**Files:**
- `supabase/migrations/20260424000005_export_url_cleanup.sql` — drops `last_export_url`, adds `last_export_key`
- `backend/user/export.py` — persists `last_export_key` (S3 object key) only; presigns 1-hour URL for email only; TTL reduced from 24 h to 1 h
- `backend/user/router.py` — `GET /user/data-export` imports `generate_presigned_get_url` and re-presigns on each authenticated GET using remaining TTL (capped at 1 h)
- `backend/tests/user/test_export.py` — mock rows updated to `last_export_key`; "ready" test patches `generate_presigned_get_url` and asserts it is called exactly once per GET

Previously a 24-hour bearer credential was stored in `public.users.last_export_url` and returned verbatim, exposing the ZIP to anyone with DB read access. Now only the object key is stored; the URL is minted fresh per authenticated session.

---

### IN-03: `_json_default` now uses `isinstance(o, uuid.UUID)`

**Commit:** `c911192` (folded into CR-02 commit)
**File:** `backend/user/export.py`

Replaced `hasattr(o, "hex")` duck-type with explicit `isinstance(o, uuid.UUID)` check.

---

### WR-01: cancel-deletion uses atomic conditional UPDATE

**Commit:** `8c56cf1`
**File:** `backend/user/router.py`

`cancel_account_deletion` now issues a single `UPDATE … WHERE deletion_requested_at IS NOT NULL RETURNING id` instead of a check-then-write pair. Also writes a symmetric `audit_log` row on cancel for non-repudiation parity with the deletion-request path.

---

### WR-02: asyncpg UUID/str convention standardized

**Commit:** `41cc32a`
**File:** `backend/worker/retention_cron.py`

All `row["id"]` bindings converted to `str(row["id"])` to match `deletion_cron.py` convention. Single pattern across both cron files; documented in inline comment.

---

### WR-03: data-export rate-limit keyed by user_id

**Commit:** `0527dcb`
**File:** `backend/user/router.py`

`@limiter.limit("1/hour")` on `POST /user/data-export` now uses `key_func=get_rate_limit_key` (user_id from `request.state`, falling back to IP). Prevents two users behind the same NAT sharing a budget.

---

### WR-04: `/auth/update-password` rate-limited

**Commit:** `5e61d14`
**File:** `backend/auth/router.py`

Added `@limiter.limit("5/minute")` to `update_password`. Login and reset-password were already throttled; this closes the gap on the password-change endpoint.

---

### WR-05: `exchange_token` verifies JWT signature

**Commit:** `f146c50`
**File:** `backend/auth/router.py`

`jwt.decode` now uses `settings.supabase_jwt_secret` with `algorithms=["HS256"]` instead of `verify_signature: False`. Cookie `max_age` is derived from the token's actual `exp` (remaining seconds, clamped to ≥ 0). Expired tokens are rejected before the cookie is set.

---

### WR-06: INTERVAL parameterized in crons (not f-string)

**Commit:** `9c3cb82`
**Files:** `backend/worker/retention_cron.py`, `backend/worker/deletion_cron.py`

All f-string integer interpolations into SQL replaced with `($N || ' days')::interval` parameterized form. Single pattern across both files.

---

### WR-07: Late-cancel guard before `delete_auth_user`

**Commit:** `786282b`
**File:** `backend/user/deletion.py`

Added a second `FOR UPDATE` re-check of `deletion_requested_at` immediately before `delete_auth_user`. If the user cancelled after R2 and Stripe purged but before auth delete, the executor logs the inconsistency and returns without completing the auth wipe, leaving the DB row for manual review. `PrivacyTab.tsx` copy updated to remove the misleading "cancel any time" phrasing.

---

### WR-08: Export background task surfaces failure status

**Commit:** `d74368e`
**Files:** `backend/user/export.py`, `frontend/src/lib/user.ts`

`build_and_deliver_export` wrapped in try/except; on any exception stamps `last_export_expires_at = now() - 1s` as a sentinel. `GET /user/data-export` now returns `{"status": "failed"}` when `last_export_key IS NULL AND last_export_expires_at <= now`, instead of leaving the UI on `pending` forever.

---

### WR-09: Resend email calls dispatched via `asyncio.to_thread`

**Commit:** `5f97ddc`
**File:** `backend/jobs/notifications.py`

All `resend.Emails.send(params)` calls (5 functions) wrapped in `await asyncio.to_thread(...)`. Prevents the sync Resend SDK from blocking the async event loop during cron email bursts.

---

## Findings Deferred (Info)

| ID | Reason |
|----|--------|
| IN-01 | `_get_supabase()` singleton — micro-optimisation, low urgency |
| IN-02 | Signup upsert refactor — functional today, can land in Phase 11 cleanup |
| IN-04 | AppFooter test year-boundary flake — minor, no production impact |
| IN-05 | Frontend error code branching — UX improvement, not a bug |
| IN-06 | `AuthenticatedLayout` serial fetches — performance, not correctness |

---

## Test Results

```
77 passed (tests/user/ + tests/worker/) — 0 failures
1 pre-existing failure (tests/integration/test_agent_flow.py — Windows socket timeout, unrelated)
```

---

## Migration Status

| Migration | Applied | Verified |
|-----------|---------|---------|
| 20260424000004_audit_log_fk.sql | yes | confdeltype = 'n' (SET NULL) |
| 20260424000005_export_url_cleanup.sql | yes | last_export_key present, last_export_url absent |
