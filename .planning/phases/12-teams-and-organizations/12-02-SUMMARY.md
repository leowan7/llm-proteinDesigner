---
phase: 12-teams-and-organizations
plan: 02
subsystem: backend
tags: [organizations, backend, fastapi, rbac, invitations, x-org-id, resend, feature-flag]

# Dependency graph
requires:
  - phase: 12-teams-and-organizations
    plan: 01
    provides: org_role ENUM + tables + create_organization RPC + protect_last_owner trigger
provides:
  - backend.auth.org_dependencies module (get_active_org + require_role factory)
  - backend.organizations package (router + service + models + notifications)
  - settings.organizations_enabled feature flag (default False)
  - /organizations and /invitations FastAPI routers (mounted behind flag)
  - 8 unit-test files for the orgs module
affects: [12-03-backend-cutover, 12-04-stripe-stamping, 12-05-frontend]

# Tech tracking
tech-stack:
  added: [FastAPI Header dependency, Pydantic StringConstraints, asyncio.to_thread for Resend send, asyncpg.exceptions.RaiseError translation]
  patterns: [SECURITY DEFINER RPC bootstrap via set_config('request.jwt.claims', ...), atomic promote-then-demote transactions, idempotent ON CONFLICT membership inserts, role-gated FastAPI dependency factory]

key-files:
  created:
    - backend/auth/org_dependencies.py
    - backend/organizations/__init__.py
    - backend/organizations/models.py
    - backend/organizations/service.py
    - backend/organizations/router.py
    - backend/organizations/notifications.py
    - backend/tests/organizations/test_active_org_dependency.py
    - backend/tests/organizations/test_create.py
    - backend/tests/organizations/test_invitations.py
    - backend/tests/organizations/test_remove_member.py
    - backend/tests/organizations/test_transfer_ownership.py
    - backend/tests/organizations/test_mine_endpoint.py
    - backend/tests/organizations/test_roles.py
    - backend/tests/organizations/test_permissions.py
  modified:
    - backend/main.py
    - backend/config.py

key-decisions:
  - "get_active_org returns the (org_id, role) tuple AND raises 400 when X-Org-Id is missing; not optional. Routes that don't need org context (GET /organizations/mine, POST /organizations, POST /invitations/accept) use only get_current_user."
  - "require_role(...) is a factory that returns a FastAPI dependency. The inner dependency consumes get_active_org and returns just org_id on success (handlers want the id, not the role tuple)."
  - "POST /organizations sets request.jwt.claims via set_config(name, value, true) (asyncpg cannot bind dotted GUC names with literal SET LOCAL) so the SECURITY DEFINER RPC's auth.uid() resolves correctly even from the service_role pool."
  - "Invitation tokens are stored plaintext per RESEARCH §6.3 (one-use bearer credential; DB compromise has bigger implications). 32-byte URL-safe via secrets.token_urlsafe(32) -> 43-char string."
  - "accept_invitation enforces invitation.email == users.email match in the service layer (RESEARCH §6.2 branch B). The email used is the JWT-verified one from public.users, NOT the request body, so a malicious client cannot bypass the gate."
  - "transfer_ownership promotes target FIRST then demotes self in one transaction (RESEARCH §5.3). The protect_last_owner trigger sees two owners at demote-time so the write succeeds."
  - "Pydantic v2 form: Annotated[str, StringConstraints(strip_whitespace, min_length, max_length)] over Field(strip_whitespace=True). The Field-arg form is deprecated in Pydantic v2 and emits a PydanticDeprecatedSince20 warning."
  - "Feature flag default: settings.organizations_enabled = False. main.py only includes the orgs/invitations routers when True. Existing single-tenant routes (/jobs, /billing, /webhooks) are untouched -- Plan 12-03 owns that cutover."
  - "Tests build isolated FastAPI sub-apps per test rather than mounting on main.app -- avoids depending on global flag state and lets each test override get_current_user + get_active_org cleanly."

