# Phase 12 Rollout Runbook — Teams & Organizations

**Audience:** the operator running the Phase 12 production cutover.
**Source of truth:** `.planning/phases/12-teams-and-organizations/12-RESEARCH.md` sections §12.1 (migration ordering) and §12.4 (rollback plan).
**Time budget:** 1-2 hours active + 24 hours monitoring before the final drop-column migration.

---

## Pre-Flight Checklist

Before starting, confirm every item below:

- [ ] All Phase 12 plans 12-01..12-05 have shipped to `master` on `llm-proteinDesigner`
- [ ] Backend is currently deployed to Railway with `ORGANIZATIONS_ENABLED=false`
- [ ] Stripe test-mode key is available: `sk_test_...` exported as `STRIPE_TEST_SECRET_KEY`
- [ ] Stripe live-mode key is available in Railway env: `STRIPE_SECRET_KEY`
- [ ] Supabase CLI is installed locally and `DATABASE_URL` (pooler URL) is exported
- [ ] Monitoring dashboards open: Sentry, Stripe Dashboard, UptimeRobot
- [ ] Slack channel `#kendrew-alerts` available for the operator
- [ ] Have read RESEARCH §12.1 (the 9-step table) and §12.4 (rollback)
- [ ] Backups verified: Supabase point-in-time recovery covers the last 7 days

---

## Rollout Steps

The sequence below mirrors RESEARCH §12.1 exactly. Each step has a verify command; do not advance until the verify passes.

### Step 1 — Verify backend is deployed flag-off

The current production backend must NOT have org routes mounted yet. The orgs router only mounts when `settings.organizations_enabled = True` (Plan 12-02).

```bash
curl -sS https://app.bindwave.com/health | jq '.organizations_enabled'
# expect: false
```

If this returns `true` or the field is missing, STOP. The Phase 12 backend has either already been flipped (skip to Step 5 verification) or never deployed.

### Step 2 — Apply Phase 12 SQL migrations (12-01 + 12-03 schema)

Two migrations land in this step:

- `20260605000001_organizations.sql` (Plan 12-01) — new tables, RLS helpers, last-owner trigger, personal-org backfill, jobs RLS rewrite
- `20260605000002_jobs_created_by.sql` (Plan 12-03) — `jobs.created_by_user_id` column

Railway predeploy runs migrations automatically (Phase 11 D-06). To trigger manually from a developer laptop:

```bash
cd /path/to/llm-proteinDesigner
supabase db push --db-url "$DATABASE_URL" --yes
```

Verify in Supabase Studio (SQL editor) that the new tables exist, the backfill ran, and `jobs.organization_id` is populated for every row:

```sql
SELECT
  (SELECT count(*) FROM public.users)                                AS user_count,
  (SELECT count(*) FROM public.organizations WHERE is_personal)      AS personal_org_count,
  (SELECT count(*) FROM public.organization_memberships
   WHERE role = 'owner')                                             AS owner_membership_count,
  (SELECT count(*) FROM public.jobs WHERE organization_id IS NULL)   AS unstamped_jobs;
```

