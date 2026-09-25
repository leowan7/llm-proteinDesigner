# Phase 12 Rollout Runbook — Teams & Organizations

**Audience:** the operator running the Phase 12 production cutover.
**Source of truth:** `.planning/phases/12-teams-and-organizations/12-RESEARCH.md` sections §12.1 (migration ordering) and §12.4 (rollback plan).
**Time budget:** 1-2 hours active + 24 hours monitoring before the final drop-column migration.

---

## Read this first: the merge is Step 2

This runbook used to assume the operator applies the Phase 12 migrations by
hand, after the code had been sitting on the trunk for a while. Two pieces of
deploy config make that false:

- `railway.toml` sets `preDeployCommand = supabase db push --db-url $MIGRATION_DB_URL --yes`.
  Merging to `master` therefore applies every pending migration to the
  production database before the new backend takes traffic. **The merge is
  Step 2.** There is no window in which the code is on trunk and the schema is
  not migrated.
- Vercel deploys `master` on push, so the frontend ships in the same merge.
  Every organization surface gates on the backend's own
  `organizations_enabled`, read at runtime from `/health`
  (`frontend/src/lib/features.ts`), so it ships dark and the app renders
  exactly as it does today until Step 5.

Step numbering changed with that: the old Step 6 ("deploy frontend") is gone,
because the frontend deploys in Step 2 and switches on in Step 5 with no
deploy of its own. Steps 7/8/9 of the old draft are now Steps 6/7/8. The
branch is `master`, not `main`.

The drop-column migration `20260606000001_drop_users_stripe_customer_id.sql`
is **deliberately not in the Phase 12 PR**, precisely because merging applies
migrations: it ships as its own PR, and merging that PR is Step 8.

---

## Pre-Flight Checklist

Before merging, confirm every item below:

- [ ] The Phase 12 PR is green on CI and approved
- [ ] The PR's diff contains `20260605000001`, `20260605000002` and `20260605000003`, and **not** `20260606000001_drop_users_stripe_customer_id.sql`
- [ ] Railway has `ORGANIZATIONS_ENABLED` unset or `false` on the backend service (the default in `backend/config.py` is `False`)
- [ ] Stripe test-mode key is available: `sk_test_...` exported as `STRIPE_TEST_SECRET_KEY`
- [ ] Stripe live-mode key is available in Railway env: `STRIPE_SECRET_KEY`
- [ ] Supabase CLI is installed locally and `DATABASE_URL` (pooler URL) is exported, for the verify queries
- [ ] Monitoring dashboards open: Sentry, Stripe Dashboard, UptimeRobot
- [ ] Slack channel `#kendrew-alerts` available for the operator
- [ ] Have read RESEARCH §12.1 (the 9-step table) and §12.4 (rollback)
- [ ] Backups verified: Supabase point-in-time recovery covers the last 7 days

---

## Rollout Steps

Each step has a verify command; do not advance until the verify passes.

### Step 1 — Verify production is flag-off before the merge

The currently deployed backend must not have org routes mounted. The orgs
router only mounts when `settings.organizations_enabled = True`
(`backend/main.py`).

```bash
curl -sS https://app.bindwave.com/health | jq '.organizations_enabled'
# expect: false, or null on a backend that predates Phase 12
```

If this returns `true`, the flag has already been flipped — stop and find out
by whom before merging anything.

Then check the one production data shape that can abort the migration.
`20260605000001_organizations.sql` §2 declares `organizations.stripe_customer_id
TEXT UNIQUE` and §8a copies each user's `public.users.stripe_customer_id` onto
their personal org. That column was created without `UNIQUE`
(`20260319000002_billing_and_results.sql`), so two users sharing one Stripe
customer id would make the copy violate the new constraint, fail the
`preDeployCommand`, and abort the rollout (see the Rollback table).

```sql
-- against production, read-only. Expect zero rows.
SELECT stripe_customer_id, count(*)
  FROM public.users
 WHERE stripe_customer_id IS NOT NULL
 GROUP BY 1 HAVING count(*) > 1;
```