patterns-established:
  - "FastAPI dependency factory pattern: require_role(*roles) returns an inner async dep that consumes get_active_org and gates by role; returns just the org_id on success."
  - "asyncpg.exceptions.RaiseError -> HTTPException 400 translation pattern at the route boundary for DB-trigger violations (used for protect_last_owner)."
  - "Per-test FastAPI app construction pattern: fresh FastAPI() + include_router + dependency_overrides on get_current_user / get_active_org, no dependency on main.app's import-time state."
  - "set_config('request.jwt.claims', $1, true) JSON-injection-safe form for planting JWT claims on a connection before calling a SECURITY DEFINER RPC."

requirements-completed: []
requirements-in-progress: [ORG-01, ORG-02, ORG-05, ORG-06]

# Metrics
duration: 13min
completed: 2026-06-04
---

# Phase 12 Plan 02: Wave 1 Backend Orgs Module Summary

**Built the backend organizations module behind a feature flag: get_active_org / require_role auth dependencies, /organizations + /invitations routers, atomic transfer-ownership transaction, idempotent invitation accept flow, and 8 unit-test files. Default-False settings.organizations_enabled gates router mounting so existing single-tenant routes deploy unchanged until Plan 12-03 cuts over.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-06-04T10:26:38Z
- **Completed:** 2026-06-04T10:39:48Z
- **Tasks:** 2
- **Files created:** 14 (6 backend modules + 8 test files)
- **Files modified:** 2 (main.py, config.py)

## Accomplishments

- Authored `backend/auth/org_dependencies.py` (89 lines): `OrgRole = Literal["owner", "scientist", "viewer"]`, `get_active_org` dependency that reads the `X-Org-Id` header and cross-checks `(org_id, user_id) -> role` against `organization_memberships`, and `require_role(*allowed)` factory returning a role-gated inner dependency.
- Authored `backend/organizations/router.py` (~370 lines) exposing 13 endpoints across two routers:
  - `router` (prefix `/organizations`): GET `/mine`, POST `""`, GET/PATCH/DELETE `/{org_id}`, GET `/{org_id}/members`, PATCH/DELETE `/{org_id}/members/{user_id}`, POST `/{org_id}/members/transfer`, GET/POST `/{org_id}/invitations`, DELETE `/{org_id}/invitations/{invite_id}`
  - `invitations_router` (prefix `/invitations`): POST `/accept`, GET `/preview`
- Authored `backend/organizations/service.py` (~180 lines): `accept_invitation` (idempotent in one transaction, email-match enforced, `ON CONFLICT (organization_id, user_id) DO NOTHING` + `WHERE id = $1 AND accepted_at IS NULL`), `transfer_ownership` (promote-then-demote atomic), `generate_invitation_token` (`secrets.token_urlsafe(32)`), `expires_default` (UTC now + 7 days).
- Authored `backend/organizations/notifications.py` (~70 lines): `send_invitation_email` mirroring `backend/jobs/notifications.py` (Resend `Emails.send` wrapped in `asyncio.to_thread`, no-op + INFO log when `resend_api_key` empty, WARNING log + swallow on transient failure).
- Authored `backend/organizations/models.py` (~95 lines): 7 Pydantic models — `CreateOrgRequest`, `OrgResponse`, `ListMineResponse`, `InviteRequest`, `TransferRequest`, `AcceptInviteRequest`, `MemberRoleUpdate`, `UpdateOrgRequest` — with `Literal`-constrained roles, `EmailStr`, and `Annotated[str, StringConstraints(...)]` for trimmed/bounded name fields.
- Wired `settings.organizations_enabled: bool = False` in `backend/config.py` and the conditional `app.include_router(orgs_router); app.include_router(invitations_router)` block in `backend/main.py` so the new code ships dark.
- Authored 8 unit-test files in `backend/tests/organizations/` covering 49 collected tests:
  - `test_active_org_dependency.py` (10 tests) — pure unit tests of `get_active_org` + `require_role` factory with mocked asyncpg pools; covers missing-header 400, non-member 403, owner/scientist/viewer returns, require_role permits/rejects matrix
  - `test_create.py` (5 tests) — POST /organizations 201 with role=owner, empty name 422, name>100 422, the `set_config('request.jwt.claims', ...)` call appears in execute log with the user-id JSON arg, the exact `SELECT public.create_organization($1)` SQL is `fetchval`'d
  - `test_invitations.py` (8 tests) — owner invite happy path with email dispatched, scientist/viewer 403, accept with matching email inserts membership + stamps `accepted_at`, mismatched-email 409, expired/revoked 410, double-click idempotency (`INSERT 0 0` + `UPDATE 0` second call returns same payload)
  - `test_remove_member.py` (4 tests) — owner removes scientist 200, scientist-removes-other 403, self-removal 200 for any role, last-owner `asyncpg.exceptions.RaiseError` -> 400 translation
  - `test_transfer_ownership.py` (4 tests) — happy path verifies UPDATE-target-to-owner ran BEFORE UPDATE-self, transfer-to-self 400, target-not-member 404, new_self_role="owner" Pydantic Literal rejects
  - `test_mine_endpoint.py` (3 tests) — multi-org membership, personal-first ordering, single-personal-org
  - `test_roles.py` (2 tests, env-gated on `SUPABASE_INTEGRATION_DB_URL`) — ENUM round-trip via cast, invalid-value `InvalidTextRepresentationError`
  - `test_permissions.py` (12 parametrized + 1 xfail) — role x endpoint x expected-status matrix; the xfail placeholder is the 12-03 jobs/launch wiring

