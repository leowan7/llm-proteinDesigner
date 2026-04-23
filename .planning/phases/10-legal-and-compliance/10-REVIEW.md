---
phase: 10-legal-and-compliance
status: issues_found
depth: standard
files_reviewed: 39
diff_base: f119580
reviewed: 2026-04-23T13:30:00Z
findings:
  critical: 2
  warning: 9
  info: 6
  total: 17
---

# Phase 10: Code Review

**Depth:** standard
**Files reviewed:** 39
**Status:** issues_found
**Severity:** Critical 2 · Warning 9 · Info 6 (total 17)

## Summary

Phase 10 is a well-engineered legal/compliance pass with a strong foundation: atomic conditional UPDATEs guard the deletion double-submit race, `FOR UPDATE` re-check guards the cron-executor race, email-first-then-stamp ordering protects against retention-warning double-sends, and the R2 → Stripe → Auth ordering keeps destructive steps idempotent on retry. Tests are comprehensive. Two real bugs stand out: the hard-delete cascade will almost certainly fail because `audit_log.admin_user_id` lacks `ON DELETE CASCADE` yet a deletion-request row is written against it, and the presigned URL is persisted to the DB while being delivered to the user — both a PII-leak and URL-expiry mismatch concern. Several warnings flag missing auth/rate-limit hardening and an asyncpg UUID/string mismatch in the retention UPDATE path.

---

## Critical

### CR-01: Hard-delete cascade will fail due to FK on audit_log.admin_user_id

**File:** `supabase/migrations/20260409000001_admin.sql:8` (schema) × `backend/user/router.py:407-412` + `backend/auth/admin_client.py:27-39` (interaction)

**Issue:** The `audit_log` table declares `admin_user_id UUID NOT NULL REFERENCES public.users(id)` with **no `ON DELETE CASCADE`**. In `request_account_deletion` (`user/router.py:407-412`) the backend inserts an `audit_log` row using the requesting user's `user_id` as `admin_user_id` on **every** deletion attempt — including successful ones. Thirty days later, `execute_hard_delete` calls `delete_auth_user(user_id)`, which cascades into `public.users`. That cascade will then try to delete a `users` row that is still referenced by at least one `audit_log.admin_user_id` — Postgres will **raise a foreign-key violation and abort the cascade**, leaving the DB in an inconsistent state where `auth.users` is gone (Supabase admin delete succeeded) but `public.users` remains. The docstring in `admin_client.py:30-36` explicitly claims the cascade cleanly removes everything — it will not.

**Fix:** Either change the FK policy to preserve the audit trail with `ON DELETE SET NULL`, or re-architect so the deletion-request audit row uses a different column that tolerates user removal. Standard non-repudiation-preserving pattern (option a):

```sql
ALTER TABLE public.audit_log
    DROP CONSTRAINT audit_log_admin_user_id_fkey,
    ALTER COLUMN admin_user_id DROP NOT NULL;

ALTER TABLE public.audit_log
    ADD CONSTRAINT audit_log_admin_user_id_fkey
    FOREIGN KEY (admin_user_id) REFERENCES public.users(id)
    ON DELETE SET NULL;
```

Update `admin_client.py` docstring to reflect reality. Add a test exercising the full end-to-end cascade with a prior audit_log row (not just the mocked-out deletion path).

---

### CR-02: Presigned export URL is persisted to DB — PII/secret leakage + TTL mismatch

**File:** `backend/user/export.py:140-153` and `backend/user/router.py:358-375`

**Issue:** Two compounding problems:

1. **Presigned URL persistence to DB.** `build_and_deliver_export` stores the presigned R2 GET URL verbatim in `public.users.last_export_url`. A presigned URL is a **bearer credential** — anyone who obtains it (DB backup, log aggregation, support dashboards, internal ops who SELECT from users, a later subtle SSRF) can download the ZIP **without authentication** for 24 hours. GDPR export ZIPs are exactly the payload an attacker wants — full profile + sessions + jobs.
2. **Presign TTL exposure.** `EXPORT_URL_TTL_SECONDS = 24*3600`, and `get_data_export_status` returns the URL to any authenticated session for that user with no re-auth / step-up check. Browser history, bookmarks, and referrers may persist the URL.

**Fix:**
- Do not persist the presigned URL. Persist only `last_export_key` (the S3 object key) and `last_export_expires_at`. Regenerate the presigned URL on each `GET /user/data-export` call, using the authenticated user's session as the trigger. This ensures only the account owner (who must present valid `access_token`) can re-mint a link.
- Redact `last_export_url` from any admin/support reads.
- Shorten user-facing TTL to 1 hour and allow re-request via a button.
- In the email itself, the presigned URL is unavoidable — note in ops runbook that export emails are sensitive.

