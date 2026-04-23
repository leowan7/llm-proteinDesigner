---
status: partial
phase: 10-legal-and-compliance
source: [10-VERIFICATION.md]
started: 2026-04-23T14:15:00Z
updated: 2026-04-23T14:15:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Signup end-to-end with ToS checkbox
expected: New user cannot submit /signup without ticking ToS checkbox; submitting unchecked surfaces "You must accept the Terms of Service and Privacy Policy."; submitting with wrong tos_version is rejected 400 by backend; verification email is sent; after email confirm + login the ReAcceptanceModal does NOT appear (because current TOS_CURRENT_VERSION='2026-04-23' matches what was stored).
result: [pending]

### 2. ReAcceptanceModal appears after ToS version bump
expected: Bump backend TOS_CURRENT_VERSION to a new date, restart backend, log in as existing user → blocking Dialog shows with no close button; only exit is POST /user/accept-tos; after acceptance the modal closes and stays closed on next login.
result: [pending]

### 3. GDPR data export delivery + ZIP content
expected: Click "Export my data" in Settings → Privacy → receive email within ~1 minute with presigned link → download ZIP → unzip → profile.json, sessions.json, session_messages.json, jobs.json, manifest.json all readable; profile.json contains tos_accepted_at, tos_version, data_retention_days, deletion_requested_at, stripe_customer_id columns.
result: [pending]

### 4. Account deletion 30-day grace with cancel
expected: Click "Delete my account" → Dialog opens → type "DELETE MY ACCOUNT" → submit → receive confirmation email with cancel link → pending-deletion yellow banner appears in Settings → Privacy → click "Cancel deletion" → banner disappears, deletion_requested_at = NULL.
result: [pending]

### 5. Account deletion hard-delete after 30 days (BLOCKED on CR-01)
expected: Manually age deletion_requested_at to > 30 days ago → invoke process_pending_deletions → R2 objects gone under users/{id}/ → Stripe customer deleted → Supabase auth.users row gone → public.users row gone via cascade → deletion-completed email arrives.
result: [blocked]
blocker: "CR-01 in 10-REVIEW.md: audit_log.admin_user_id FK lacks ON DELETE CASCADE — cascade will FK-violate, leaving auth.users deleted but public.users orphaned. DO NOT run this verification on real accounts until /gsd-code-review-fix lands the audit_log FK migration."

### 6. Retention cron warning email at T-7 days
expected: Age a completed job via UPDATE public.jobs SET created_at = NOW() - INTERVAL '85 days' for a user with data_retention_days=90 → invoke send_retention_warnings → email arrives with job name and deletion date; retention_warning_sent_at stamped; second invocation is idempotent (no duplicate email).
result: [pending]

### 7. Retention cron hard-delete of job storage
expected: Age same job to created_at = NOW() - INTERVAL '91 days' → invoke execute_retention_deletions → R2 prefix users/{id}/jobs/{job_id}/ is empty; jobs.status flips from 'complete' to 'expired'; jobs.retention_deleted_at stamped; pre-policy exemption verified by leaving one job with created_at < policy_effective_from and confirming it is NOT purged.
result: [pending]

### 8. Cookie consent banner first-visit + re-open
expected: Fresh browser / clear localStorage → load any Kendrew URL → banner appears at bottom naming access_token, refresh_token, csrftoken → click "Got it" → banner disappears → reload page → banner stays hidden → click "Cookie preferences" in footer → banner re-opens.
result: [pending]

### 9. Per-user retention override save + validation
expected: Settings → Privacy → Data retention shows current value (default 90) → change to 60 → shortening-warning text appears → click Save → "Retention updated" confirms → reload → value persists; change to 29 or 366 → Save button disabled; change to 365 → Save works.
result: [pending]

## Summary

total: 9
passed: 0
issues: 0
pending: 8
skipped: 0
blocked: 1

## Gaps