## Task Commits

1. **Task 1: Backend orgs module + RBAC dependency + feature flag** — `59f770f` (feat: 6 new files, 2 modified, 1003 insertions)
2. **Task 2: 8 unit-test files + Pydantic v2 StringConstraints** — `ae2621f` (test: 8 new files, 1 modified, 1413 insertions, 4 deletions)

**Plan metadata commit:** _(see final `docs(12-02)` commit listed at plan close-out)_

## Files Created/Modified

- `backend/auth/org_dependencies.py` — NEW. `get_active_org` resolves and cross-checks the `X-Org-Id` header. `require_role(*roles)` factory returns an inner role-gated dep.
- `backend/organizations/__init__.py` — NEW. Module marker.
- `backend/organizations/models.py` — NEW. Pydantic v2 request/response schemas with Literal-constrained roles and Annotated StringConstraints for trimmed name bounds.
- `backend/organizations/service.py` — NEW. `accept_invitation` (idempotent, email-match), `transfer_ownership` (atomic promote-then-demote), token + expiry helpers.
- `backend/organizations/router.py` — NEW. Two FastAPI routers exposing 13 endpoints; calls `public.create_organization` RPC with `set_config('request.jwt.claims', ...)` to bind `auth.uid()` correctly.
- `backend/organizations/notifications.py` — NEW. `send_invitation_email` mirroring `jobs/notifications.py` (Resend + asyncio.to_thread + swallow-and-log).
- `backend/main.py` — MODIFIED. Conditional `app.include_router(orgs_router); app.include_router(invitations_router)` behind `settings.organizations_enabled`.
- `backend/config.py` — MODIFIED. Added `organizations_enabled: bool = False`.
- `backend/tests/organizations/test_active_org_dependency.py` — NEW. 10 tests against the dependency callables (no FastAPI app).
- `backend/tests/organizations/test_create.py` — NEW. 5 tests against POST /organizations via isolated FastAPI sub-app.
- `backend/tests/organizations/test_invitations.py` — NEW. 8 tests covering invite creation + accept + idempotency.
- `backend/tests/organizations/test_remove_member.py` — NEW. 4 tests including trigger error translation.
- `backend/tests/organizations/test_transfer_ownership.py` — NEW. 4 tests including atomic ordering proof.
- `backend/tests/organizations/test_mine_endpoint.py` — NEW. 3 tests for memberships listing.
- `backend/tests/organizations/test_roles.py` — NEW. 2 env-gated DB tests for ENUM round-trip.
- `backend/tests/organizations/test_permissions.py` — NEW. 12-row parametrized permission matrix + 1 xfail.

## Decisions Made

