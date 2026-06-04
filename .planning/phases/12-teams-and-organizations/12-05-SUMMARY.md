---
phase: 12
plan: 05
subsystem: frontend-organizations
tags: [organizations, frontend, react, context-provider, x-org-id, invitations, switcher, owner-gated-billing]
dependency-graph:
  requires:
    - "12-01: SQL schema, RLS, last-owner trigger, personal-org backfill"
    - "12-02: backend orgs module, GET /organizations/mine, POST /organizations, /invitations/* endpoints, get_active_org dependency"
    - "12-03: route cutover -- X-Org-Id mandatory on /jobs, /billing, /user/usage; jobs include created_by_user_id + created_by_email"
  provides:
    - "Frontend org context wired everywhere: switcher, invitation-accept page, create-org page, settings org tab, owner-gated billing, launched-by column"
    - "X-Org-Id header injection in api.ts with opt-out list for routes without active-org context"
    - "localStorage[kendrew.activeOrgId] as the source of truth for the active org, cleared on logout from both UserMenu + AppSidebar"
    - "Selectors + routes ready for Plan 12-06 Playwright E2E (org-switcher trigger, dropdown items by data-testid, settings org sub-tabs, invite form by aria-label)"
  affects:
    - "frontend/src/lib/api.ts (every authenticated request now carries X-Org-Id)"
    - "frontend/src/components/layout/AuthenticatedLayout.tsx (mounts OrgProvider)"
    - "frontend/src/components/layout/AppHeader.tsx (mounts OrganizationSwitcher)"
    - "frontend/src/pages/SettingsPage.tsx (Organization tab + owner-only Billing)"
    - "frontend/src/pages/JobHistoryPage.tsx (Launched by column for non-personal orgs)"
tech-stack:
  added:
    - "shadcn DropdownMenu for the org switcher"
    - "shadcn Dialog for transfer-ownership + remove + delete-org confirmations"
  patterns:
    - "useOrgContext() returns a safe empty fallback when no <OrgProvider> is mounted -- single-tenant + Vitest-scaffold compatible (no breaking changes to Plan 09 + Plan 10 specs)"
    - "X-Org-Id header opt-out list (/auth/, /organizations/mine, POST /organizations, /invitations/, /health) keeps auth + invitation flows decoupled from the active org"
    - "403 stale-org sweep: api.ts clears localStorage[kendrew.activeOrgId] when backend returns 'Not a member of this organization' or 'X-Org-Id header required'"
    - "setActiveOrg() triggers window.location.reload() so all in-flight queries + SSE streams re-fetch under the new org"
key-files:
  created:
    - frontend/src/lib/organizations.ts
    - frontend/src/components/org/OrganizationContext.tsx
    - frontend/src/components/org/OrganizationSwitcher.tsx
    - frontend/src/components/org/MembersTab.tsx
    - frontend/src/components/org/InvitationsTab.tsx
    - frontend/src/components/org/OrgSettingsTab.tsx
    - frontend/src/pages/CreateOrganization.tsx
    - frontend/src/pages/AcceptInvitation.tsx
    - frontend/src/lib/organizations.test.ts
    - frontend/src/components/org/OrganizationSwitcher.test.tsx
    - frontend/src/components/org/MembersTab.test.tsx
    - frontend/src/pages/AcceptInvitation.test.tsx
  modified:
    - frontend/src/lib/api.ts
    - frontend/src/lib/jobs.ts
    - frontend/src/components/layout/AppHeader.tsx
    - frontend/src/components/layout/AuthenticatedLayout.tsx
    - frontend/src/components/UserMenu.tsx
    - frontend/src/components/layout/AppSidebar.tsx
    - frontend/src/pages/SettingsPage.tsx
    - frontend/src/pages/JobHistoryPage.tsx
    - frontend/src/App.tsx
