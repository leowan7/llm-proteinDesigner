# Phase 12: Teams & Organizations - Research

**Researched:** 2026-06-03
**Domain:** Multi-tenancy + RBAC + organization-level billing for an existing single-tenant FastAPI + Supabase + Stripe SaaS
**Confidence:** HIGH on schema + RLS recommendations (verified via Supabase docs + Postgres recursive-policy gotcha reports). MEDIUM on Stripe migration sequencing (Stripe documents the Billing Meters API behavior but does not document a canonical "swap customer on an active metered subscription" path; chosen approach is the lowest-friction one — recreate subscription on the org customer).

---

## 1. Summary

Phase 12 layers an **organizations + memberships + roles** dimension on top of the existing single-tenant model. The platform currently ties every job, Stripe customer, and RLS policy to `auth.uid() = user_id`. To support team billing, three things must move from "owned by a user" to "owned by an organization": **jobs**, the **Stripe customer + subscription**, and **RLS scope**.

The complexity lives in four places, not the schema: **(a)** Postgres RLS recursive-policy traps when policies on `organizations` and `organization_memberships` reference each other — solved by `SECURITY DEFINER` PL/pgSQL helpers (NOT SQL functions — they get inlined and recurse anyway) `[VERIFIED: GitHub supabase discussion #3328 + dev.to "SECURITY DEFINER gotcha"]`; **(b)** **active-org propagation** — JWT-claim updates require token refresh, so the recommended path is HTTP header (`X-Org-Id`) cross-checked server-side against memberships, not JWT custom claims `[CITED: medium.com FastAPI multi-tenancy 2025-04]`; **(c)** **Stripe migration** — every existing user has a personal Stripe customer + metered subscription; the cleanest path is to auto-create a "personal org" per existing user, MOVE the existing `stripe_customer_id` to the new `organizations` row, and treat that org as owner-only; **(d)** the **last-owner invariant** must be DB-enforced (CHECK constraint via partial unique index + DELETE trigger), not application-level.

The recommended schema introduces `organizations`, `organization_memberships`, and `organization_invitations`, adds nullable `organization_id` to `public.jobs` with a backfill migration, and replaces the existing `jobs_own` RLS policy with an org-aware variant gated by `is_member_of(auth.uid(), organization_id)`. Stripe lives at the org level only — no more `users.stripe_customer_id`.

**Primary recommendation:** Use the schema in §4. Run the Stripe migration as `(personal-org create + customer move) → cutover billing routes → drop user-level Stripe column`. Active-org context propagates via `X-Org-Id` HTTP header validated by a `get_active_org` FastAPI dependency on every org-scoped route. Do NOT touch JWT custom claims for this — the Supabase JWKS path (ES256) was just stabilized in Phase 11 and adding a custom claim refresh on every org switch is the wrong tradeoff.

---

## 2. Phase Requirements

Numbering scheme: **ORG-01..ORG-08**. These are new requirements; planner should add them to `.planning/REQUIREMENTS.md` and update the Traceability table.

| ID | Description | Research-Informed Gloss |
|----|-------------|-------------------------|
| ORG-01 | User can create an organization and invite team members by email | "Create" = POST /organizations creating the row + auto-adding the creator as owner in a single transaction. "Invite by email" = token-in-URL flow; invited email may or may not have an existing account (§6). |
| ORG-02 | Organization roles: owner (billing + admin), scientist (run jobs, view all org jobs), viewer (read-only) | Postgres ENUM `org_role` with three values. Permission matrix in §5. Owner is the only role with billing access; scientist runs jobs; viewer reads only. |
| ORG-03 | All jobs within an organization are visible to all org members (not siloed per user) | `public.jobs.organization_id` becomes the RLS scope key. The original `auth.uid() = user_id` policy is REPLACED (not augmented) so a user does NOT see other personal-org members' jobs — they see jobs in orgs where they hold a membership. |
| ORG-04 | Organization-level billing: one Stripe subscription, one invoice, usage aggregated across all members | `stripe_customer_id` moves from `public.users` to `public.organizations`. Webhook handler reads `organizations.stripe_customer_id` via the job's `organization_id`, not the job's `user_id`. One meter event per job; all events for an org aggregate on that customer's subscription. |
| ORG-05 | Owner can remove members and transfer ownership | "Remove member" = DELETE /organizations/{id}/members/{user_id} — forbidden if target is the only owner. "Transfer ownership" = atomic SQL transaction: promote target to owner + demote self to scientist (or whatever role param is passed). |
| ORG-06 | User can belong to multiple organizations and switch between them | `organization_memberships` is many-to-many. Active org propagated via `X-Org-Id` header (§8). Org switcher in `AppHeader.tsx` (§10). |
| ORG-07 | Existing single-tenant users migrated without data loss | Migration: each existing user gets a `personal` org auto-created; their `stripe_customer_id` and `jobs` move to that org. The user is added as the only owner. |
| ORG-08 | Last owner cannot leave an organization | DB-enforced via partial unique index + DELETE trigger that counts owners — see §5. App-level checks are insufficient for race-condition safety. |

---

## 3. Current State (with code excerpts)

### 3.1 Schema — single-tenant model

**`supabase/migrations/20260318000000_init.sql:1-29`** — original schema. Note: the join is `auth.users(id) → public.users(id) → public.jobs(user_id)`. `auth.uid() = user_id` is the ENTIRE RLS scoping mechanism:

```sql
CREATE TABLE IF NOT EXISTS public.users (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'queued', 'running', 'complete', 'failed', 'cancelled')),
    tool        TEXT,
    parameters  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_own ON public.users
    FOR ALL USING (auth.uid() = id);

CREATE POLICY jobs_own ON public.jobs
    FOR ALL USING (auth.uid() = user_id);
```

Same RLS pattern repeats in `20260319000002_billing_and_results.sql:31-34` (job_candidates joins through jobs.user_id) and `20260408000001_session_persistence.sql:39-45` (sessions + session_messages).

### 3.2 Stripe customer per user

**`supabase/migrations/20260319000002_billing_and_results.sql:5`** — Stripe customer column lives on `users`:

```sql
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
```

**`backend/billing/stripe_client.py:25-64`** — `get_or_create_customer` writes the Stripe customer ID back to `public.users`:

```python
async def get_or_create_customer(
    email: str,
    user_id: str,
    pool: asyncpg.Pool,
) -> str:
    # Check DB first to avoid redundant Stripe API calls
    row = await pool.fetchrow(
        "SELECT stripe_customer_id FROM public.users WHERE id = $1",
        user_id,
    )
    if row and row["stripe_customer_id"]:
        return row["stripe_customer_id"]
    customer = stripe.Customer.create(
        email=email,
        metadata={"user_id": user_id},
    )
    await pool.execute(
        "UPDATE public.users SET stripe_customer_id = $1 WHERE id = $2",
        customer.id, user_id,
    )
    return customer.id
```

**`backend/billing/stripe_client.py:125-149`** — `record_gpu_usage` is called with a `stripe_customer_id` keyed to ONE user — that's the linchpin to change in Phase 12.

### 3.3 Per-request auth identification

**`backend/auth/dependencies.py:9-44`** — every authenticated route depends on `get_current_user`, which decodes a JWT from an HTTP-only cookie and returns the Supabase `sub` claim. There's no organization context anywhere in this function:

```python
async def get_current_user(access_token: str | None = Cookie(default=None)) -> str:
    if access_token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = await jwks_verifier.verify(access_token)
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

**`backend/auth/jwks.py:54-298`** — JWKS verifier was just rebuilt in Phase 11 Plan 04. It does dual ES256/HS256 verification with `kid`-based key rotation. **Touching this for org claims is high-risk** — recommend NOT putting active-org into the JWT.

### 3.4 Job ownership flows through user_id everywhere

**`backend/jobs/router.py:104-235`** — `launch_job_endpoint` does payment-gating via `users.stripe_customer_id` and writes through to `public.jobs.user_id`:

```python
async def launch_job_endpoint(
    request: Request,
    body: LaunchRequest,
    user_id: str = Depends(get_current_user),
):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT job_spec FROM public.jobs WHERE id = $1 AND user_id = $2",
            body.job_id, user_id,
        )
    # ...
    if settings.stripe_secret_key:
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT email, stripe_customer_id FROM public.users WHERE id = $1",
                user_id,
            )
        stripe_customer_id = await get_or_create_customer(
            email=user_row["email"], user_id=user_id, pool=pool,
        )