- **Per-test FastAPI sub-app pattern over global main.app.** Each test builds a fresh `FastAPI()`, mounts the org router, and overrides `get_current_user` + `get_active_org` via `dependency_overrides`. This sidesteps the feature-flag gate on `main.app` and keeps the tests focused on the route + dep behavior rather than the global mount logic.
- **`set_config('request.jwt.claims', $1, true)` over literal `SET LOCAL request.jwt.claims`.** Same reason Plan 12-01 hit: asyncpg cannot bind parameters into a `SET LOCAL` statement when the GUC name contains a dot. `set_config(name, value, true)` is the documented functionally-equivalent form (third arg `true` = local scope). JSON-safe via parameter binding instead of f-string interpolation.
- **Pydantic v2 `Annotated[str, StringConstraints(...)]` over `Field(strip_whitespace=True)`.** Auto-fixed during Task 2 — the `Field(strip_whitespace=True)` form is deprecated in v2 and emits `PydanticDeprecatedSince20`. The `Annotated` form is canonical Pydantic v2.
- **Patch only `organizations.router.get_db_pool`, not `organizations.service.get_db_pool`.** The service module never imports `get_db_pool`; the router acquires the pool and passes it to `service.transfer_ownership` / `service.accept_invitation` as an argument. Auto-fixed during Task 2 test debugging.
- **DELETE /organizations/{id}/members/{user_id} dual-policy:** owner can remove anyone, non-owners can only remove themselves. The protect_last_owner trigger blocks the owner-leaves-last-owner race at the DB level.
- **/invitations/preview returns `{valid: false, reason: "not_found"}` for unknown tokens** — same response shape as valid-but-stale tokens. Prevents the org-existence enumeration attack from threat T-12-02-05. `organization_name` is only returned when the token resolves to a real row (otherwise enumeration of org names becomes possible).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pydantic v2 deprecation on `Field(strip_whitespace=True)`**
- **Found during:** Task 2 test run
- **Issue:** Initial `CreateOrgRequest.name = Field(strip_whitespace=True, ...)` emitted `PydanticDeprecatedSince20` warnings on every test. Pydantic v2 deprecates `strip_whitespace` as a `Field` keyword argument.
- **Fix:** Replaced with `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]` as a reusable `OrgName` type alias, applied to both `CreateOrgRequest.name` and `UpdateOrgRequest.name`. `Field` import retained for `AcceptInviteRequest.token` (which uses only `min_length`/`max_length`).
- **Files modified:** `backend/organizations/models.py`
- **Verification:** Pytest run now clean of the `Field(strip_whitespace=...)` warning; only `class Config` from `pydantic_settings` remains as a not-actionable-here legacy warning.
- **Committed in:** ae2621f (Task 2 commit)

**2. [Rule 1 - Bug] Wrong patch target in tests**
- **Found during:** Task 2 test run
- **Issue:** Two tests (test_permissions.py, test_invitations.py double-click, test_transfer_ownership.py) tried to patch `organizations.service.get_db_pool` -> `AttributeError: <module> does not have the attribute 'get_db_pool'`. The service module does not import `get_db_pool`; the router acquires the pool and passes it through to service functions.
- **Fix:** Removed the spurious `service.get_db_pool` patches; only `organizations.router.get_db_pool` needs to be patched.
- **Files modified:** `backend/tests/organizations/test_permissions.py`, `test_invitations.py`, `test_transfer_ownership.py`
- **Verification:** 46 passed + 2 skipped + 1 xfailed on the full org test suite (no real-DB available).
- **Committed in:** ae2621f (Task 2 commit)