**Expected:** `user_count == personal_org_count == owner_membership_count` (every user has exactly one personal org owned by themselves) AND `unstamped_jobs == 0` (every existing job was attached to its user's personal org). If any of these fail, DO NOT proceed — investigate.

### Step 3 — Stamp Stripe metadata (test mode first)

Plan 12-04 shipped `backend/scripts/stamp_stripe_org_metadata.py`. Run it against Stripe test mode as a rehearsal before touching live customers.

```bash
cd backend
STRIPE_TEST_SECRET_KEY=$STRIPE_TEST_SECRET_KEY \
  python scripts/stamp_stripe_org_metadata.py --test-mode --dry-run \
  | tee /tmp/stamp-test-dryrun-$(date +%F).jsonl

STRIPE_TEST_SECRET_KEY=$STRIPE_TEST_SECRET_KEY \
  python scripts/stamp_stripe_org_metadata.py --test-mode \
  | tee /tmp/stamp-test-live-$(date +%F).jsonl
```

Inspect the JSONL: every row should have `outcome: modified` (first run) or `outcome: skipped-already-tagged` (re-run). The trailing summary line should report `counts.failed == 0`.

Then prod:

```bash
python scripts/stamp_stripe_org_metadata.py --dry-run \
  | tee /tmp/stamp-prod-dryrun-$(date +%F).jsonl

python scripts/stamp_stripe_org_metadata.py \
  | tee /tmp/stamp-prod-live-$(date +%F).jsonl
```

Review every `outcome: failed` row (there should be zero) before advancing.

### Step 4 — Verify Stripe metadata

```bash
cd backend
python scripts/verify_stripe_org_metadata.py --test-mode \
  | tee /tmp/verify-test-$(date +%F).json

python scripts/verify_stripe_org_metadata.py \
  | tee /tmp/verify-prod-$(date +%F).json
```

Both must exit code 0. Non-zero exit = mismatches detected; the JSON output lists up to 25 mismatched rows. **DO NOT advance to Step 5 until both verify runs are clean.** This is the gate Plan 12-06's drop-column migration depends on.

### Step 5 — Flip the feature flag

In Railway dashboard, set `ORGANIZATIONS_ENABLED=true` on the backend service. Redeploy.

Verify the flag landed:

```bash
curl -sS https://app.bindwave.com/health | jq '.organizations_enabled'
# expect: true
```

The backend now mounts the orgs router and all org-scoped routes (`/jobs/*`, `/billing/*`, `/organizations/*`, `/invitations/*`) enforce `get_active_org` and `require_role`.

### Step 6 — Deploy frontend

Push frontend changes to `main`; Vercel auto-deploys.

Verify a multi-org test account sees the org switcher:

```bash
# In the browser (signed in as a user with 2+ org memberships):
# - Header should show the org switcher button
# - Settings page should show the Organization tab
# - Job history should show the "Launched by" column for non-personal orgs
```

For a single-tenant test account (only personal org), the switcher should be hidden and the Settings Organization tab should be hidden — preserving pre-Phase-12 UX byte-for-byte (Plan 12-05 decision).

### Step 7 — Smoke test the full teams flow

Walk through the happy path manually OR run the Playwright E2E (`frontend/e2e/organizations.spec.ts`) against the prod-like environment:

1. Sign in as user A.
2. Verify the personal org is the default active org (org switcher shows "Personal" or hidden if only one).
3. Navigate to `/organizations/new`. Create a team org "E2E Acme".
4. Navigate to `/settings?tab=organization` → Invitations sub-tab → invite `user-b@example.com` as scientist.
5. Confirm the invite email landed in user B's inbox (Resend Dashboard or test inbox).
6. As user B, click the accept URL, complete the accept flow, land in `/jobs`.
7. As user B, launch a small smoke job (any tool, smallest preset).
8. As user A, switch to E2E Acme in the header switcher. Confirm the new job appears in `/jobs` with `Launched by: user-b@example.com`.
9. As user A, navigate to `/settings?tab=billing`. Confirm the Stripe portal CTA renders (owner).
10. As user B, navigate to `/settings?tab=billing`. Confirm the "Billing is managed by your organization owner" copy renders (non-owner gate).
11. As user A, navigate to Members → Transfer ownership to user B (self-demote to scientist).
12. As user B, refresh → Billing tab now shows the portal CTA (new owner).
13. Clean up: as user B, delete the org (or leave it in test data).

If any step surfaces an unexpected error, STOP. Do not advance to Step 8 with broken state.

### Step 8 — 24-hour watch

Leave production running for at least 24 hours. Monitor:

- **Sentry:** zero org-related 5xx (filter `route:/organizations/*` and `route:/invitations/*`)
- **Stripe Dashboard:** every meter event since the flag flip lands on a customer whose `metadata.organization_id` is populated
- **GPU spend alerts:** no unbilled completed jobs (cross-reference webhook handler logs against Stripe events)
- **UptimeRobot:** /health endpoint stays green
- **User feedback:** any reports of "I can't see my jobs" or "billing is gone" → investigate immediately

**Do NOT proceed to Step 9 if any of the above show issues.** If issues appear, follow the Rollback table below.

### Step 9 — Drop the deprecated column

Once 24 hours have elapsed with no incidents, apply the final migration:

```bash
cd /path/to/llm-proteinDesigner
supabase db push --db-url "$DATABASE_URL" --yes
# picks up 20260606000001_drop_users_stripe_customer_id.sql
```

Verify the column is gone:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users'
ORDER BY ordinal_position;
-- Expected columns (no stripe_customer_id):
--   id, email, created_at, updated_at,
--   tos_*, deletion_*, retention_*, is_admin
-- Stripe customer ID is now exclusively on public.organizations.
```

Confirm the new table comment landed:

```sql
SELECT obj_description('public.users'::regclass, 'pg_class');
-- Should mention "Phase 12: Stripe customer_id moved to public.organizations"
```

After Step 9, Phase 12 rollout is COMPLETE. The deprecated column is gone; the system runs entirely on the org-scoped path.

---

## Rollback

| Failure Mode | Detection | Rollback Procedure |
|--------------|-----------|--------------------|
| Migration fails mid-transaction (Step 2) | `supabase db push` exits non-zero | Postgres rolls back automatically. Investigate logs in Supabase Studio. No data loss; re-run the migration after fixing. |
| Stamp script reports any `outcome: failed` (Step 3 prod) | JSONL row with `outcome: failed` | Inspect the row's `error` field. Common cause: a Stripe customer was manually deleted out-of-band. Fix the DB row (set `stripe_customer_id = NULL` if the customer no longer exists; org will lazily create a new one), re-run. |
| Verify script exits non-zero (Step 4) | non-zero exit + JSON `mismatch_count > 0` | Re-run the stamp script to fix the rows; if metadata is being manually edited in Stripe Dashboard, audit who. Do NOT advance to Step 5 until clean. |
| Backend org code returns 5xx after flag flip (Step 5) | Sentry alerts, UptimeRobot drops | Railway rollback to the previous backend deploy (5 deploys retained per Phase 11). The pre-12-02 backend reads `users.stripe_customer_id` which is STILL present in the DB (deprecated, not dropped) — so the legacy path keeps working. Then debug, redeploy, retry Step 5. |
| Stripe meter events landing on wrong customer (Step 8 watch) | Stripe Dashboard customer view shows wrong meter aggregation | Railway rollback (same as above). The customer-id move is reversible because the source value is still in `users.stripe_customer_id`. Re-run stamp script with corrected metadata after fixing the bug. |
| Frontend org switcher broken (Step 6) | Manual smoke or user reports | Vercel rollback to the previous frontend deploy (independent of backend; the backend keeps serving the orgs API). The pre-12-05 frontend ignores the org-aware response fields and works against the single-tenant code path. |
| Drop-column migration applied prematurely (Step 9 before 24h watch) | After-the-fact discovery | Forward-only recovery: create a new migration that re-adds `users.stripe_customer_id`, backfill from `organizations.stripe_customer_id WHERE organization_memberships.role = 'owner'`. Painful but recoverable. **Prevention: respect Step 8 timing.** |

### Decisive Rollback Gate

**Do NOT apply `20260606000001_drop_users_stripe_customer_id.sql` (Step 9) until at least 24 hours of clean production data with the new code path.** This is the point of no return for clean Railway rollback. After this migration runs, restoring `users.stripe_customer_id` requires a forward migration and a backfill — there is no automatic path back.

---

## Post-Rollout

After Step 9 succeeds:

- [ ] Update `.planning/STATE.md`: Phase 12 status → Complete
- [ ] Update `.planning/ROADMAP.md`: Phase 12 → Verified with date
- [ ] Update `.planning/REQUIREMENTS.md`: ORG-01..ORG-08 → Validated
- [ ] Remove `ORGANIZATIONS_ENABLED` from Railway env once the next backend deploy hard-codes the org path (no longer flag-gated). Until then, leave at `true`.
- [ ] Tag the release: `git tag v1.0-phase-12 && git push --tags`
- [ ] Email Stripe-Dashboard-savvy stakeholder a sample customer page link so they can verify metadata visibility
- [ ] Archive `/tmp/stamp-*.jsonl` and `/tmp/verify-*.json` artifacts to the team drive for compliance

---

## Reference

- Plan 12-01 (DB foundation): `.planning/phases/12-teams-and-organizations/12-01-PLAN.md`
- Plan 12-02 (backend orgs module): `.planning/phases/12-teams-and-organizations/12-02-PLAN.md`
- Plan 12-03 (backend cutover): `.planning/phases/12-teams-and-organizations/12-03-PLAN.md`
- Plan 12-04 (Stripe stamp scripts): `.planning/phases/12-teams-and-organizations/12-04-PLAN.md`
- Plan 12-05 (frontend org context + switcher + invites): `.planning/phases/12-teams-and-organizations/12-05-PLAN.md`
- Plan 12-06 (this runbook + drop migration + E2E): `.planning/phases/12-teams-and-organizations/12-06-PLAN.md`
- Research: `.planning/phases/12-teams-and-organizations/12-RESEARCH.md` §12.1 (ordering) + §12.4 (rollback)
- Stamp script: `backend/scripts/stamp_stripe_org_metadata.py`
- Verify script: `backend/scripts/verify_stripe_org_metadata.py`
- Drop migration: `supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql`
- Playwright E2E: `frontend/e2e/organizations.spec.ts`