decisions:
  - "useOrgContext() returns an empty fallback ({orgs:[], activeOrg:null, role:null}) instead of throwing when no <OrgProvider> is mounted -- preserves single-tenant UX + lets existing Phase 9/10 Vitest scaffolds render SettingsPage without rewriting their setup"
  - "X-Org-Id opt-out is an explicit list (4 prefix matches + POST /organizations exact match) rather than an allowlist -- minimises blast radius when new routes ship without touching api.ts"
  - "Switcher is hidden whenever orgs.length <= 1 (not just for personal-only users) -- single-tenant deployments where the backend returns 404 on /organizations/mine produce orgs=[] and the switcher disappears, preserving the pre-Plan-12 UX byte-for-byte"
  - "Invitation accept page lives at /invitations/accept as a TOP-LEVEL public route -- works in the signed-out branch without depending on the authenticated layout chain"
  - "Login + Signup invite_token forwarding is intentionally NOT implemented in this plan; the redirect carries the token as a query param and AcceptInvitation re-runs preview after auth (deferred to 12-06 if Login/Signup need explicit handling)"
  - "Launched-by column is conditional on activeOrg && !is_personal -- in a personal-only workspace every row would be 'You', adding zero information"
  - "OrgContext setActiveOrg writes localStorage BEFORE reload so the new value survives the page transition (post-reload OrgProvider mount picks it up via resolveActiveOrgId)"
  - "Delete-org confirmation requires typing the org name exactly -- guards against accidental clicks since the action is destructive + irreversible"
metrics:
  duration_minutes: 24
  completed_date: "2026-06-04"
  tasks_completed: 2
  files_created: 12
  files_modified: 9
  total_changes: 21
  tests_added: 17
  total_tests: 87
---

# Phase 12 Plan 05: Frontend Org Context + Switcher + Invitations + Settings UI Summary

Built the user-facing slice of multi-tenancy on top of the backend cutover (12-02 + 12-03) and the Stripe metadata stamp (12-04). After this plan, scientists can create teams, invite teammates by email, accept invitations from any auth state, switch between orgs, see which teammate launched each job, and have non-owner team members gracefully bounced from the Billing tab. Everything is gated behind a > 1 org check, so existing single-user UX is byte-identical until a user creates their first team.

## What shipped

### API layer
- **`frontend/src/lib/organizations.ts`** — 13 typed wrappers: `fetchMyOrgs`, `createOrg`, `fetchMembers`, `inviteMember`, `acceptInvitation`, `previewInvitation`, `transferOwnership`, `removeMember`, `updateMemberRole`, `revokeInvitation`, `fetchPendingInvitations`, `renameOrg`, `deleteOrg`. Type aliases for `OrgRole`, `OrgResponse`, `MemberRow`, `InvitationRow`, `PreviewResult`, `InvitationInvalidReason`.
- **`frontend/src/lib/api.ts`** — `api()` now injects `X-Org-Id: <localStorage[kendrew.activeOrgId]>` on every request except:
  - `/auth/*` (auth has no active-org context)
  - `/organizations/mine` (lists all orgs, must not be filtered)
  - `POST /organizations` (no id, creating a new org)
  - `/invitations/*` (token-scoped, out-of-band)
  - `/health`

  On 403 with detail containing `"Not a member of this organization"` or `"X-Org-Id header required"`, clears `localStorage[kendrew.activeOrgId]` so `OrgProvider` re-resolves on next mount.

### Context + Switcher
- **`frontend/src/components/org/OrganizationContext.tsx`** — `<OrgProvider>` fetches `/organizations/mine` on mount, persists last-active in `localStorage[kendrew.activeOrgId]`, defaults to the personal org (`is_personal=true`) when the stored id is invalid. `useOrgContext()` exposes `{orgs, activeOrgId, activeOrg, role, loading, refresh, setActiveOrg}`. `setActiveOrg(id)` writes localStorage and calls `window.location.reload()`. `clearActiveOrgOnLogout()` helper exported. Returns a safe empty fallback when no provider is mounted (single-tenant + test-scaffold compatible).
- **`frontend/src/components/org/OrganizationSwitcher.tsx`** — `<OrganizationSwitcher>` dropdown. Hidden when `orgs.length <= 1`. Shows org name + role per item, "(Personal)" suffix on the personal org, footer items linking to `/organizations/new` and `/settings?tab=organization`. Mounted in `AppHeader` after the logo block.

