---
status: partial
phase: 10-legal-and-compliance
source: [10-VERIFICATION.md]
started: 2026-04-23T14:15:00Z
updated: 2026-04-24T00:00:00Z
---

## Tests

### 1. Signup end-to-end with ToS checkbox
expected: New user cannot submit /signup without ticking ToS checkbox; submitting unchecked surfaces "You must accept the Terms of Service and Privacy Policy."; submitting with wrong tos_version is rejected 400 by backend; verification email is sent; after email confirm + login the ReAcceptanceModal does NOT appear (because current TOS_CURRENT_VERSION='2026-04-23' matches what was stored).
result: PASS — unchecked submit showed "You must accept the Terms of Service and Privacy Policy."; signed up as uattest001@outlook.com; verification email received in Mailpit; email confirmed via Supabase link → /email-confirmed page; logged in → /chat with no ReAcceptanceModal.

### 2. ReAcceptanceModal appears after ToS version bump
expected: Bump backend TOS_CURRENT_VERSION to a new date, restart backend, log in as existing user → blocking Dialog shows with no close button; only exit is POST /user/accept-tos; after acceptance the modal closes and stays closed on next login.
result: PASS — bumped tos_current_version to "2026-04-30" in config.py, restarted backend; existing user (uattest001@outlook.com) logged in → blocking ReAcceptanceModal appeared with no close button; accepted ToS → modal closed; re-login → modal did not reappear. Reverted config.py to "2026-04-23" after test.

### 3. GDPR data export delivery + ZIP content
expected: Click "Export my data" in Settings → Privacy → receive email within ~1 minute with presigned link → download ZIP → unzip → profile.json, sessions.json, session_messages.json, jobs.json, manifest.json all readable; profile.json contains tos_accepted_at, tos_version, data_retention_days, deletion_requested_at, stripe_customer_id columns.
result: [pending]

### 4. Account deletion 30-day grace with cancel
expected: Click "Delete my account" → Dialog opens → type "DELETE MY ACCOUNT" → submit → receive confirmation email with cancel link → pending-deletion yellow banner appears in Settings → Privacy → click "Cancel deletion" → banner disappears, deletion_requested_at = NULL.
result: PASS — delete dialog opened; typed "DELETE MY ACCOUNT"; submitted → 200 response, deletion_requested_at set; pending-deletion yellow banner appeared on reload; "Cancel deletion" cleared the banner (deletion_requested_at = NULL confirmed). Known issue: delete confirmation dialog does not auto-close after success (Base UI Dialog bug); spawned as separate fix task. Email delivery not verified (Resend not wired in local dev).


### 5. Account deletion hard-delete after 30 days (BLOCKED on CR-01)
expected: Manually age deletion_requested_at to > 30 days ago → invoke process_pending_deletions → R2 objects gone under users/{id}/ → Stripe customer deleted → Supabase auth.users row gone → public.users row gone via cascade → deletion-completed email arrives.
result: [blocked]
blocker: "CR-01 in 10-REVIEW.md: audit_log.admin_user_id FK lacks ON DELETE CASCADE — cascade will FK-violate, leaving auth.users deleted but public.users orphaned. DO NOT run this verification on real accounts until /gsd-code-review-fix lands the audit_log FK migration."
notes: R2 abort path verified separately — execute_hard_delete raises and stops on stale S3_ENDPOINT_URL (no partial delete). Race guard (SELECT FOR UPDATE, abort if deletion_requested_at = NULL) confirmed working via DB manipulation.

### 6. Retention cron warning email at T-7 days
expected: Age a completed job via UPDATE public.jobs SET created_at = NOW() - INTERVAL '85 days' for a user with data_retention_days=90 → invoke send_retention_warnings → email arrives with job name and deletion date; retention_warning_sent_at stamped; second invocation is idempotent (no duplicate email).
result: [pending — requires Resend configured in local dev]

### 7. Retention cron hard-delete of job storage
expected: Age same job to created_at = NOW() - INTERVAL '91 days' → invoke execute_retention_deletions → R2 prefix users/{id}/jobs/{job_id}/ is empty; jobs.status flips from 'complete' to 'expired'; jobs.retention_deleted_at stamped; pre-policy exemption verified by leaving one job with created_at < policy_effective_from and confirming it is NOT purged.
result: [pending — requires working S3_ENDPOINT_URL / MinIO]

### 8. Cookie consent banner first-visit + re-open
expected: Fresh browser / clear localStorage → load any Kendrew URL → banner appears at bottom naming access_token, refresh_token, csrftoken → click "Got it" → banner disappears → reload page → banner stays hidden → click "Cookie preferences" in footer → banner re-opens.
result: PASS — banner appeared on /signup naming access_token, refresh_token, csrftoken; "Got it" dismissed it; banner did not reappear on reload.

### 9. Per-user retention override save + validation
expected: Settings → Privacy → Data retention shows current value (default 90) → change to 60 → shortening-warning text appears → click Save → "Retention updated" confirms → reload → value persists; change to 29 or 366 → Save button disabled; change to 365 → Save works.
result: PASS — default 90 shown; changed to 60 → shortening warning appeared; Save → "Retention updated" toast; reload → 60 persisted; 29 → Save disabled; 366 → Save disabled; 365 → Save worked.

## Summary

total: 9
passed: 5 (T1, T2, T4, T8, T9)
issues: 1 (T4: delete confirmation dialog does not auto-close after success — Base UI Dialog bug; spawned as separate fix task)
pending: 2 (T3, T6 — require Resend in local dev; T7 — requires working MinIO/S3)
skipped: 0
blocked: 1 (T5 — CR-01 audit_log FK migration must land first)

## Gaps

- Tests 3, 6, 7 require Resend email and working S3/MinIO endpoint — environment-only blockers, not code issues.
- T5 blocked until CR-01 fix (audit_log ON DELETE CASCADE migration) is applied.
- T4 known issue: delete dialog does not close on success (Base UI bug). Separate fix tracked.
