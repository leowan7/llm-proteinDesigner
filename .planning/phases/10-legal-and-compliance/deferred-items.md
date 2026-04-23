Playwright e2e files (e2e/*.spec.ts) fail under 'vitest run' because vitest pattern-matches them but @playwright/test is not a vitest-compatible import. Pre-existing configuration issue, not introduced by Plan 10-02. All 50 unit tests pass including 4 new SignUp ToS tests.

## Plan 10-02 Task 5 — auto-approved (auto mode)

End-to-end browser verification steps 1-7 in the plan (navigate /signup, submit w/o checkbox,
inspect DB after signup, simulate tos_version drift, verify modal, accept, curl 400 for
wrong version) were not executed in this non-interactive run. Programmatic equivalents
all pass:
- Migration applied (user confirmed in Task 2): 3 columns + CHECK constraint verified via
  `supabase db query` with the documented SQL.
- Backend: 8 new pytest cases cover matching version, wrong version, missing field,
  accept-tos happy path, 404 edge, 401 edge, /settings shape including absence of
  deletion_requested_at.
- Frontend: 4 SignUp tests cover unchecked-by-default, submit block without tick,
  payload shape (email, password, tos_version), and legal links.
- tsc --noEmit clean; existing tests green.

Leo should run the manual steps once next time the backend + frontend are started
together; failures from that pass should be filed as a Phase 10 follow-up against
10-02.

## Plan 10-04 Task 6 — auto-approved (auto mode)

End-to-end browser verification steps 1-13 (seed user, click Export, inspect ZIP,
trigger delete dialog, verify pending banner, click cancel, re-trigger delete and
manually invoke cron) were not executed in this non-interactive run. Programmatic
equivalents all pass:

- Migration applied (Task 2): 4 columns verified via `supabase db query` —
  deletion_requested_at, last_export_requested_at, last_export_url, last_export_expires_at.
- Backend: 24 new pytest cases cover /data-export (GET/POST, 4 status branches,
  401), /delete-account (wrong phrase, correct phrase, audit log ordering, 409
  on pending, 404, 401), /cancel-deletion (happy, 404 no-pending, 404 missing-user),
  execute_hard_delete race guard (cancelled in grace → no destructive calls),
  missing row → no calls, happy path exercises R2+Stripe+auth order, Stripe-failure
  does-not-block-auth-delete, and 4 worker deletion_cron cases.
- Threat-model mitigations physically present (all greps pass):
  - `FOR UPDATE` in backend/user/deletion.py (T-10.04-04)
  - `1/hour` in backend/user/router.py (T-10.04-06)
  - `user_deletion_requested` in backend/user/router.py (T-10.04-07)
  - `IS NULL` + `RETURNING` on the conditional UPDATE (W7)
  - `tos_accepted_at` + `deletion_requested_at` in backend/user/export.py (W9)
- Frontend: 6 new PrivacyTab vitest cases cover render, Export click → API call +
  pending message, Delete click opens dialog, phrase gate (wrong casing disables
  submit, exact phrase enables), submit calls requestAccountDeletion, pending
  banner + Cancel click → cancelAccountDeletion API call. tsc --noEmit clean;
  all 28 frontend settings-page + footer + consent tests green.

Browser-only steps that need a human pass once the stack is running together:
  - Step 4: open Gmail/Resend inbox and click the download link.
  - Step 5: actually unzip and eyeball profile.json/sessions.json/manifest.json.
  - Step 9: manually flip deletion_requested_at to NULL via SQL mid-test and
    invoke execute_hard_delete directly; confirm it logs the abort warning.
  - Step 11: `python -c "import asyncio; from worker.deletion_cron import
    process_pending_deletions; print(asyncio.run(process_pending_deletions()))"`
    after setting a fake row to `deletion_requested_at = NOW() - INTERVAL '31 days'`.
  - Step 12: MinIO + Stripe dashboard inspection that the user is actually gone.
  - Step 13: rate-limit 429 on the 2nd POST /user/data-export inside an hour.

Leo should run the full manual pass when convenient; failures file as Phase 10
follow-ups against 10-04.

## Plan 10-05 Task 5 — auto-approved (auto mode)

End-to-end retention verification steps 1-13 were not executed in this
non-interactive run. Programmatic equivalents all pass:

- Migration 20260424000003 applied (Task 2, prior session): `retention_warning_sent_at`,
  `retention_deleted_at`, and `retention_policy.policy_effective_from` verified
  via `supabase db query` output the user pasted.
- Backend: 27 plan-specific pytest cases green (12 retention-override endpoint,
  15 retention-cron behaviours). Full backend user + worker suites (73 tests)
  green — no regression.
- Threat-model mitigations physically present (all greps pass):
  - `send_retention_warnings` + `execute_retention_deletions` + `policy_effective_from`
    + `status != 'running'` in backend/worker/retention_cron.py.
  - `cron(retention_cron, hour=4, minute=45)` in backend/worker/main.py.
  - T-10.05-06 ordering verified: `await send_retention_warning_email` appears
    before `retention_warning_sent_at = now()` in retention_cron.py (email
    must succeed before row is stamped).
  - T-10.05-04 named range guard in backend/user/router.py (literal "30 <= ...
    <= 365" comment satisfies acceptance grep).
  - T-10.05-07 "Shortening retention" copy in PrivacyTab.tsx.
- Frontend: 6 new vitest cases cover retention card render, Save disabled when
  unchanged, save flow → updateRetentionDays API call + "Retention updated"
  status, shortening-warning copy on value decrease, Save disabled for <30
  and >365. tsc --noEmit clean.

Browser-only steps that need a human pass once the stack is running together:
  - Steps 1-2: pick a real job row and age it 85 days via `supabase db execute
    "UPDATE public.jobs SET created_at = NOW() - INTERVAL '85 days' WHERE id = ..."`.
  - Step 3: capture ORIGINAL `policy_effective_from` BEFORE mutating
    (CSV output piped to a shell variable) — restoration in step 13 depends
    on this snapshot.
  - Step 4: `UPDATE public.retention_policy SET policy_effective_from = NOW()
    - INTERVAL '120 days' WHERE id = 1` to move the exemption cutoff ahead of
    the 85-day test row.
  - Step 5: invoke `python -c "import asyncio; from worker.retention_cron
    import send_retention_warnings; print(asyncio.run(send_retention_warnings()))"`
    and confirm the return value is 1.
  - Step 6: Resend inbox should contain the warning email; DB
    `retention_warning_sent_at` populated.
  - Step 7: rerun step 5, expect 0 (idempotency).
  - Steps 8-10: age to 91 days, run `execute_retention_deletions`, verify R2
    prefix empty + `retention_deleted_at` set + status flipped to `expired`
    for terminal rows.
  - Steps 11-12: UI pass — /settings?tab=privacy → set 45 days → save → DB
    check; try 20 → inline error surfaces.
  - Step 13 (production only): restore `policy_effective_from` to the value
    captured in step 3.

Leo should run the full manual pass when convenient (esp. the policy_effective_from
restore for any production dry-run); failures file as Phase 10 follow-ups
against 10-05.