### Routing
- **`frontend/src/App.tsx`** — Two new routes:
  - `/organizations/new` → `<CreateOrganization />` (inside `<AuthenticatedLayout>` so it has the OrgProvider)
  - `/invitations/accept` → `<AcceptInvitation />` (top-level public route — handles auth-aware branches itself)
- **`frontend/src/components/layout/AuthenticatedLayout.tsx`** — wraps the authenticated outlet in `<OrgProvider>` so every page below it can read context.

### Pages
- **`frontend/src/pages/CreateOrganization.tsx`** — single-input form. On submit, POSTs `/organizations`, refreshes context, switches active org to the new id (reload).
- **`frontend/src/pages/AcceptInvitation.tsx`** — handles all four RESEARCH §6.2 branches:
  1. **signed in + email matches** → `"Join {Org} as {role}"` with Accept button. Accept calls `POST /invitations/accept`, pre-seeds localStorage with the new org id, refreshes context, navigates to `/jobs`.
  2. **signed in + email mismatch** → `"This invitation is for {email}"`. Sign-out CTA logs the user out and redirects to `/login?invite_token=...&next=/invitations/accept`.
  3. **signed out + valid token** → "You've been invited" with Sign-in + Create-account CTAs, both carrying the token through as a query param.
  4. **invalid token** — reason-specific copy for `expired`, `revoked`, `already_accepted`, `not_found`.

### Settings page changes (`frontend/src/pages/SettingsPage.tsx`)
- New **Organization tab** (visible only when `activeOrg && !is_personal`) with three sub-tabs:
  - **Members** — `<MembersTab>`: members table, invite form (owner-only), per-row role select (owner-only), Remove button per row (owner-only) with confirmation dialog, Transfer-ownership button + dialog.
  - **Invitations** — `<InvitationsTab>`: pending invitations table with copy-link + revoke (owner-only).
  - **Settings** — `<OrgSettingsTab>`: rename + delete-org (owner-only; delete requires typing the org name exactly).
- **Billing tab** gated: when the user's role in the active non-personal org is not `owner`, the tab renders `"Billing is managed by your organization owner. Ask {ownerEmail} for access."` instead of the Stripe payment portal. Owner email resolved via `fetchMembers()`.