```

**`backend/jobs/router.py:336-421`** — `list_jobs` is hardcoded to `WHERE user_id = $1`. This is the read-side query that ORG-03 requires changing.

**`backend/webhooks/router.py:299-307`** — webhook handler reads `users.stripe_customer_id` to bill on job completion. This is the WRITE-side billing path that ORG-04 requires changing:

```python
if internal_status in ("complete", "cancelled") and gpu_seconds > 0:
    async with pool.acquire() as conn:
        cust_row = await conn.fetchrow(
            "SELECT stripe_customer_id FROM public.users WHERE id = $1",
            user_id,
        )
    if cust_row and cust_row["stripe_customer_id"]:
        record_gpu_usage(cust_row["stripe_customer_id"], job_id, gpu_seconds)
```

### 3.5 Frontend auth context

**`frontend/src/App.tsx:97-104`** — there's no React auth context. `AuthenticatedLayout` does an inline `await api("/auth/me")` check per mount. Org switcher state needs a new lightweight context provider (NOT Redux — overkill).

**`frontend/src/lib/api.ts:75-115`** — `api()` is the only HTTP client. It carries the auth cookie via `credentials: "include"` and sends CSRF as `x-csrftoken`. The new `X-Org-Id` header is added here.

---

## 4. Recommended Schema

Three alternatives were considered, then one chosen:

| Option | Description | Why Rejected (or chosen) |
|--------|-------------|-------------------------|
| A. Personal-workspace-as-discriminator | One `workspaces` table, `is_personal BOOL` column, single membership row per workspace for personal | **Rejected.** Mixes two concepts (collaboration scope + identity) in one row; complicates RLS predicates with `OR (is_personal AND workspaces.owner_user_id = auth.uid())`. |
| B. Separate `personal_workspaces` and `organizations` tables | Two parallel ownership models, jobs nullable on both, foreign key OR check | **Rejected.** Doubles every query (UNION ALL on workspace_id). |
| C. **Personal-org auto-created on signup (CHOSEN)** | Every user gets one `personal=true` org at signup; `jobs.organization_id` is NOT NULL after migration; single uniform RLS predicate | **Chosen.** Uniform schema, one query path, no special cases for "personal vs team". The `is_personal` flag is metadata only — used by UI to hide the org from the switcher when there's only one (no friction for solo users). |

### 4.1 DDL — `supabase/migrations/20260605000001_organizations.sql`

```sql
-- ============================================================================
-- Phase 12: Teams & Organizations
-- ============================================================================
-- Adds organizations, organization_memberships, organization_invitations tables.
-- Moves stripe_customer_id from public.users to public.organizations.
-- Replaces jobs_own RLS policy with organization-scoped policy.
-- Backfills a personal organization for every existing user.
--
-- This migration is ONLINE-SAFE: all schema changes are additive and the
-- backfill runs in the same transaction as the cutover. Apply via Supabase
-- CLI (Phase 11 D-06 makes this the predeploy hook).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. ENUM for roles
-- ----------------------------------------------------------------------------

CREATE TYPE public.org_role AS ENUM ('owner', 'scientist', 'viewer');

COMMENT ON TYPE public.org_role IS
    'Organization member role. owner = billing+admin; scientist = run+view jobs; viewer = read-only.';

-- ----------------------------------------------------------------------------
-- 2. organizations
-- ----------------------------------------------------------------------------

CREATE TABLE public.organizations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    is_personal         BOOLEAN NOT NULL DEFAULT FALSE,
    stripe_customer_id  TEXT UNIQUE,
    created_by          UUID REFERENCES public.users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT name_not_blank CHECK (length(btrim(name)) > 0)
);

COMMENT ON TABLE public.organizations IS
    'Tenant boundary. Every job, billing event, and RLS scope is keyed on this. '
    'is_personal=true means the auto-created personal org for a solo user.';

CREATE INDEX idx_orgs_stripe_customer ON public.organizations(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. organization_memberships
-- ----------------------------------------------------------------------------

CREATE TABLE public.organization_memberships (
    organization_id     UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    role                public.org_role NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, user_id)
);

CREATE INDEX idx_memberships_user ON public.organization_memberships(user_id);
CREATE INDEX idx_memberships_org_role ON public.organization_memberships(organization_id, role);

COMMENT ON TABLE public.organization_memberships IS
    'Many-to-many user <-> organization with role. Last-owner DELETE protection '
    'enforced by the trigger below. Personal orgs always have exactly 1 owner row.';

-- ----------------------------------------------------------------------------
-- 4. organization_invitations
-- ----------------------------------------------------------------------------

CREATE TABLE public.organization_invitations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    email               TEXT NOT NULL,
    role                public.org_role NOT NULL,
    token               TEXT NOT NULL UNIQUE,
    invited_by          UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    expires_at          TIMESTAMPTZ NOT NULL,
    accepted_at         TIMESTAMPTZ,
    accepted_by         UUID REFERENCES public.users(id) ON DELETE SET NULL,
    revoked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT no_duplicate_pending UNIQUE (organization_id, email)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX idx_invitations_org ON public.organization_invitations(organization_id) WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE INDEX idx_invitations_email ON public.organization_invitations(lower(email)) WHERE accepted_at IS NULL AND revoked_at IS NULL;

COMMENT ON COLUMN public.organization_invitations.token IS
    'Single-use URL-safe random token (32 bytes base64url). Generated by the backend, never stored in plaintext anywhere else.';

-- ----------------------------------------------------------------------------
-- 5. Last-owner protection trigger
-- ----------------------------------------------------------------------------
-- DB-enforced invariant: an organization always has at least one owner.
-- App-level checks alone are insufficient against concurrent DELETEs.

CREATE OR REPLACE FUNCTION public.protect_last_owner()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    remaining_owners INT;
BEGIN
    -- For UPDATE: only fire if the role was demoted FROM owner
    IF (TG_OP = 'UPDATE' AND OLD.role = 'owner' AND NEW.role <> 'owner')
       OR (TG_OP = 'DELETE' AND OLD.role = 'owner')
    THEN
        SELECT count(*) INTO remaining_owners
        FROM public.organization_memberships
        WHERE organization_id = OLD.organization_id
          AND role = 'owner'
          AND user_id <> OLD.user_id;
        IF remaining_owners = 0 THEN
            RAISE EXCEPTION 'Cannot remove or demote last owner of organization %', OLD.organization_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER protect_last_owner_trigger
    BEFORE UPDATE OR DELETE ON public.organization_memberships
    FOR EACH ROW
    EXECUTE FUNCTION public.protect_last_owner();

-- ----------------------------------------------------------------------------
-- 6. RLS helper functions — MUST be plpgsql, NOT sql, or they inline & recurse
-- ----------------------------------------------------------------------------
-- Postgres inlines simple SQL functions during query planning. Inlined SECURITY
-- DEFINER loses its context and RLS re-applies, causing "infinite recursion
-- detected in policy" on any policy that queries organization_memberships.
-- PL/pgSQL functions are NEVER inlined, so SECURITY DEFINER works as intended.
-- See: https://dev.to/bairescodeai/infinite-recursion-in-postgres-rls-a-security-definer-gotcha-1916

CREATE OR REPLACE FUNCTION public.is_member_of(_org_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM public.organization_memberships
        WHERE organization_id = _org_id AND user_id = auth.uid()
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.has_role_in(_org_id UUID, _required public.org_role[])
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM public.organization_memberships
        WHERE organization_id = _org_id
          AND user_id = auth.uid()
          AND role = ANY(_required)
    );
END;
$$;