**3. [Rule 1 - Bug] Wrong expected status on owner-transfer matrix row**
- **Found during:** Task 2 test run
- **Issue:** The matrix row `("owner", "POST", "/organizations/org-1/members/transfer", body, 404)` assumed the target wasn't a member. The `_generic_pool` helper returns `{"role": "scientist"}` for any `organization_memberships` fetchrow, so `transfer_ownership`'s "is target a member" check passes and the full transfer succeeds with 200.
- **Fix:** Updated the expected status to 200 with a code comment noting the pool returns a membership row for the target. The negative case (target-not-member -> 404) is already covered by `test_transfer_to_non_member_returns_404` in `test_transfer_ownership.py`.
- **Files modified:** `backend/tests/organizations/test_permissions.py`
- **Verification:** Full matrix passes.
- **Committed in:** ae2621f (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (all Rule 1 bugs caught and fixed during the same task). No scope changes, no architectural changes, no user input required.

## Issues Encountered

None requiring user input.

## User Setup Required

None. `settings.organizations_enabled` is False by default, so this code does not change any production behavior on deploy. Plan 12-04 flips the flag after Stripe metadata stamping is in place per RESEARCH §12.1 step 5.

`RESEND_API_KEY` and `RESEND_FROM_EMAIL` are already configured on Railway from Phase 11 — `send_invitation_email` is a no-op + INFO log when the key is absent, so local dev without Resend is supported.

## Next Phase Readiness

Wave 1 done. Plans 12-03, 12-04, and 12-05 are unblocked:

- **12-03 (backend cutover)** can now `from auth.org_dependencies import require_role` and gate `/jobs/launch`, `/billing/*`, and `/webhooks` routes by role. The 12-02 permission-matrix test file already has an xfail placeholder for the jobs/launch wiring -- 12-03 should flip that test green.
- **12-04 (Stripe metadata stamping)** can read `public.organizations.stripe_customer_id` via the same DB pool patterns used here.
- **12-05 (frontend)** can call `GET /organizations/mine`, `POST /organizations`, `POST /organizations/{id}/invitations`, `POST /invitations/accept`, and `GET /invitations/preview` from the React app. The X-Org-Id header injection in `frontend/src/lib/api.ts` is the frontend's responsibility.

Threat-register mitigations from the plan's `<threat_model>`:
- T-12-02-01 (X-Org-Id spoofing): mitigated via DB cross-check in `get_active_org`.
- T-12-02-02 (require_role bypass): mitigated via `get_active_org` -> role-set membership-check chain; role comes from DB not JWT.
- T-12-02-03 (invitation email substitution): mitigated via service-layer email-match using JWT-verified email from `public.users`.
- T-12-02-04 (token brute force): mitigated via `secrets.token_urlsafe(32)` (256 bits of entropy).
- T-12-02-05 (preview enumeration): mitigated via uniform `{valid: false, reason: "not_found"}` shape; org_name only returned when token resolves.
- T-12-02-06 (stale token replay): mitigated via idempotent INSERT ON CONFLICT + UPDATE WHERE accepted_at IS NULL.
- T-12-02-08 (transfer race): mitigated via single `conn.transaction()` wrapping the two UPDATEs.
- T-12-02-09 (repudiation): mitigated by the `invited_by FK NOT NULL` constraint laid in 12-01.
- T-12-02-10 (SET LOCAL injection): mitigated via `set_config(name, $1, true)` bound parameter (the user_id comes from `get_current_user`, which is JWT-verified).
- T-12-02-07 (bulk invitation spam): partially mitigated by existing global slowapi rate limits; an explicit per-org `5/minute` limit on `POST /organizations/{id}/invitations` is deferred to 12-03 because the slowapi decorator needs the request: Request injection pattern the orgs router currently doesn't have (matches the rest of Phase 12 -- rate limits are a Plan 12-03 concern).

## Self-Check: PASSED

- `backend/auth/org_dependencies.py` — FOUND
- `backend/organizations/__init__.py` — FOUND
- `backend/organizations/models.py` — FOUND
- `backend/organizations/service.py` — FOUND
- `backend/organizations/router.py` — FOUND
- `backend/organizations/notifications.py` — FOUND
- `backend/main.py` updated (`organizations_enabled` + conditional include_router) — VERIFIED
- `backend/config.py` updated (`organizations_enabled: bool = False`) — VERIFIED
- All 8 test files exist under `backend/tests/organizations/` — VERIFIED
- Commit `59f770f` (Task 1) — FOUND
- Commit `ae2621f` (Task 2) — FOUND
- Verification commands: `python -m py_compile` exits 0; router import smoke test prints `routers OK`; `pytest --collect-only` reports 49 items; no-DB subset 46 passed + 2 skipped + 1 xfailed.

---
*Phase: 12-teams-and-organizations*
*Completed: 2026-06-04*