```python
# user/export.py — store key only, not URL
async with pool.acquire() as conn:
    await conn.execute(
        """UPDATE public.users
           SET last_export_requested_at = now(),
               last_export_key = $2,
               last_export_expires_at = $3,
               updated_at = now()
           WHERE id = $1""",
        user_id, key, expires_at,
    )

# user/router.py — re-presign on each GET
if row["last_export_expires_at"] and row["last_export_expires_at"] > now:
    remaining = (row["last_export_expires_at"] - now).total_seconds()
    presigned = generate_presigned_get_url(row["last_export_key"], expires_in=int(remaining))
    return {"status": "ready", "url": presigned, ...}
```

---

## Warnings

### WR-01: `cancel-deletion` race vs executor not atomic at endpoint level

**File:** `backend/user/router.py:461-485`

**Issue:** `cancel_account_deletion` does a check-then-write pair (SELECT then UPDATE) with no `WHERE deletion_requested_at IS NOT NULL` guard. The executor's FOR UPDATE check handles correctness, but the endpoint returning `{"cancelled": true}` to a user whose row may in fact be mid-delete is misleading.

**Fix:** Atomic conditional UPDATE mirroring `request_account_deletion`:

```python
updated = await conn.fetchrow(
    """UPDATE public.users
       SET deletion_requested_at = NULL, updated_at = now()
       WHERE id = $1 AND deletion_requested_at IS NOT NULL
       RETURNING id""",
    user_id,
)
```

Also write an audit_log row on cancel for symmetry.

---

### WR-02: asyncpg UUID/str mismatch in retention UPDATE

**File:** `backend/worker/retention_cron.py:135-141, 214-223` vs `backend/worker/deletion_cron.py:47-52`

**Issue:** `row["id"]` is a UUID object; the retention cron binds it directly to UPDATE ($1), while `deletion_cron.py` converts to str. Inconsistency across call sites that handle the same value type.

**Fix:** Pick one convention (UUID or str) and use everywhere. Document the choice.

---

### WR-03: `request_data_export` rate-limit key is IP-based, not user-based

**File:** `backend/user/router.py:313-344`

**Issue:** `@limiter.limit("1/hour")` without a key function uses slowapi's default (remote IP). Two users behind the same NAT share a 1/hour budget, producing spurious 429s.

**Fix:**

```python
@router.post("/data-export", status_code=202)
@limiter.limit(
    "1/hour",
    key_func=lambda request: getattr(request.state, "user_id", None) or get_remote_address(request),
)
```

Verify middleware populates `request.state.user_id` from the auth dependency.

---

### WR-04: `update_password` lacks rate limiting

**File:** `backend/auth/router.py:256-272`

**Issue:** `/auth/update-password` reads `access_token` from a cookie and updates the password with no rate limit, while `/auth/login` (5/minute) and `/auth/reset-password` (3/minute) are throttled. Recovery-flow brute-forcing deserves throttling too.

**Fix:** Add `@limiter.limit("5/minute")`. Verify CSRF middleware applies.

---

### WR-05: `exchange_token` accepts unverified JWTs

**File:** `backend/auth/router.py:223-253`

**Issue:** `jwt.decode(..., options={"verify_signature": False}, audience="authenticated")` does a structural parse only. Endpoint sets the cookie unconditionally with hardcoded `max_age=3600`, regardless of the token's actual `exp`. A 5-minute recovery token becomes a 60-minute cookie.

**Fix:**

```python
payload = jwt.decode(
    body.access_token,
    settings.supabase_jwt_secret,
    algorithms=["HS256"],
    audience="authenticated",
)
exp_seconds = max(0, int(payload["exp"] - time.time()))
```

Use `exp_seconds` as cookie `max_age`. Reject expired tokens.

---

### WR-06: f-string SQL with `WARNING_DAYS_BEFORE` and `GRACE_PERIOD_DAYS`

**File:** `backend/worker/retention_cron.py:103-114`, `backend/worker/deletion_cron.py:37-41`

**Issue:** Both crons interpolate Python integers directly into SQL via f-strings. Module constants today, no injection risk — but a copy-paste tomorrow with a user-supplied value would be a SQLi. Same files use `($1 || ' days')::interval` for user-supplied data, so two patterns coexist in the same file.

**Fix:** Use the parameterized interval pattern consistently:

```python
query = """
    SELECT ...
    WHERE j.created_at + (u.data_retention_days || ' days')::interval
        < NOW() + ($2 || ' days')::interval
"""
await conn.fetch(query, effective_from, str(WARNING_DAYS_BEFORE))
```

---

### WR-07: Hard-delete race between R2 deletion and auth delete

**File:** `backend/user/deletion.py:75-103`

**Issue:** The FOR UPDATE guard releases at end of the leading transaction (line 73). Between line 73 and line 103 (`delete_auth_user`), a user could call `/user/cancel-deletion` successfully — the executor continues to R2 → Stripe → auth-delete anyway because the in-memory guard variable doesn't re-check. User gets `{"cancelled": true}` while data is wiped.