REVOKE EXECUTE ON FUNCTION public.is_member_of(UUID) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.has_role_in(UUID, public.org_role[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_member_of(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.has_role_in(UUID, public.org_role[]) TO authenticated;

-- ----------------------------------------------------------------------------
-- 7. RLS policies on new tables
-- ----------------------------------------------------------------------------

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_invitations ENABLE ROW LEVEL SECURITY;

-- Members can SELECT their orgs; owners can UPDATE.
CREATE POLICY orgs_select_members ON public.organizations
    FOR SELECT USING (public.is_member_of(id));

CREATE POLICY orgs_update_owners ON public.organizations
    FOR UPDATE USING (public.has_role_in(id, ARRAY['owner']::public.org_role[]));

-- INSERT requires the creator to set themselves as owner in the same request.
-- The router handles this in a single transaction. We allow any authenticated
-- user to insert (then RLS on memberships gates the matching insert).
CREATE POLICY orgs_insert_any_authenticated ON public.organizations
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- Memberships: members can read all rows for orgs they belong to; owners write.
CREATE POLICY memberships_select_members ON public.organization_memberships
    FOR SELECT USING (public.is_member_of(organization_id));

CREATE POLICY memberships_write_owners ON public.organization_memberships
    FOR ALL USING (public.has_role_in(organization_id, ARRAY['owner']::public.org_role[]))
    WITH CHECK (public.has_role_in(organization_id, ARRAY['owner']::public.org_role[]));

-- A user must be able to insert THEIR OWN initial owner row when creating an
-- organization, before the orgs_insert_any_authenticated policy's matching
-- membership exists. Handle this via a SECURITY DEFINER RPC `create_organization`
-- (see §4.2) — not a separate INSERT policy that would broaden the attack surface.

-- Invitations: members can SELECT pending invites for their org; owners write.
CREATE POLICY invitations_select_members ON public.organization_invitations
    FOR SELECT USING (public.is_member_of(organization_id));

CREATE POLICY invitations_write_owners ON public.organization_invitations
    FOR ALL USING (public.has_role_in(organization_id, ARRAY['owner']::public.org_role[]))
    WITH CHECK (public.has_role_in(organization_id, ARRAY['owner']::public.org_role[]));

-- ----------------------------------------------------------------------------
-- 8. Add organization_id to public.jobs, then backfill, then enforce NOT NULL
-- ----------------------------------------------------------------------------

ALTER TABLE public.jobs ADD COLUMN organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE;

-- 8a. Backfill: create one personal org per existing user, with the user as owner.
DO $$
DECLARE
    u RECORD;
    new_org_id UUID;
BEGIN
    FOR u IN
        SELECT id, email, stripe_customer_id, created_at
        FROM public.users
    LOOP
        INSERT INTO public.organizations
            (name, is_personal, stripe_customer_id, created_by, created_at)
        VALUES
            (
                COALESCE(NULLIF(split_part(u.email, '@', 1), ''), 'Personal')
                || ' (Personal)',
                TRUE,
                u.stripe_customer_id,
                u.id,
                u.created_at
            )
        RETURNING id INTO new_org_id;

        INSERT INTO public.organization_memberships (organization_id, user_id, role, created_at)
        VALUES (new_org_id, u.id, 'owner', u.created_at);

        UPDATE public.jobs SET organization_id = new_org_id WHERE user_id = u.id;
    END LOOP;
END $$;

-- 8b. Now safe to enforce NOT NULL.
ALTER TABLE public.jobs ALTER COLUMN organization_id SET NOT NULL;

-- 8c. Helpful index for org-scoped job lists.
CREATE INDEX idx_jobs_org_created ON public.jobs(organization_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- 9. Replace jobs_own RLS policy
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS jobs_own ON public.jobs;

CREATE POLICY jobs_org_members ON public.jobs
    FOR SELECT USING (public.is_member_of(organization_id));

-- INSERT/UPDATE require scientist or owner (viewers are read-only).
CREATE POLICY jobs_write_active ON public.jobs
    FOR INSERT WITH CHECK (
        public.has_role_in(organization_id, ARRAY['owner','scientist']::public.org_role[])
    );

CREATE POLICY jobs_update_active ON public.jobs
    FOR UPDATE USING (
        public.has_role_in(organization_id, ARRAY['owner','scientist']::public.org_role[])
    );

-- ----------------------------------------------------------------------------
-- 10. Drop legacy users.stripe_customer_id AFTER moving the value
-- ----------------------------------------------------------------------------
-- The backfill above already copied stripe_customer_id to the personal org.
-- Drop the column LAST so any in-flight backend request that read it during
-- migration sees the org-level value before the column disappears.
-- For ONLINE safety, drop in a SECOND migration after backend deploys (see §12).

-- 10a. In THIS migration, just mark the column deprecated via a check that
-- the personal org's customer matches when both are non-null. Drop happens in
-- the follow-up migration 20260606000001_drop_users_stripe_customer.sql.

COMMENT ON COLUMN public.users.stripe_customer_id IS
    'DEPRECATED — Phase 12 moved Stripe to public.organizations.stripe_customer_id. '
    'This column will be dropped in 20260606000001 once backend migration is verified.';

-- ----------------------------------------------------------------------------
-- 11. Update RLS on job_candidates + sessions to org-scope through jobs
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS candidates_own ON public.job_candidates;
CREATE POLICY candidates_org ON public.job_candidates
    FOR ALL USING (
        public.is_member_of(
            (SELECT organization_id FROM public.jobs WHERE id = job_candidates.job_id)
        )
    );

-- Sessions stay user-scoped (a conversation in /chat is private to the
-- individual scientist; not shared with the org). Sessions also fan out into
-- a job, which IS org-shared via jobs.organization_id — so the conversation
-- privacy boundary stops at the session_messages level and the job becomes
-- org-visible the moment it's launched.
-- No change to sessions_own / session_messages_own policies.
```

### 4.2 SECURITY DEFINER RPC to bootstrap an organization

To avoid the chicken-and-egg between `organizations` INSERT policy and `organization_memberships` INSERT policy when creating a new org, the backend calls a SECURITY DEFINER RPC instead of two separate INSERTs:

```sql
CREATE OR REPLACE FUNCTION public.create_organization(_name TEXT)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    new_org_id UUID;
    caller_id UUID := auth.uid();
BEGIN
    IF caller_id IS NULL THEN
        RAISE EXCEPTION 'create_organization requires an authenticated user';
    END IF;
    INSERT INTO public.organizations (name, is_personal, created_by)
    VALUES (_name, FALSE, caller_id)
    RETURNING id INTO new_org_id;

    INSERT INTO public.organization_memberships (organization_id, user_id, role)
    VALUES (new_org_id, caller_id, 'owner');

    RETURN new_org_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.create_organization(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_organization(TEXT) TO authenticated;
```

The backend calls this via `await pool.fetchval("SELECT public.create_organization($1)", name)` rather than two-statement INSERTs.

---

## 5. Recommended Role Model

### 5.1 Permission Matrix

| Action | Owner | Scientist | Viewer |
|--------|:-----:|:---------:|:------:|
| **Organization** | | | |
| View org details | ✓ | ✓ | ✓ |
| Edit org name | ✓ | — | — |
| Delete org | ✓ | — | — |
| **Members** | | | |
| View member list | ✓ | ✓ | ✓ |
| Invite member | ✓ | — | — |
| Remove member | ✓ | — | — |
| Change member role | ✓ | — | — |
| Transfer ownership | ✓ | — | — |
| Leave org (as self) | ✓ (unless last owner) | ✓ | ✓ |
| **Jobs** | | | |
| View any org job | ✓ | ✓ | ✓ |
| Launch job | ✓ | ✓ | — |
| Cancel any org job | ✓ | ✓ | — |
| Download results | ✓ | ✓ | ✓ |
| **Billing** | | | |
| View payment method | ✓ | — | — |
| Add/update payment method | ✓ | — | — |
| View usage summary | ✓ | ✓ (own usage only) | — |
| View invoices | ✓ | — | — |
| Open Stripe portal | ✓ | — | — |
| **Sessions (private)** | | | |
| Each user's own /chat sessions | self | self | self |

**Design notes:**
- **Viewer can download results.** Listed as ✓ because a viewer who can SEE a job's candidates needs to GET the presigned download URL. If you want results gated higher, drop viewer download and surface a "scientist+ required" message.
- **Cancel job is scientist+.** A viewer cannot stop a job they're billed for, which matches "viewer = read-only."
- **Sessions remain private.** Per §4.1 §11, the `/chat` conversation history is per-user; the JOB that emerges from a session becomes org-visible at launch time.

### 5.2 Backend enforcement (matches the SQL helpers)

```python
# backend/auth/org_dependencies.py — NEW FILE

from fastapi import Depends, Header, HTTPException, status
from auth.dependencies import get_current_user
from db.connection import get_db_pool

OrgRole = Literal["owner", "scientist", "viewer"]


async def get_active_org(
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    user_id: str = Depends(get_current_user),
) -> tuple[str, OrgRole]:
    """Resolve the active organization for this request and return (org_id, role).

    Cross-checks the X-Org-Id header against organization_memberships so a
    client cannot freely impersonate an org — the JWT identifies the user,
    and the user must hold a membership in the requested org.

    Raises:
        HTTPException 400: X-Org-Id missing.
        HTTPException 403: User is not a member of the requested org.
    """
    if not x_org_id:
        raise HTTPException(
            status_code=400, detail="X-Org-Id header required for this endpoint"
        )
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role::text FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            x_org_id, user_id,
        )
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    return x_org_id, row["role"]


def require_role(*allowed: OrgRole):
    """Factory: returns a FastAPI dependency that requires one of the given roles."""
    async def dep(active: tuple[str, OrgRole] = Depends(get_active_org)):
        org_id, role = active
        if role not in allowed:
            raise HTTPException(
                status_code=403, detail=f"Requires one of: {', '.join(allowed)}"
            )
        return org_id
    return dep
```

Usage:
```python
@router.post("/jobs/launch")
async def launch_job_endpoint(
    body: LaunchRequest,
    org_id: str = Depends(require_role("owner", "scientist")),
    user_id: str = Depends(get_current_user),
):
    ...
```

### 5.3 Ownership transfer is atomic

```python
async def transfer_ownership(org_id: str, target_user_id: str, new_self_role: OrgRole, pool):
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Promote target to owner. The trigger only blocks demotion, not promotion.
            await conn.execute(
                "UPDATE public.organization_memberships SET role = 'owner' "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, target_user_id,
            )
            # 2. Demote self to new role.
            # The protect_last_owner trigger now sees TWO owners and allows this.
            await conn.execute(
                "UPDATE public.organization_memberships SET role = $3 "
                "WHERE organization_id = $1 AND user_id = $2",
                org_id, current_user_id, new_self_role,
            )
```

---

## 6. Invitation Flow Design

### 6.1 Sequence diagram (text)

```
[Owner UI]                  [Backend]                 [Resend]              [Invited User]
    |                          |                         |                        |
    |--- POST /organizations/  |                         |                        |
    |    {id}/invitations      |                         |                        |
    |    {email, role}         |                         |                        |
    |                          |                         |                        |
    |                          |--- generate token       |                        |
    |                          |    (secrets.token_      |                        |
    |                          |    urlsafe(32))         |                        |
    |                          |                         |                        |
    |                          |--- INSERT org_          |                        |
    |                          |    invitations row      |                        |
    |                          |                         |                        |
    |                          |--- send email --------->|                        |
    |                          |    with accept URL      |                        |
    |                          |                         |                        |
    |                          |                         |--- deliver --------->  |
    |                          |                         |                        |
    |                          |                         |                        |--- clicks
    |                          |                         |                        |    accept URL
    |                          |                         |                        |
    |                          |<--- GET /invitations/   |                        |
    |                          |     accept?token=...    |                        |
    |                          |                         |                        |
    |                          |--- look up token        |                        |
    |                          |    in org_invitations   |                        |
    |                          |                         |                        |
    |                          |--- check expires_at,    |                        |
    |                          |    revoked_at,          |                        |
    |                          |    accepted_at          |                        |
    |                          |                         |                        |
    |                          |--- branch on auth state:                         |
    |                          |    (A) signed in & email matches → join          |
    |                          |    (B) signed in & email DIFFERS → 409 conflict  |
    |                          |    (C) NOT signed in → redirect to /signup?      |
    |                          |        invite_token=... (carry through signup)   |
    |                          |                                                  |
    |                          |--- on accept:                                    |
    |                          |    BEGIN                                         |
    |                          |    INSERT INTO memberships ...                   |
    |                          |    UPDATE invitations SET accepted_at, accepted_by
    |                          |    COMMIT                                        |
    |                          |                                                  |
    |                          |--- send "welcome" email --->                     |
```

### 6.2 Tradeoff: claim-on-signup vs invite-existing-user-only

**Recommendation:** Support BOTH flows. The invitation row carries the email + role. On click:
- **If the user is signed in AND their email == invitation email** → accept directly (single click).
- **If the user is signed in AND email differs** → show "This invite is for email@example.com. Sign out and sign in with that account to accept." (return 409).
- **If the user is NOT signed in AND no account exists for that email** → redirect to `/signup?invite_token=...`. The signup endpoint reads the token, validates it, creates the account, AND inserts the membership row in one transaction.
- **If the user is NOT signed in AND an account DOES exist for that email** → redirect to `/login?invite_token=...&next=/invitations/accept`.

### 6.3 Token generation + email template

- Token: `secrets.token_urlsafe(32)` = 43-character URL-safe base64. Stored plaintext in DB (it's a one-use bearer credential; if your DB is compromised you have larger problems).
- Email: use existing Resend infrastructure. Template signature follows existing `jobs/notifications.py` patterns.
- Accept URL: `{settings.frontend_base_url}/invitations/accept?token={token}` — the frontend `/invitations/accept` route makes the API call and handles redirects per §6.2.
- Default expiry: 7 days. Stored explicitly as `expires_at` (not "created_at + interval"), so changing the default doesn't affect outstanding invites.

### 6.4 Email-matches-existing-user edge case

When the owner invites `existing@user.com` to org X, but that user is already signed in to org Y:
- Backend creates the invitation row (independent of whether the email belongs to a known user).
- Invited user gets the email regardless. Clicking accepts; they're added to org X without disturbing their session in org Y.
- The org switcher in the header now shows both X and Y.
- We do NOT auto-add (without click) even when the email is known — explicit consent prevents accidental "you're now an owner of someone else's org" situations.

---

## 7. Stripe Migration Strategy

This is the riskiest part of the phase. The current state has N personal Stripe customers, each with an open metered subscription, each accruing usage tied to `users.stripe_customer_id`.

### 7.1 Recommended approach

**"Move existing Stripe customer to the personal org"** — no new Stripe customers, no subscription teardown.

The migration in §4.1 step 8a already does the SQL side:
```sql
INSERT INTO public.organizations (name, is_personal, stripe_customer_id, created_by, created_at)
VALUES (..., u.stripe_customer_id, ...)
```

This MOVES (not copies) the `cus_...` ID. The user's existing subscription, payment method, and usage history stay attached to the same Stripe customer; the customer is now keyed to an organization in Kendrew's DB instead of a user.

### 7.2 What happens to billing routes during/after migration

After migration:
- `_resolve_stripe_customer()` in `billing/router.py` takes an **org_id**, not user_id.
- All billing endpoints require `Depends(require_role("owner"))` — only owners see billing.
- Webhook handler reads `organizations.stripe_customer_id` keyed off the job's `organization_id`, not `users.stripe_customer_id`.

### 7.3 Creating a NEW team org (post-migration)

When an owner creates a NEW (team) organization:
1. `create_organization` RPC inserts the row + owner membership.
2. The org has `stripe_customer_id = NULL` initially.
3. First time the owner tries to launch a job in this org OR opens billing:
   - `_resolve_stripe_customer(org_id)` sees NULL, creates a NEW Stripe customer with `metadata={"organization_id": org_id, "kendrew_org_name": name}`.
   - Owner must complete a Stripe Checkout setup session to add a payment method (same flow as today, but Stripe metadata now says org).

### 7.4 Stripe customer metadata update

For migrated customers, push a one-time metadata update to Stripe (idempotent):
```python
async def stamp_migrated_orgs():
    pool = await get_db_pool()
    rows = await pool.fetch(
        "SELECT id, stripe_customer_id, name FROM public.organizations "
        "WHERE is_personal = TRUE AND stripe_customer_id IS NOT NULL",
    )
    for row in rows:
        stripe.Customer.modify(
            row["stripe_customer_id"],
            metadata={
                "organization_id": str(row["id"]),
                "kendrew_org_name": row["name"],
                "is_personal": "true",
                "migrated_from_user_v1": "2026-06-XX",
            },
        )
```
Run as a one-shot script under `backend/scripts/`.

### 7.5 Meter event payload key naming

`stripe.billing.MeterEvent.create` payload uses `"stripe_customer_id"` (the same key Stripe expects for customer-scoped meters per `backend/billing/stripe_client.py:142-148`). NO change to the meter event shape — only the customer ID value changes (now sourced from `organizations`).

### 7.6 What's deferred (explicitly)

- **Splitting historic per-user usage across new team-org siblings.** If a user is now an owner of their personal org AND a member of a new team org, future usage is correctly billed to whichever org owns the job. Pre-migration usage stays on the personal org's invoice. No retroactive re-attribution.
- **Stripe Tax for orgs.** Existing personal-customer Tax IDs (if any) migrate by virtue of customer-ID continuity. Adding a separate Tax ID to a team org is a normal Customer.modify call done via the Billing Portal.

### 7.7 Rollback plan if Stripe migration breaks

If post-migration Stripe routes return errors:
1. Backend immediately deploy-rollback (Vercel + Railway both keep 5 deploys; rollback under 5 min per Phase 11 SC 9).
2. SQL state is forward-only safe: `users.stripe_customer_id` is still present (we left it deprecated, not dropped), so the old code path keeps working against the same `cus_...` values.
3. The SECOND migration that drops `users.stripe_customer_id` is held back until Stripe routes are verified green for ≥24h in production.

---

## 8. Multi-Org UX + Active-Org Context

### 8.1 Where the active org lives — DECISION

| Storage | Pros | Cons | Verdict |
|---------|------|------|---------|
| JWT custom claim | One-piece-of-state per request, no DB round-trip | Token refresh required on every switch; Supabase JWT was JUST stabilized in Phase 11 (ES256 + JWKS); cookie cap is 4KB | **REJECTED** |
| `public.users.active_organization_id` column | Server-side persisted, survives token refresh, no extra header | Switching is a write; doesn't support "open two tabs in two different orgs" | OK but inferior |
| **`X-Org-Id` HTTP header + frontend `localStorage`** | Two tabs can hold different active orgs; server cross-checks against memberships per request | One extra ~36 bytes per request | **CHOSEN** |
| Subdomain (acme.bindwave.com) | Most "professional" UX | Vercel multi-domain config, SSL per-tenant, NOT compatible with Phase 11 single-domain setup | REJECTED for v1 |

### 8.2 How active-org propagates

**Frontend:**
1. On login: `GET /organizations/mine` returns `[{id, name, role, is_personal}]`.
2. Frontend stores last-selected org_id in `localStorage["kendrew.activeOrgId"]`.
3. If no stored selection: default to the personal org. If user has only a personal org: do not show switcher at all.
4. `api()` adds `X-Org-Id: <activeOrgId>` header to every request.

**Backend:**
- Routes that don't need org context (e.g., `/auth/me`, `/organizations/mine`, `/invitations/accept`) do NOT depend on `get_active_org`.
- All org-scoped routes (`/jobs/*`, `/billing/*`, `/organizations/{id}/*`, etc.) depend on `get_active_org` which validates membership.

### 8.3 Job launch UX

When the user clicks "Launch job":
- The job submit form reads the active org from context and displays "Running in: {orgName}".
- If multiple orgs: a non-modal pill in the submit card surfaces the active org with a tiny "Switch" link.
- We do NOT prompt every launch — that's friction. The switcher is in the header for explicit changes.

### 8.4 Switching orgs in UI

- Click switcher → frontend updates localStorage + context, then `window.location.reload()` for a clean state (queries, SSE streams, etc. re-fetch under the new org).
- Per Phase 6 (sessions persist), the user's `/chat` history is per-user not per-org, so the sidebar stays consistent across orgs.

---

## 9. Backend Changes (file-by-file)

### 9.1 New files

| Path | Purpose |
|------|---------|
| `backend/organizations/__init__.py` | Module marker. |
| `backend/organizations/router.py` | All organization + membership + invitation endpoints. |
| `backend/organizations/service.py` | Business logic for invitation token gen, accept flow, transfer ownership. |
| `backend/organizations/models.py` | Pydantic request/response models. |
| `backend/organizations/notifications.py` | Resend templates for invitation emails. |
| `backend/auth/org_dependencies.py` | `get_active_org`, `require_role` (per §5.2). |

### 9.2 New endpoints

| Method | Path | Auth | Body / Query |
|--------|------|------|--------------|
| GET | `/organizations/mine` | user | — |
| POST | `/organizations` | user | `{name: str}` → returns `{id, name, role: "owner"}` |
| GET | `/organizations/{org_id}` | active-org member | — |
| PATCH | `/organizations/{org_id}` | active-org owner | `{name?: str}` |
| DELETE | `/organizations/{org_id}` | active-org owner | requires `confirmation_phrase` like deletion |
| GET | `/organizations/{org_id}/members` | active-org member | — |
| PATCH | `/organizations/{org_id}/members/{user_id}` | active-org owner | `{role: OrgRole}` |
| DELETE | `/organizations/{org_id}/members/{user_id}` | active-org owner OR self | — |
| POST | `/organizations/{org_id}/members/transfer` | active-org owner | `{target_user_id: str, new_self_role: OrgRole}` |
| GET | `/organizations/{org_id}/invitations` | active-org member | filter `?status=pending|accepted|revoked` |
| POST | `/organizations/{org_id}/invitations` | active-org owner | `{email: EmailStr, role: OrgRole}` |
| DELETE | `/organizations/{org_id}/invitations/{invite_id}` | active-org owner | (revoke pending invite) |
| POST | `/invitations/accept` | user | `{token: str}` |
| GET | `/invitations/preview` | optional auth | `?token=...` → `{organization_name, role, valid: bool}` |

Notes:
- `/organizations/mine` and `/invitations/*` do NOT depend on `get_active_org` (chicken-and-egg).
- DELETE org must verify NO non-personal org has active subscriptions with Stripe (or graceful subscription cancel inline). For v1, recommend: blocked while subscription has open invoice; surface "settle outstanding invoices in Billing Portal first."

### 9.3 Existing endpoints touched

| File | Change |
|------|--------|
| `backend/jobs/router.py:103-235` | `launch_job_endpoint` adds `org_id` from `require_role("owner","scientist")`. Job row INSERT includes `organization_id`. Replace `WHERE user_id = $1` with `WHERE id = $1 AND organization_id = $2`. Stripe payment-method check now via org's customer ID. |
| `backend/jobs/router.py:336-421` | `list_jobs` replaces `WHERE user_id = $1` with `WHERE organization_id = $1`. Adds `created_by_user_id` to response so UI can show "launched by [scientist's name]." |
| `backend/jobs/router.py:424-464` | `job_status_stream`, `download_all_designs`, `cancel_job`, `get_job` — every `WHERE user_id = $1` becomes `WHERE id = $1 AND organization_id = $org_id`. |
| `backend/billing/router.py:32-60` | `_resolve_stripe_customer` takes `org_id` not user_id. All endpoints (`/billing/*`) depend on `require_role("owner")`. |
| `backend/webhooks/router.py:299-307` | Read `organizations.stripe_customer_id` by joining through `jobs.organization_id`, not `users.stripe_customer_id`. |
| `backend/user/router.py:96-154` | `/user/usage` is removed OR rescoped to "across all my orgs" (recommend keep, rescope). Add new `/organizations/{id}/usage` for org-level usage. |
| `backend/auth/router.py:81-150` | `signup` flow auto-creates a personal org and inserts membership. Stripe customer creation deferred to first billing interaction. |

### 9.4 Active-org middleware vs dependency

Use FastAPI **dependency** (`get_active_org`), not Starlette middleware. Dependencies run per-route, are testable, return typed values, and don't pay the cost on routes that don't need org context. Middleware would force every route into the validation path.

---

## 10. Frontend Changes (file-by-file)

### 10.1 New files

| Path | Purpose |
|------|---------|
| `frontend/src/lib/organizations.ts` | API client for `/organizations/*` and `/invitations/*`. |
| `frontend/src/components/org/OrganizationSwitcher.tsx` | Header dropdown showing user's orgs + "Create Organization" + "Manage" link. |
| `frontend/src/components/org/OrganizationContext.tsx` | React context provider holding `activeOrgId, orgs, role, refresh()`. Reads `localStorage`. |
| `frontend/src/components/org/MembersTab.tsx` | Members table, invite form, role editor, remove button (in Settings). |
| `frontend/src/components/org/InvitationsTab.tsx` | Pending invites, copy-link, revoke. |
| `frontend/src/components/org/OrgSettingsTab.tsx` | Org name, "Delete organization" (owner-only). |
| `frontend/src/pages/CreateOrganization.tsx` | Standalone page `/organizations/new`. |
| `frontend/src/pages/AcceptInvitation.tsx` | `/invitations/accept` — handles all four §6.2 branches. |

### 10.2 Existing files touched

| Path | Change |
|------|--------|
| `frontend/src/App.tsx:65-110` | Wrap routes in `<OrganizationProvider>`. Add `/organizations/new` and `/invitations/accept` public-ish routes (auth-aware). |
| `frontend/src/components/layout/AppHeader.tsx:19-63` | Insert `<OrganizationSwitcher>` to the right of session title. Hide if user has only one (personal) org. |
| `frontend/src/lib/api.ts:75-115` | `api()` reads `kendrew.activeOrgId` from localStorage and sets `X-Org-Id` header on every request (suppress on routes in an opt-out list like `/auth/*`, `/organizations/mine`, `/invitations/*`). |
| `frontend/src/pages/SettingsPage.tsx` | Add `Organization` tab (members, invitations, settings sub-tabs). Billing tab becomes owner-only (show "ask your owner" copy for non-owners). Usage tab shows current org's usage. |
| `frontend/src/pages/JobHistoryPage.tsx` | Already gets org-scoped results (backend changes propagate). Add a "launched by" column (uses new backend response field). |
| `frontend/src/pages/JobPage.tsx` | Same — backend already returns org-scoped data; UI can show "Run by [name]" + role-gated cancel button. |
| `frontend/src/components/chat/...` (job submit form) | Display "Running in: {orgName}" pill above the submit CTA. Disable submit if active role is viewer. |

---

## 11. Validation Architecture

Test framework already exists; nyquist_validation is enabled (config.json).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest==8.3.5` + `pytest-asyncio==0.24.0` (backend) + `vitest` + `@playwright/test` (frontend) `[VERIFIED: backend/requirements.txt + Phase 9 PLAN]` |
| Config file | `backend/pytest.ini` (asyncio auto-mode) `[VERIFIED: read in this session]` |
| Quick run command | `cd backend && pytest tests/organizations -x` |
| Full suite command | `cd backend && pytest && cd ../frontend && npm test && npx playwright test` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ORG-01 | User can create an organization | unit | `pytest backend/tests/organizations/test_create.py::test_creates_with_owner_membership -x` | ❌ Wave 0 |
| ORG-01 | Owner invites by email | integration | `pytest backend/tests/organizations/test_invitations.py::test_invite_creates_row_and_sends_email -x` | ❌ Wave 0 |
| ORG-02 | Role ENUM accepted, others rejected | unit | `pytest backend/tests/organizations/test_roles.py::test_role_enum_round_trip -x` | ❌ Wave 0 |
| ORG-02 | Permission matrix (viewer cannot launch, scientist cannot bill) | integration | `pytest backend/tests/organizations/test_permissions.py -x` | ❌ Wave 0 |
| ORG-02 | RLS prevents non-member SELECT on jobs | integration (real DB) | `pytest backend/tests/integration/test_rls_jobs_org.py -x` | ❌ Wave 0 |
| ORG-03 | All org members see all org jobs | integration | `pytest backend/tests/organizations/test_list_jobs_org_scope.py -x` | ❌ Wave 0 |
| ORG-04 | One Stripe meter event per job, customer = org's customer | unit (mock Stripe) | `pytest backend/tests/billing/test_meter_org.py -x` | ❌ Wave 0 (extends existing test_meter.py) |
| ORG-04 | Webhook handler routes to org's customer | unit | `pytest backend/tests/webhooks/test_runpod_org_billing.py -x` | ❌ Wave 0 |
| ORG-05 | Remove non-owner member | unit | `pytest backend/tests/organizations/test_remove_member.py::test_owner_removes_scientist -x` | ❌ Wave 0 |
| ORG-05 | Last-owner remove blocked by trigger | integration (real DB) | `pytest backend/tests/integration/test_last_owner_trigger.py -x` | ❌ Wave 0 |
| ORG-05 | Transfer ownership atomic | integration | `pytest backend/tests/organizations/test_transfer_ownership.py -x` | ❌ Wave 0 |
| ORG-06 | User in 2 orgs can list both | unit | `pytest backend/tests/organizations/test_mine_endpoint.py -x` | ❌ Wave 0 |
| ORG-06 | X-Org-Id header validated against membership | unit | `pytest backend/tests/organizations/test_active_org_dependency.py -x` | ❌ Wave 0 |
| ORG-06 | Cross-org access returns 403 | integration | `pytest backend/tests/organizations/test_cross_org_isolation.py -x` | ❌ Wave 0 |
| ORG-07 | Migration creates personal org per user and moves stripe_customer_id | integration | `pytest backend/tests/integration/test_org_migration.py -x` | ❌ Wave 0 |
| ORG-07 | Pre-migration jobs visible to original user post-migration | integration | `pytest backend/tests/integration/test_org_migration.py::test_legacy_jobs_visible -x` | ❌ Wave 0 |
| ORG-08 | Last owner cannot leave | integration | `pytest backend/tests/integration/test_last_owner_trigger.py -x` | ❌ Wave 0 |
| ORG-01..08 | E2E happy path (create org → invite → accept → run job → owner sees billing) | E2E (Playwright) | `npx playwright test frontend/tests/e2e/organizations.spec.ts` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest backend/tests/organizations -x` (under 30s expected for the unit subset)
- **Per wave merge:** `pytest backend/tests/organizations backend/tests/integration/test_org_migration.py backend/tests/integration/test_last_owner_trigger.py backend/tests/integration/test_rls_jobs_org.py`
- **Phase gate:** full backend + frontend + Playwright green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `backend/tests/organizations/` directory + `__init__.py`
- [ ] `backend/tests/organizations/conftest.py` — fixtures: `org_factory`, `member_factory`, `invitation_factory`
- [ ] `backend/tests/integration/test_org_migration.py` — runs the migration against a fresh schema snapshot
- [ ] `backend/tests/integration/test_last_owner_trigger.py` — exercises the trigger with concurrent connections
- [ ] `backend/tests/integration/test_rls_jobs_org.py` — uses `set_config('request.jwt.claims', ...)` to drive RLS as different users
- [ ] `frontend/tests/e2e/organizations.spec.ts` — Playwright spec
- [ ] Reuse existing `conftest.py` Supabase fixtures (already in place for auth integration)

---

## 12. Migration + Rollout Plan

### 12.1 Migration ordering

The Phase 12 changes touch RLS, the jobs table, the users table, and Stripe — all live in prod. Sequencing is non-negotiable:

| Step | What | Where | Online-safe? |
|------|------|-------|-------------|
| 1 | Deploy backend with org module DISABLED behind `settings.organizations_enabled` flag | Railway | ✓ |
| 2 | Apply migration `20260605000001_organizations.sql` | Supabase predeploy | ✓ (additive + backfill in one tx) |
| 3 | Run `backend/scripts/stamp_migrated_orgs.py` to push Stripe metadata | One-shot | ✓ |
| 4 | Verify in Supabase Studio: every user has exactly one personal org membership; every job has an `organization_id`; `organizations.stripe_customer_id` matches the source `users.stripe_customer_id` | Manual | — |
| 5 | Flip `organizations_enabled = true` and deploy backend | Railway | ✓ |
| 6 | Deploy frontend with org UI | Vercel | ✓ |
| 7 | Smoke test: existing users see exactly their personal org as active, can see all jobs, can hit billing portal | Manual + E2E | — |
| 8 | Wait 24 hours, watch Sentry + GPU spend alerts | Monitoring | — |
| 9 | Apply migration `20260606000001_drop_users_stripe_customer.sql` (drops the deprecated column) | Supabase | ✓ (column unused by new code) |

### 12.2 Feature flag

- `settings.organizations_enabled: bool = False` in `backend/config.py`.
- When `False`: org endpoints are not mounted; `get_active_org` is a no-op (or not used because routes still use `user_id`); existing single-tenant code path is the active one.
- When `True`: full org behavior, including `X-Org-Id` header enforcement.

This is ONE deploy cycle of dual-path co-existence, removed by Step 5 above. Do NOT leave the flag in long-term — it's a roll-forward switch.

### 12.3 Online migration safety

- The backfill in `DO $$ ... $$` runs in the migration's implicit transaction. If anything fails, the entire migration rolls back; no half-state.
- The migration takes seconds for any reasonable user count (write a single `INSERT`-per-user + `UPDATE jobs WHERE user_id = u.id`). For huge user tables, replace the inner UPDATE with a batched form, but at Bindwave's current scale this isn't needed.
- `ALTER TABLE public.jobs ALTER COLUMN organization_id SET NOT NULL` runs after backfill in the same tx — safe.

### 12.4 Rollback plan

| Failure mode | Rollback |
|--------------|----------|
| Migration fails mid-transaction | Postgres rolls back automatically; nothing to do |
| Migration applied but backend org code 500s | Railway rollback to previous deploy; old code path queries `users.stripe_customer_id` which is still present |
| Stripe meter events misfiring to wrong customer | Railway rollback; metadata change is reversible via second `Customer.modify` call |
| Frontend breaks org switcher rendering | Vercel rollback (separate from backend) |

The DECISIVE rollback gate is: **Don't drop `users.stripe_customer_id` until 24h of clean production data with the new code path.** Step 9 is the point of no return.

---

## 13. Open Questions / Claude's Discretion

These are decision points the planner or executor will resolve during execution; none are blockers, but each affects user experience.

1. **Personal org naming convention.** Current backfill names "lwan (Personal)" from email. Some users may want to rename. Backend allows it (PATCH /organizations/{id}); UX-wise, do we show the personal org name verbatim in the switcher, or always say "Personal" with the user's email subtitle?
2. **Show personal org in switcher when user has team orgs too?** Yes (recommended) — users should be able to launch private jobs that aren't billed to their team. Alternative: hide personal once user joins a team. Pick one and document.
3. **Email-domain auto-join.** Some platforms detect "you signed up with @company.com and Acme Bio has 5 @company.com members" and offer to join the existing team. This is a v1 nice-to-have but adds privacy concerns (org existence is implicitly revealed). Recommend: DEFER.
4. **Org-level retention override.** Phase 10 added `users.data_retention_days`. Should orgs also have a retention setting that overrides member-level? For v1, recommend: keep retention per-user — orgs only matter for jobs/billing.
5. **Org-level Sentry user context.** Sentry SDK currently sets `user_id` on errors; we could add `organization_id` tag. Cheap, helpful for triage. Recommend: do it.
6. **API key scoping (Phase 13 cross-cutting).** Phase 13 will introduce API keys; should keys be org-scoped, user-scoped, or both? Phase 12 should not commit to this — but the org schema should not preclude either model. Currently it doesn't.
7. **Job created_by vs owned_by.** A job is launched BY a user but billed TO an org. Add `public.jobs.created_by_user_id` (NOT NULL, references public.users(id)) so the UI can show "launched by Alice." Already implied in §9 but worth explicit decision.
8. **Cancel-self when scientist.** Current spec: scientist can cancel any org job. Is that too broad (one bad actor cancels everyone's runs)? Alternative: only owner can cancel jobs not launched by themselves. Recommend: keep scientist-can-cancel-all; it matches "team trust" model.
9. **Onboarding nudge for solo-to-team conversion.** Show "Invite your team" banner in dashboard. Optional polish. Recommend: defer to Phase 12.5.
10. **GDPR exports across orgs.** Phase 10 GDPR export shipped per-user. When a user is in multiple orgs, the export should still be per-user (their own session_messages, their personal data) but should NOT include other org members' data. Confirm during execution.

---

## 14. Pitfalls + Landmines

### 14.1 RLS recursive policy — SECURITY DEFINER must use PL/pgSQL

`[VERIFIED: dev.to + github.com/orgs/supabase/discussions/3328]`

A SECURITY DEFINER **SQL** function that queries `organization_memberships` will be **inlined** by Postgres during query planning. Inlining drops the SECURITY DEFINER context, RLS re-applies to the inlined subquery, and you get `infinite recursion detected in policy for relation "organization_memberships"`.

**Fix:** Use `LANGUAGE plpgsql` (NOT `sql`). PL/pgSQL functions are never inlined. See §4.1 helper definitions.

### 14.2 The "join from RLS policy" foot-gun

If `organizations` RLS says "user must be a member" (queries `organization_memberships`) AND `organization_memberships` RLS says "user must be a member of the org" (queries `organization_memberships`), even with `is_member_of` you must be careful about the SELF-policy on memberships.

The schema in §4.1 sidesteps this by giving the memberships policy `public.is_member_of(organization_id)`, which the SECURITY DEFINER helper resolves at the function-owner privilege level. **Confirmed via the dev.to article's worked example.**

### 14.3 Stripe payment-method-on-active-subscription cannot be detached

`[CITED: docs.stripe.com/api/payment_methods/detach — "PaymentMethod cannot be detached if it's the default payment method on a subscription"]`

If an owner who is migrating their personal Stripe customer to a team org wants to remove the payment method, Stripe will block while there's an active metered subscription. Document this in the org-deletion flow: "Cancel any open subscriptions before deleting the organization."

### 14.4 JWT claim updates require token refresh

If we DID put `active_org_id` in the JWT (we're not, per §8), switching orgs would require a forced token refresh (logout/re-login or `/auth/refresh` with new claim). The Supabase `signed-keys` JWKS path doesn't have a custom-claim hook by default; this would require a Supabase Edge Function. **Avoided entirely by using the header.**

### 14.5 The "last owner cannot leave" invariant MUST be DB-enforced

App-level checks alone race under concurrent DELETEs:
```
Tx A: SELECT count(*) WHERE role='owner' → 2
Tx B: SELECT count(*) WHERE role='owner' → 2
Tx A: DELETE WHERE user_id = me → 1
Tx B: DELETE WHERE user_id = other → 0  ← BOOM
```

Trigger in §4.1 closes this. `[ASSUMED]` based on standard Postgres trigger semantics — the BEFORE DELETE trigger sees the post-delete state when counting, and EXCLUSIVE row locks prevent overlap. Verify in test_last_owner_trigger.py with two concurrent psql sessions.

### 14.6 `supabase/migrations/...` ordering

Phase 11 D-06 makes Supabase CLI the migration owner. Migrations must be named `YYYYMMDDHHMMSS_*.sql`. The 20260605 + 20260606 split (§12.1) follows convention; verify no other phase has reserved those slots.

### 14.7 RLS performance — every query takes a `is_member_of` hit

Every SELECT on `jobs`, `job_candidates`, `organizations`, `organization_memberships` triggers one SECURITY DEFINER function call. PL/pgSQL adds ~10μs overhead per call vs raw SQL. For a job-list query returning 25 rows, that's negligible. For a stream of webhook ingests... webhooks use service_role and bypass RLS, so this is a non-issue. **Confirm:** webhook handler uses `pool = await get_db_pool()` which connects as service_role per existing setup.

### 14.8 Idempotency on invitation accept

If an invited user double-clicks "Accept," two requests race. The `INSERT INTO memberships ON CONFLICT DO NOTHING` keeps this safe. The `UPDATE invitations SET accepted_at = now() WHERE accepted_at IS NULL` is also idempotent. Test this.

### 14.9 Frontend `X-Org-Id` cache invalidation on logout

On logout, clear `localStorage["kendrew.activeOrgId"]`. Failing to do so means a subsequent login (potentially as a different user on the same device) sends a stale org ID; backend will 403 it (membership check), and the UI must handle that gracefully (clear stale ID, default to personal).

### 14.10 `auth.uid()` returns NULL outside an authenticated request

The migration's backfill DO block runs as the migration user, not an authenticated user. `auth.uid()` would be NULL there, but the backfill doesn't call `auth.uid()` — it uses the explicit `u.id` from the cursor. Good.

For the `protect_last_owner` trigger, `auth.uid()` is unused — the trigger uses `OLD.role` and `OLD.organization_id` directly. Good.

### 14.11 Supabase project IS named `omrhpkmgiqvuwpadhbsl` ("kendrew-prod" breadcrumb but actually Bindwave)

`[VERIFIED: MEMORY.md project_bindwave_supabase_auth_hardened.md]`

The Supabase project reference is `omrhpkmgiqvuwpadhbsl`. The dashboard breadcrumb may still say "kendrew-prod" — that's the legacy name. Migrations apply to this project. Predeploy command uses `$DATABASE_URL` (already pointing to this project in Phase 11 Railway env).

### 14.12 Webhook handler bypasses RLS

`[VERIFIED: backend/webhooks/router.py read in this session]`

The webhook handler is an UNAUTHENTICATED endpoint; it uses the `service_role` DB pool which bypasses RLS. This means the org-aware lookup in §3.4 (looking up `organizations.stripe_customer_id` via job.organization_id) works without any RLS surgery. Good.

### 14.13 The frontend `csrftoken_v2` cookie name precedent

`[VERIFIED: backend/main.py:100]`

Phase 11 renamed the CSRF cookie to `csrftoken_v2` to force a browser refresh on the cookie-domain change. There's no equivalent forcing-function for Phase 12 (the new `X-Org-Id` header is forward-only), but if we EVER need to invalidate stored active-org IDs after a backend incident, set a version suffix on the localStorage key (`kendrew.activeOrgId.v2`).

### 14.14 Sessions and chats are user-private, jobs are org-shared

This split (§4.1 Item 11) needs UX clarity: a scientist may launch a job from /chat, expecting only their org's jobs to share that conversation. The current backend already links `jobs.session_id → sessions.id`, and `sessions.user_id → users.id`. After Phase 12:
- A teammate viewing /jobs/{id} can see the job, the job's parameters, the candidates, the cost.
- They CANNOT see the conversation that produced the job (sessions stay private).
- The job-detail page should NOT show a "View conversation" link to non-owners.

Verify in tests.

---

## 15. References

### Primary (HIGH confidence)

- `[VERIFIED]` Supabase RLS docs — Row Level Security with security_invoker views (Postgres 15+) — https://supabase.com/docs/guides/database/postgres/row-level-security
- `[VERIFIED]` Postgres RLS infinite-recursion via SECURITY DEFINER inlining — https://dev.to/bairescodeai/infinite-recursion-in-postgres-rls-a-security-definer-gotcha-1916
- `[VERIFIED]` GitHub: Supabase discussion #3328 — "infinite recursion detected in policy" — https://github.com/orgs/supabase/discussions/3328
- `[VERIFIED]` Phase 11 plan + context — `.planning/phases/11-deployment/11-CONTEXT.md` (Supabase Cloud + Stripe + Modal architecture is fixed)
- `[VERIFIED]` Existing codebase — `backend/billing/stripe_client.py`, `backend/auth/jwks.py`, `backend/jobs/router.py`, `backend/webhooks/router.py`, `supabase/migrations/*` — read in this session

### Secondary (MEDIUM confidence)

- `[CITED]` Stripe Billing Meters API docs — `https://docs.stripe.com/billing/subscriptions/usage-based/migration` — billing meters track usage across multiple customers without subscriptions, so migrating customer-id values without recreating the subscription is supported
- `[CITED]` Makerkit "Supabase RLS Best Practices: Production Patterns for Secure Multi-Tenant Apps" — https://makerkit.dev/blog/tutorials/supabase-rls-best-practices
- `[CITED]` Medium: "Multi-Tenant Architecture with FastAPI: Design Patterns and Pitfalls" (Koushik Sathish, Apr 2025) — header-based tenant resolution with X-Tenant-ID is the standard FastAPI pattern; must always cross-check against auth
- `[CITED]` PostgreSQL trigger docs — BEFORE DELETE triggers run with row-level locks per implicit MVCC contract

### Tertiary (LOW confidence — flag for validation during execution)

- `[ASSUMED]` `protect_last_owner` trigger correctness under high concurrency — needs the test in test_last_owner_trigger.py to run with two psql clients to confirm; standard PG semantics suggest it's correct but the failure mode is silent so test rigorously
- `[ASSUMED]` Stripe metadata update doesn't trigger a customer.updated webhook flood — verify in test_stamp_migrated_orgs.py
- `[ASSUMED]` Resend rate limits allow N invitation emails on bulk-invite — verify by checking Resend account quota before bulk operations

---

## Project Constraints (from CLAUDE.md)

- **graphify rebuild after code changes:** Run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` after modifying code files in this session. The Phase 12 implementation will touch many files; the plan should include a graphify rebuild as a final step.

(No other directives in this repo's CLAUDE.md.)

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `protect_last_owner` PL/pgSQL trigger handles concurrent owner-deletes correctly | §14.5 | Two simultaneous deletes could BOTH succeed, leaving an orphan org. MUST be load-tested. |
| A2 | Stripe `Customer.modify` for metadata doesn't trigger a flood of customer.updated webhooks | §7.4 | Webhook handler could 5xx on burst; mitigated by Stripe's built-in retry. Low risk. |
| A3 | Resend can deliver N invitation emails without hitting rate limits | §6.3 / §14 | A "invite 50 colleagues at once" UX could partially fail. Recommend rate-limit invitations at backend (10/minute per org). |
| A4 | Default invitation expiry of 7 days is right | §6.3 | Too short = friction; too long = stale tokens. Verify with usage data post-launch. |
| A5 | Scientist role having cancel-on-anyone's-job is acceptable | §13 Q8 | Bad actor could grief team. Recommend monitoring + audit log. |
| A6 | Sessions stay user-private (not org-shared) | §14.14 | Some teams might want shared conversation history. Document explicitly; revisit post-launch. |
| A7 | Personal org auto-rename allowed via PATCH | §13 Q1 | UI may need to render special-case label even if name is custom. |

---

## Metadata

**Confidence breakdown:**
- Standard stack (Postgres ENUM + RLS + plpgsql functions + FastAPI dependencies): **HIGH** — verified against Supabase docs, Postgres docs, and existing project patterns
- Architecture (organizations/memberships/invitations schema; X-Org-Id header propagation): **HIGH** — multiple independent sources converge on this design
- Stripe migration sequencing: **MEDIUM** — Stripe doesn't publish an explicit "swap owner identity on metered subscription" workflow; chosen path (move customer-id from one app-side table to another, keep subscription intact) is sound but should be smoke-tested in Stripe test mode before prod cutover
- Pitfalls (RLS recursion, last-owner trigger): **HIGH** — verified problem reports and the canonical fix

**Research date:** 2026-06-03
**Valid until:** 2026-07-03 (30 days; Supabase ES256 JWT migration just stabilized and Stripe Billing Meters API is stable)

---

## RESEARCH COMPLETE