Any rows: stop. Decide which user keeps the customer before merging — this is
unresolved billing identity, not a migration problem.

### Step 2 — Merge the Phase 12 PR (this applies the migrations)

Merging is the deploy. In order, automatically:

1. Railway builds the backend image and runs the predeploy:
   `supabase db push --db-url $MIGRATION_DB_URL --yes`. Three migrations apply:
   - `20260605000001_organizations.sql` — organizations, memberships,
     invitations, RLS helpers, last-owner trigger, personal-org backfill,
     `jobs.organization_id` (NOT NULL), jobs RLS rewrite
   - `20260605000002_jobs_created_by.sql` — `jobs.created_by_user_id` (NOT NULL)
   - `20260605000003_personal_org_tolerance.sql` — `personal_org_for()`, the
     `jobs` BEFORE INSERT trigger that fills both new columns, the two
     Stripe-customer resolvers, the `protect_last_owner` cascade guard (without
     it the GDPR hard delete fails at the database, flag or no flag), the
     BEFORE INSERT/UPDATE guard that keeps a personal org to one member (the
     premise that cascade guard relies on) and the replacement of
     `no_duplicate_pending` with a pending-only unique index
2. A failed predeploy aborts the rollout (`railway.toml` comment), and the old
   replicas keep serving. `supabase db push` applies one file per transaction,
   so a mid-sequence failure leaves the earlier files applied — check which
   before retrying.
3. With `numReplicas = 2` the old and new replicas overlap for the length of
   the deploy. Old replicas insert jobs without the two new columns; migration
   `…000003`'s trigger fills them, and signups that create a `public.users`
   row with no personal org get one lazily on first use. This is covered by
   `backend/tests/integration/test_flag_off_rolling_window.py`.
4. Vercel deploys the frontend from `master`. It renders single-tenant because
   `/health` still reports `organizations_enabled: false`.

Verify the schema, in Supabase Studio (SQL editor):

```sql
SELECT
  (SELECT count(*) FROM public.users)                                AS user_count,
  (SELECT count(*) FROM public.organizations WHERE is_personal)      AS personal_org_count,
  (SELECT count(*) FROM public.organization_memberships m
     JOIN public.organizations o ON o.id = m.organization_id
   WHERE m.role = 'owner' AND o.is_personal)                         AS personal_owner_count,
  (SELECT count(*) FROM public.jobs WHERE organization_id IS NULL)   AS unstamped_jobs,
  (SELECT count(*) FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'users'
     AND column_name = 'stripe_customer_id')                         AS legacy_column_present;
```

**Expected:** `user_count == personal_org_count == personal_owner_count`
(every user has exactly one personal org, owned by themselves),
`unstamped_jobs == 0` (every existing job was attached to its user's personal
org) and `legacy_column_present == 1`. That last one is the check that the
drop-column migration did not ride along in this PR; if it reads 0, stop —
Step 3 and the rolling-deploy fallback both depend on the column.

Then confirm the app still looks like it did:

```bash
curl -sS https://app.bindwave.com/health | jq '.organizations_enabled'
# expect: false

# In the browser, signed in as any existing account:
# - No org switcher in the header
# - No Organization tab in Settings
# - No "Launched by" column in job history
# - Jobs list, job launch, and the billing tab all behave as before
```

Finally, run the **Stripe customer reconciliation** query from Step 7 once. The
rolling deploy in this step is the only window in which it can find anything,
and finding it now is much cheaper than finding it on a customer's invoice.

### Step 3 — Stamp Stripe metadata (test mode first)

`backend/scripts/stamp_stripe_org_metadata.py` copies `organization_id` and
`kendrew_org_name` onto each Stripe customer. Run it against Stripe test mode
as a rehearsal before touching live customers.

Nothing is broken while this is unstamped: with the flag off, customer
resolution goes through `public.org_stripe_customer()`, which falls back to
the deprecated `public.users.stripe_customer_id`. The stamp is what makes the
metadata queryable in the Stripe Dashboard and what Step 8 gates on.