**Fix:** Re-check NULL state atomically before `delete_auth_user`:

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        guard_row = await conn.fetchrow(
            "SELECT deletion_requested_at FROM public.users WHERE id = $1 FOR UPDATE",
            user_id,
        )
        if guard_row is None or guard_row["deletion_requested_at"] is None:
            logger.error(
                "Hard-delete: user=%s cancelled mid-execute after R2/Stripe ran. "
                "R2 and Stripe were already purged; leaving DB row for manual review.",
                user_id,
            )
            return
delete_auth_user(user_id)
```

Also: Privacy tab copy says "cancel any time" — not strictly true once cron is running. Update text.

---

### WR-08: Export builder fails silently on DB/R2 errors

**File:** `backend/user/export.py:44-156`

**Issue:** `build_and_deliver_export` runs as `BackgroundTask`. If `s3.put_object`, the DB UPDATE, or `send_export_ready_email` raises, the exception is logged but the user is never notified. `GET /user/data-export` reports `status: "pending"` forever.

**Fix:** Wrap in try/except; on failure stamp `last_export_expires_at = now() - 1s` (URL stays NULL) and have GET return `status: "failed"` when `last_export_requested_at > 1 hour ago AND url is NULL`.

```python
async def build_and_deliver_export(user_id: str, user_email: str) -> None:
    try:
        # existing body
    except Exception as exc:
        logger.error("Export failed for user=%s: %s", user_id, exc)
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.users SET last_export_expires_at = now() - interval '1 second' WHERE id = $1",
                user_id,
            )
        raise
```

---

### WR-09: `_send_email_safely` blocks event loop on sync HTTP call

**File:** `backend/jobs/notifications.py:32-52, 287-305`

**Issue:** `resend.Emails.send(params)` is sync. Called from async functions, it blocks the event loop during the Resend HTTP call. Under cron load this serializes warning sends.

**Fix:** Wrap in `asyncio.to_thread`:

```python
await asyncio.to_thread(resend.Emails.send, params)
```

Or switch to Resend's async client.

---

## Info

### IN-01: `_get_supabase()` creates a new client per call

**File:** `backend/auth/router.py:22-24`

**Issue:** Every auth call instantiates a fresh Supabase client. Module-level singleton avoids re-init cost.

**Fix:** Lazy module-level singleton with `functools.lru_cache`.

---

### IN-02: `retry once after a short sleep` polling hack on signup

**File:** `backend/auth/router.py:126-139`

**Issue:** 200ms `asyncio.sleep` + retry to handle the `auth.users` → `public.users` trigger race. Fragile under DB load; wasted on happy path.

**Fix:** Always upsert:

```python
await conn.execute(
    """INSERT INTO public.users (id, email, tos_accepted_at, tos_version)
       VALUES ($1, $2, now(), $3)
       ON CONFLICT (id) DO UPDATE
           SET tos_accepted_at = EXCLUDED.tos_accepted_at,
               tos_version     = EXCLUDED.tos_version,
               updated_at      = now()""",
    new_user_id, body.email, body.tos_version,
)
```

---

### IN-03: `_json_default` uses fragile `hasattr(o, "hex")` test

**File:** `backend/user/export.py:29-41`

**Fix:** `isinstance(o, uuid.UUID)` explicitly.

---

### IN-04: AppFooter test flakes across year boundary

**File:** `frontend/src/components/layout/AppFooter.test.tsx:50-55`

**Fix:** `vi.setSystemTime(new Date('2026-06-01'))` in `beforeAll`; assert literal year.

---

### IN-05: SignUp branches on error string regex

**File:** `frontend/src/pages/SignUp.tsx:77-79`

**Issue:** `/terms of service/i.test(error.detail)` breaks if backend copy changes.

**Fix:** Backend returns `{detail, code: "tos_version_mismatch"}`; frontend branches on code.

---

### IN-06: AuthenticatedLayout settings fetches serial

**File:** `frontend/src/components/layout/AuthenticatedLayout.tsx:85-132`

**Fix:**

```typescript
const [meRes, settingsRes, sessionsRes] = await Promise.allSettled([
  api("/auth/me"),
  getSettings(),
  listSessions(),
]);
```

---

## Files reviewed (39)

backend: admin_client.py, auth/router.py, config.py, jobs/notifications.py, storage/client.py, user/{deletion,export,router}.py, worker/{deletion_cron,main,retention_cron}.py, plus 7 test files.
frontend: App.tsx, components/{auth/AuthLayout, layout/{AppFooter,AuthenticatedLayout}, legal/{CookieConsentBanner,CookieConsentProvider,PrivacyTab,ReAcceptanceModal}}.tsx, lib/{cookieConsent,legal,user}.ts, main.tsx, pages/{SettingsPage,SignUp}.tsx, plus 4 test files.
supabase: migrations 20260424000001, 20260424000002, 20260424000003.