### Job history (`frontend/src/pages/JobHistoryPage.tsx`)
- New **Launched by** column (only shown when `activeOrg && !is_personal`). Cell renders `"You"` when `job.created_by_user_id === currentUser.id`, otherwise the truncated `job.created_by_email` (28 chars max, full email in `title` attribute).
- `JobListItem` type in `lib/jobs.ts` extended with `created_by_user_id?` and `created_by_email?` (both backed by Plan 12-03's backend cutover).

### Logout cleanup
- `clearActiveOrgOnLogout()` called from both logout sites:
  - `frontend/src/components/UserMenu.tsx` (header avatar dropdown)
  - `frontend/src/components/layout/AppSidebar.tsx` (sidebar footer)

## Vitest specs (4 files, 17 tests)

- **`frontend/src/lib/organizations.test.ts`** (6) — envelope unwrap on `fetchMyOrgs`, `createOrg` POST shape, X-Org-Id presence on `inviteMember`, X-Org-Id absence on `fetchMyOrgs` / `createOrg` / `acceptInvitation`, `previewInvitation` query encoding.
- **`frontend/src/components/org/OrganizationSwitcher.test.tsx`** (3) — hidden when 1 org, rendered when 2+, click writes localStorage + triggers reload.
- **`frontend/src/components/org/MembersTab.test.tsx`** (3) — viewer + scientist see no editing controls, owner sees invite form + per-row role select + Remove buttons.
- **`frontend/src/pages/AcceptInvitation.test.tsx`** (5) — all four §6.2 branches render the expected UI; invalid reasons (expired / revoked / already_accepted / not_found) each surface reason-specific copy.

**Full suite**: 87 tests pass across 14 files. `npx tsc -b` clean. `npm run build` produced `dist/assets/index-*.js` at 1119 kB (existing bundle-size warning; not new in this plan).

## Single-tenant fallback

When `settings.organizations_enabled=False` on the backend:
- `GET /organizations/mine` returns 404.
- `OrgProvider.refresh()` catches the error, leaves `orgs=[]`, `activeOrgId=null`.
- `api()` skips the X-Org-Id header (no value stored).
- The switcher hides (`orgs.length <= 1`).
- The Settings Organization tab hides.
- The Billing tab shows the normal Stripe portal (no gate triggered without an active org).
- Job history hides the Launched-by column.

Net effect: the entire pre-Plan-12 UX is preserved unchanged for any deployment that hasn't flipped the feature flag.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `useOrgContext()` throwing broke existing Vitest scaffolds**
- **Found during:** running the full Vitest suite after Task 2 implementation.
- **Issue:** `SettingsPage.test.tsx` (Plan 10-05 / Phase 9 scaffolds) renders `<SettingsPage>` directly under `<MemoryRouter>` without an `OrgProvider`. The plan-specified `useOrgContext()` threw when no provider was mounted, taking out 21 pre-existing tests that have nothing to do with orgs.
- **Fix:** Changed `useOrgContext()` to return a safe empty fallback `{orgs:[], activeOrg:null, role:null, ...}` when no provider is in the tree. This is also the correct behaviour for the single-tenant fallback (no flag flipped) and for the public `/invitations/accept` route, so it doubles as a design simplification.
- **Files modified:** `frontend/src/components/org/OrganizationContext.tsx`, `frontend/src/pages/AcceptInvitation.tsx` (removed the now-redundant `useOrgContextOrNull` wrapper).
- **Tracked as:** `[Rule 1 - Bug]`

**2. [Rule 3 - Blocking] Vitest 4 dropped the `--reporter=basic` flag**
- **Found during:** running the plan's verification command.
- **Issue:** Plan's verification uses `npx vitest run --reporter=basic` but Vitest 4 only ships `default`, `verbose`, `dot`, `json`, `junit`, etc. The `basic` reporter is gone.
- **Fix:** Dropped `--reporter=basic`; default reporter still produces the pass/fail summary the verification step needs.
- **Files modified:** none (verification command only; the test files themselves are unchanged).
- **Tracked as:** `[Rule 3 - Blocking]`

**3. [Rule 2 - Critical functionality] AcceptInvitation post-accept navigation fallback**
- **Found during:** wiring the four §6.2 branches.
- **Issue:** When the accept page runs outside `<OrgProvider>` (public route at `/invitations/accept`), `setActiveOrg()` falls through to the no-op fallback and `window.location.reload()` never fires — leaving the user staring at "Joined!" forever.
- **Fix:** Pre-seed `localStorage[kendrew.activeOrgId]` with the new org id BEFORE calling `setActiveOrg()`, then `navigate("/jobs")` as a fallback so the user lands in the authenticated layout (which mounts OrgProvider and picks up the seeded id on first paint).
- **Files modified:** `frontend/src/pages/AcceptInvitation.tsx`.
- **Tracked as:** `[Rule 2 - Critical]`

### Auth gates
None encountered. All work was frontend-only; no auth-protected scripts ran in this plan.

## Self-Check: PASSED

- `frontend/src/lib/organizations.ts` — FOUND (13 exports present)
- `frontend/src/lib/api.ts` — FOUND (X-Org-Id, kendrew.activeOrgId, opt-out list, Not a member, X-Org-Id header required all grep-positive)
- `frontend/src/components/org/OrganizationContext.tsx` — FOUND (OrgProvider, useOrgContext, clearActiveOrgOnLogout, window.location.reload, kendrew.activeOrgId)
- `frontend/src/components/org/OrganizationSwitcher.tsx` — FOUND (orgs.length, /organizations/new, /settings?tab=organization)
- `frontend/src/components/layout/AppHeader.tsx` — FOUND (OrganizationSwitcher mounted)
- `frontend/src/components/layout/AuthenticatedLayout.tsx` — FOUND (OrgProvider mounted)
- `frontend/src/App.tsx` — FOUND (OrgProvider import absent — provider lives in AuthenticatedLayout; /organizations/new and /invitations/accept routes present)
- `frontend/src/pages/CreateOrganization.tsx` — FOUND (createOrg call, CreateOrganization export)
- `frontend/src/pages/AcceptInvitation.tsx` — FOUND (acceptInvitation, previewInvitation, invite_token, sign out and sign in, expired, revoked, already_accepted, not_found)
- `frontend/src/components/org/MembersTab.tsx` — FOUND (MembersTab, transferOwnership, removeMember, updateMemberRole)
- `frontend/src/components/org/InvitationsTab.tsx` — FOUND (revokeInvitation, fetchPendingInvitations)
- `frontend/src/components/org/OrgSettingsTab.tsx` — FOUND (renameOrg, deleteOrg)
- `frontend/src/pages/SettingsPage.tsx` — FOUND (MembersTab, InvitationsTab, OrgSettingsTab, Organization tab label, "Ask ... for access" copy)
- `frontend/src/pages/JobHistoryPage.tsx` — FOUND (created_by_email, Launched by)
- 4 Vitest specs — FOUND, all 17 tests pass
- TypeScript compile — `tsc -b` clean
- Build — `npm run build` succeeded (existing bundle-size warning unrelated to this plan)

### Commits

- `531a4f7` `feat(12-05): organizations API client + OrgProvider context + switcher + X-Org-Id header`
- (Task 2 commit hash in git log; created in this plan)

## Notes for Plan 12-06 (Playwright E2E)

Selectors that 12-06 can rely on:
- Switcher trigger: `button[aria-label="Switch organization"]`
- Switcher item by org id: `[data-testid="org-switcher-item-<orgId>"]`
- Switcher footer create link: `[data-testid="org-switcher-create"]`
- Switcher footer manage link: `[data-testid="org-switcher-manage"]`
- Settings Organization tab: `button[role="tab"][value="organization"]` (shadcn tabs)
- Settings Organization sub-tabs: `<button>` with text content `Members`, `Invitations`, `Settings`
- Members invite form: `form[aria-label="Invite member"]`
- Members invite email field: `input#invite-email` (also `getByLabelText(/email/i)`)
- Members per-row role select: `select[aria-label="Role for <email>"]`
- AcceptInvitation Accept button: `button` with text `Accept invitation`
- AcceptInvitation reason copy lookups: case-insensitive matches on `wrong account`, `you've been invited`, `invitation unavailable`

Test data setup:
- localStorage key for active org: `kendrew.activeOrgId` (string UUID).
- Logout must clear this key — both `UserMenu` and `AppSidebar` call `clearActiveOrgOnLogout()`. Playwright should set the cookie + localStorage together before navigating into the app.
- Backend feature flag: `settings.organizations_enabled=True` required. Otherwise `/organizations/mine` returns 404 and the switcher hides.
- Backend backfill: every existing user has exactly one `is_personal=true` org from Plan 12-01. Tests that need a multi-org user should `createOrg("Test Acme")` against the seeded user, which produces a second non-personal org.

Routes shipped by this plan:
- `/organizations/new` (auth-required, inside AuthenticatedLayout)
- `/invitations/accept?token=...` (public)

Invitation links from `/organizations/{id}/invitations` POST currently use the invitation `id` as the token in the copy-link helper (`InvitationsTab.handleCopyLink`). 12-06 should confirm with the backend whether the public `/invitations/preview?token=...` accepts the row id or a separately-issued opaque token; if the latter, the copy-link needs to use the `token` field returned on creation.