```bash
cd backend
STRIPE_TEST_SECRET_KEY=$STRIPE_TEST_SECRET_KEY \
  python scripts/stamp_stripe_org_metadata.py --test-mode --dry-run \
  | tee /tmp/stamp-test-dryrun-$(date +%F).jsonl

STRIPE_TEST_SECRET_KEY=$STRIPE_TEST_SECRET_KEY \
  python scripts/stamp_stripe_org_metadata.py --test-mode \
  | tee /tmp/stamp-test-live-$(date +%F).jsonl
```

Inspect the JSONL: every row should have `outcome: modified` (first run) or
`outcome: skipped-already-tagged` (re-run). The trailing summary line should
report `counts.failed == 0`.

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

Both must exit code 0. Non-zero exit = mismatches detected; the JSON output
lists up to 25 mismatched rows. **DO NOT advance to Step 5 until both verify
runs are clean.** This is the gate the Step 8 drop-column migration depends
on.

### Step 5 — Flip the feature flag (backend and frontend, one switch)

> **Do not run this step yet.** The flag-off landing is safe to merge and this
> step is not blocked by it, but an independent review of the landed code found
> four defects that only bite once the flag is on. Each was verified against the
> files cited. Fix them, or decide each one is acceptable, before flipping:
>
> 1. **Team jobs are launched in and billed to the launcher's personal org.**
>    `frontend/src/lib/jobs.ts` reaches `/jobs/launch`, `/jobs/`,
>    `/billing/checkout-session` and `/billing/payment-status` with bare
>    `fetch`, nine call sites, none through the `api()` helper that is the only
>    place `X-Org-Id` is attached (asserted by `frontend/src/lib/api.test.ts`,
>    the `api() X-Org-Id header` describe block). With no header,
>    `backend/auth/org_dependencies.py:80-85` resolves the request to
>    `personal_org_for(user)` as `owner`. So a scientist who selects team org T
>    and launches a job has it metered to their own Stripe customer and is sent
>    to Checkout to add a personal card for team work, while `/user/usage` --
>    which does go through `api()` -- shows T with no usage. This is money on
>    the wrong customer and it is the first thing a real team will do.
> 2. **A departed sole owner's email and card stay on the team org.**
>    `backend/billing/router.py:51-57` creates a team org's Stripe customer with
>    the oldest owner's email. The hard delete removes only that person's
>    *personal* customer -- `deletion_cron.py:51-53` resolves
>    `public.user_stripe_customer(u.id)`, whose personal-org branch is
>    constrained to `is_personal`, and `deletion.py:91-94` deletes exactly the id
>    it is handed -- so the team's billing keeps working, which is why this is a
>    privacy defect rather than an outage. Nothing then rewrites the team org's
>    `stripe_customer_id`: `deletion.py` promotes an heir owner but never updates
>    that column, the orgs module only reads it
>    (`backend/organizations/router.py:190,200`), and its only writers are
>    `billing/stripe_client.py:100` and `:127`, both on customer *create*. So
>    after a GDPR erasure the org still bills the deleted person's card under
>    their email, and their email is still on a live Stripe customer.
> 3. **`DELETE /organizations/{id}` destroys every member's jobs.**
>    `backend/organizations/router.py:205` deletes the org row with no job
>    rescue, and `jobs.organization_id` is `ON DELETE CASCADE`
>    (`supabase/migrations/20260605000001_organizations.sql:213`). The only
>    guard is a 409 when the org has a Stripe customer id, so an org that never
>    billed deletes and takes the jobs with it. `backend/user/deletion.py`
>    re-parents other people's jobs before its own org delete; this route does
>    not. Latent only because defect 1 keeps jobs out of team orgs -- fixing 1
>    makes this live.
> 4. **A signed-out invitee never joins.** `AcceptInvitation.tsx:205,217,253`
>    sends them to `/login?invite_token=...&next=...`, and `Login.tsx:53` goes
>    to `/chat` while `SignUp.tsx:70` goes to `/verify-email`; neither page
>    reads either parameter. The E2E spec signs in before opening the link, so
>    it covers only the already-signed-in branch.
>
> Defects 1 and 2 are money and erasure, so they are filed as their own tasks.
> None of the four is reachable while the flag is off: the orgs router is not
> mounted, no frontend surface renders, and every user resolves to their own
> personal org, which is the correct answer for them.

In the Railway dashboard, set `ORGANIZATIONS_ENABLED=true` on the backend
service. Redeploy.

```bash
curl -sS https://app.bindwave.com/health | jq '.organizations_enabled'
# expect: true
```

The backend now mounts the orgs router. `/jobs/*`, `/billing/*` and
`/user/usage` enforce `get_active_org`/`require_role` (header-scoped), and
`/organizations/{org_id}/*` enforces `require_path_role` against the org in the
path. `/invitations/accept` and `/invitations/preview` deliberately enforce
neither: the caller is by definition not yet a member, so they are gated by the
invitation token itself (`backend/organizations/router.py`, the
"root-mounted, no active-org" section).

The frontend needs no deploy. Each page load probes `/health` once
(`frontend/src/lib/features.ts`) and `OrgProvider` exposes the result as
`enabled`; the switcher, the Settings Organization tab, the job-history
"Launched by" column, `/organizations/new` and the invitation-accept page all
read it. A browser with the app already open picks the flag up on its next
load, so hard-refresh before checking:

```bash
# In the browser, signed in as a user with 2+ org memberships:
# - Header shows the org switcher
# - Settings shows the Organization tab
# - Job history shows "Launched by" for non-personal orgs
```

For a single-tenant account (personal org only) the switcher and the Settings
Organization tab both stay hidden, by different tests: the Settings tab needs a
non-personal active org (`frontend/src/pages/SettingsPage.tsx`, `showOrgTab`),
and the switcher needs more than one org to switch between
(`frontend/src/components/org/OrganizationSwitcher.tsx`, `orgs.length <= 1`).
Both also require the flag. Pre-Phase-12 UX is preserved (Plan 12-05 decision).

Because the frontend reads the flag at runtime, **flipping the flag back to
`false` is also the frontend rollback** — see the Rollback table.

### Step 6 — Smoke test the full teams flow

Walk through the happy path manually OR run the Playwright E2E
(`frontend/e2e/organizations.spec.ts`, the `chromium-orgs` project) against a
prod-like environment:

1. Sign in as user A.
2. Verify the personal org is the default active org (switcher shows
   "Personal", or is hidden if that is the only org).
3. Navigate to `/organizations/new`. Create a team org "E2E Acme".
4. Navigate to `/settings?tab=organization` → Invitations sub-tab → invite
   `user-b@example.com` as scientist.
5. Confirm the invite email landed in user B's inbox (Resend Dashboard or test
   inbox).
6. As user B, click the accept URL, complete the accept flow, land in `/jobs`.
7. As user B, launch a small smoke job (any tool, smallest preset).
8. As user A, switch to E2E Acme in the header switcher. Confirm the new job
   appears in `/jobs` with `Launched by: user-b@example.com`.
9. As user A, navigate to `/settings?tab=billing`. Confirm the Stripe portal
   CTA renders (owner).
10. As user B, navigate to `/settings?tab=billing`. Confirm the "Billing is
    managed by your organization owner" copy renders (non-owner gate).
11. As user A, navigate to Members → Transfer ownership to user B
    (self-demote to scientist).
12. As user B, refresh → Billing tab now shows the portal CTA (new owner).
13. Clean up: as user B, delete the org (or leave it in test data).

A new team org has no Stripe customer of its own, and
`public.org_stripe_customer()` deliberately does not fall back to the
creator's personal customer for a non-personal org. So a team org's first GPU
job meters nothing until an owner adds a payment method. That is intended —
the alternative is charging a personal card for team usage.

If any step surfaces an unexpected error, STOP. Do not start the watch with
broken state.

### Step 7 — 24-hour watch

Leave production running for at least 24 hours. Monitor:

- **Sentry:** zero org-related 5xx (filter `route:/organizations/*` and `route:/invitations/*`)
- **Stripe Dashboard:** every meter event since the flag flip lands on a customer whose `metadata.organization_id` is populated
- **GPU spend alerts:** no unbilled completed jobs (cross-reference the RunPod completion handler's logs against Stripe events)
- **UptimeRobot:** /health stays green
- **User feedback:** any report of "I can't see my jobs" or "billing is gone" → investigate immediately
- **Stripe customer reconciliation:** run the query below. It must return zero
  rows.

```sql
-- A personal org whose Stripe customer disagrees with its owner's deprecated
-- public.users.stripe_customer_id. Only the merge's rolling deploy can create
-- this: an old replica that read the legacy column as NULL before the new code
-- wrote it goes on to create its own customer and blind-write it, so the org
-- meters one customer while the card was attached to the other. See
-- backend/billing/stripe_client.py get_or_create_customer.
SELECT o.id            AS organization_id,
       u.id            AS user_id,
       u.email,
       o.stripe_customer_id AS metered_customer,
       u.stripe_customer_id AS legacy_customer
  FROM public.organizations o
  JOIN public.users u ON u.id = o.created_by
 WHERE o.is_personal
   AND o.stripe_customer_id IS NOT NULL
   AND u.stripe_customer_id IS NOT NULL
   AND o.stripe_customer_id <> u.stripe_customer_id;
```

**If it returns a row:** open both customers in the Stripe dashboard. The one
holding the payment method is the real one. Point the org at it
(`UPDATE public.organizations SET stripe_customer_id = '<cus_with_card>',
updated_at = now() WHERE id = '<org_id>'`), set the legacy column to the same
value, and check whether any meter events landed on the loser (they must be
re-sent or credited). Do not proceed to Step 8 with a row outstanding — the
drop-column migration removes the evidence.

**Do NOT proceed to Step 8 if any of the above show issues.** If issues
appear, follow the Rollback table below.

### Step 8 — Merge the drop-column PR

Once 24 hours have elapsed with no incidents, merge the separate PR carrying
`20260606000001_drop_users_stripe_customer_id.sql`. Railway's predeploy
applies it, exactly as in Step 2 — there is no manual `supabase db push`.

Before merging it, confirm no customer id exists only on the legacy column.
The drop PR must copy any stragglers onto their personal org first; if this
query returns rows, that copy has not happened and merging loses those ids:

```sql
SELECT u.id, u.email
FROM public.users u
JOIN public.organizations o ON o.created_by = u.id AND o.is_personal
WHERE u.stripe_customer_id IS NOT NULL
  AND o.stripe_customer_id IS DISTINCT FROM u.stripe_customer_id;
-- expect: 0 rows
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

The drop PR must also delete, in the same PR that drops the column:

- the legacy leg of `public.org_stripe_customer()` and
  `public.user_stripe_customer()`
- the compare-and-set write to `public.users.stripe_customer_id` in
  `backend/billing/stripe_client.py` `get_or_create_customer`, which exists
  only to keep a rolling-deploy old replica on the same customer
- the `test_users_stripe_customer_id_column_still_exists` guard in
  `backend/tests/integration/test_flag_off_rolling_window.py`, which exists to
  fail if the drop lands early

After Step 8, Phase 12 rollout is COMPLETE.

---

## Rollback

| Failure Mode | Detection | Rollback Procedure |
|--------------|-----------|--------------------|
| Predeploy migration fails (Step 2) | Railway deploy shows a failed predeploy; the rollout aborts | Old replicas keep serving. `supabase db push` runs one file per transaction, so check which of the three applied (`SELECT version FROM supabase_migrations.schema_migrations ORDER BY version DESC LIMIT 5`) before re-running. No data loss. |
| Jobs fail to insert during the deploy window (Step 2) | 5xx on launch, or `NotNullViolation` on `jobs.organization_id` / `jobs.created_by_user_id` in Sentry | Means `20260605000003` did not apply while `…000001`/`…000002` did. Apply it immediately (`supabase db push`); it is additive and safe to run alone. |
| Stamp script reports any `outcome: failed` (Step 3 prod) | JSONL row with `outcome: failed` | Inspect the row's `error` field. Common cause: a Stripe customer was manually deleted out-of-band. Fix the DB row (set `stripe_customer_id = NULL` if the customer no longer exists; the org will lazily create a new one), re-run. |
| Verify script exits non-zero (Step 4) | non-zero exit + JSON `mismatch_count > 0` | Re-run the stamp script to fix the rows; if metadata is being manually edited in the Stripe Dashboard, audit who. Do NOT advance to Step 5 until clean. |
| Org routes return 5xx after the flag flip (Step 5) | Sentry alerts, UptimeRobot drops | Set `ORGANIZATIONS_ENABLED=false` in Railway and redeploy. The router unmounts, and the frontend re-reads `/health` on the next page load and renders single-tenant again — no Vercel rollback needed. Then debug and retry Step 5. |
| Org UI broken but the API is fine (Step 5/6) | Manual smoke or user reports | Same single switch: flag off in Railway. Only if the breakage is in a surface that does **not** gate on the flag does a Vercel rollback to the pre-merge deploy become necessary. |
| Stripe meter events landing on the wrong customer (Step 7 watch) | Stripe Dashboard customer view shows wrong meter aggregation | Flag off (as above), which restores personal-org scope for every user. The customer-id move is reversible because the source value is still in `users.stripe_customer_id` until Step 8. Re-run the stamp script with corrected metadata after fixing the bug. |
| Drop-column migration merged early (Step 8 before the watch) | After-the-fact discovery; `test_users_stripe_customer_id_column_still_exists` fails in CI first if it is added to the Phase 12 PR | Forward-only recovery: a new migration re-adds `users.stripe_customer_id` and backfills from `organizations.stripe_customer_id` for each user's personal org. Painful but recoverable. **Prevention: keep that migration in its own PR and respect Step 7 timing.** |

### Decisive Rollback Gate

**Do NOT merge `20260606000001_drop_users_stripe_customer_id.sql` (Step 8)
until at least 24 hours of clean production data with the new code path.**
This is the point of no return for a clean flag-off rollback: while the column
exists, both Stripe resolvers fall back to it, so flipping the flag off is
always safe. After it is dropped, restoring it requires a forward migration
and a backfill — there is no automatic path back.

---

## Post-Rollout

After Step 8 succeeds:

- [ ] Update `.planning/STATE.md`: Phase 12 status → Complete
- [ ] Update `.planning/ROADMAP.md`: Phase 12 → Verified with date
- [ ] Update `.planning/REQUIREMENTS.md`: ORG-01..ORG-08 → Validated
- [ ] Remove `ORGANIZATIONS_ENABLED` from Railway env once a later backend deploy hard-codes the org path (no longer flag-gated) and the frontend stops probing for it. Until then, leave at `true`.
- [ ] Tag the release: `git tag v1.0-phase-12 && git push --tags`
- [ ] Email a Stripe-Dashboard-savvy stakeholder a sample customer page link so they can verify metadata visibility
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
- Deploy config that makes the merge Step 2: `railway.toml`
- Runtime feature probe: `frontend/src/lib/features.ts`, consumed by `frontend/src/components/org/OrganizationContext.tsx`
- Rolling-window coverage: `backend/tests/integration/test_flag_off_rolling_window.py`, `backend/tests/organizations/test_flag_off_single_user.py`
- Stamp script: `backend/scripts/stamp_stripe_org_metadata.py`
- Verify script: `backend/scripts/verify_stripe_org_metadata.py`
- Drop migration (its own PR, Step 8): not on `master` yet. The file is written; it sits in backup commit `c4c5a0d` on `origin/backup/phases-12-13-wip` and lands as `supabase/migrations/20260606000001_drop_users_stripe_customer_id.sql`
- Playwright E2E: `frontend/e2e/organizations.spec.ts`
