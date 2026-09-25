-- ============================================================================
-- Phase 12: make the org cutover tolerant of callers that do not set org columns
-- ============================================================================
-- 20260605000001 and ...002 add jobs.organization_id and jobs.created_by_user_id
-- and set both NOT NULL. railway.toml runs `supabase db push` as the Railway
-- preDeployCommand, so both land on production BEFORE any new backend replica
-- serves traffic, and (numReplicas = 2) old replicas keep serving against the
-- new schema for the length of the rolling deploy.
--
-- Two populations of INSERT INTO public.jobs do not supply the new columns:
--
--   1. Old backend replicas during the rolling-deploy window.
--   2. Two paths this phase never touched, which still insert only
--      (id, user_id, tool, status, job_spec, created_at):
--        backend/agent/tools.py:850          (draft job behind the ReviewCard)
--        backend/agent/analysis/refolding.py:172  (refolding validation jobs)
--      Both swallow the exception, so without this migration they would fail
--      silently and permanently -- not just during the deploy window.
--
-- A BEFORE INSERT trigger fills both columns from the row's own user_id, which
-- covers every caller including ones that do not exist yet. BEFORE-row triggers
-- run before NOT NULL is checked, so the constraint stays enforced for real
-- violations (user_id is itself NOT NULL, per 20260318000000_init.sql:12).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. One personal org per user, enforced by the database
-- ----------------------------------------------------------------------------
-- 20260605000001 §8a backfills exactly one personal org per existing user with
-- created_by = that user. This index makes that a rule rather than a habit:
-- without it, a retried signup or two concurrent lazy creations could give one
-- user two personal orgs, and billing would then read stripe_customer_id off
-- the empty one and create a DUPLICATE Stripe customer for an existing payer.
CREATE UNIQUE INDEX organizations_one_personal_per_creator
    ON public.organizations (created_by)
    WHERE is_personal;

-- ----------------------------------------------------------------------------
-- 2. Find-or-create a user's personal org, idempotently
-- ----------------------------------------------------------------------------
-- Called from the jobs trigger below and from backend/auth/org_dependencies.py
-- (the X-Org-Id fallback) and backend/auth/router.py (signup bootstrap).
-- Safe to call any number of times and from concurrent sessions.
CREATE OR REPLACE FUNCTION public.personal_org_for(_user_id UUID)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    found_org UUID;
    label     TEXT;
BEGIN
    IF _user_id IS NULL THEN
        RAISE EXCEPTION 'personal_org_for requires a user id';
    END IF;

    SELECT id INTO found_org
    FROM public.organizations
    WHERE created_by = _user_id AND is_personal;

    IF found_org IS NULL THEN
        -- Name matches the 20260605000001 §8a backfill convention so a lazily
        -- created org is indistinguishable from a backfilled one in the UI.
        SELECT COALESCE(NULLIF(split_part(email, '@', 1), ''), 'Personal')
               || ' (Personal)'
          INTO label
        FROM public.users
        WHERE id = _user_id;

        IF label IS NULL THEN
            RAISE EXCEPTION 'personal_org_for: no public.users row for %', _user_id;
        END IF;

        INSERT INTO public.organizations (name, is_personal, created_by)
        VALUES (label, TRUE, _user_id)
        ON CONFLICT (created_by) WHERE is_personal DO NOTHING
        RETURNING id INTO found_org;

        IF found_org IS NULL THEN
            -- Lost the race to a concurrent session; adopt the row it inserted.
            SELECT id INTO found_org
            FROM public.organizations
            WHERE created_by = _user_id AND is_personal;
        END IF;
    END IF;

    -- Separate statement, not an afterthought: an org whose owner membership is
    -- missing is invisible to is_member_of(), so every RLS-scoped read of it
    -- would come back empty. Re-asserted on every call for that reason.
    INSERT INTO public.organization_memberships (organization_id, user_id, role)
    VALUES (found_org, _user_id, 'owner')
    ON CONFLICT (organization_id, user_id) DO NOTHING;

    RETURN found_org;
END;
$$;

COMMENT ON FUNCTION public.personal_org_for(UUID) IS
    'Find-or-create the caller-supplied user''s personal organization, with its '
    'owner membership. Idempotent and concurrency-safe via '
    'organizations_one_personal_per_creator.';

-- Not granted to `authenticated`: the function takes the user id as an argument
-- and is SECURITY DEFINER, so a grant there would let any signed-in caller reach
-- it through PostgREST (POST /rest/v1/rpc/personal_org_for) for an arbitrary user
-- id. The backend pool connects as `postgres` (settings.database_url,
-- backend/db/connection.py:29) and the trigger below is itself SECURITY DEFINER,
-- so neither needs the grant.
REVOKE EXECUTE ON FUNCTION public.personal_org_for(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.personal_org_for(UUID) TO service_role;

-- ----------------------------------------------------------------------------
-- 3. Fill the new jobs columns when the inserting code does not
-- ----------------------------------------------------------------------------
-- SECURITY DEFINER so the trigger can call personal_org_for regardless of which
-- role ran the INSERT; without it the REVOKE above would turn a tolerated
-- insert into a permission error for any role outside the two grants.
CREATE OR REPLACE FUNCTION public.jobs_fill_org_defaults()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF NEW.created_by_user_id IS NULL THEN
        NEW.created_by_user_id := NEW.user_id;
    END IF;
    IF NEW.organization_id IS NULL THEN
        NEW.organization_id := public.personal_org_for(NEW.user_id);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER jobs_fill_org_defaults_trigger
    BEFORE INSERT ON public.jobs
    FOR EACH ROW
    EXECUTE FUNCTION public.jobs_fill_org_defaults();

COMMENT ON FUNCTION public.jobs_fill_org_defaults() IS
    'BEFORE INSERT on public.jobs: default organization_id to the inserting '
    'user''s personal org and created_by_user_id to jobs.user_id. Keeps inserts '
    'that predate Phase 12 working against the post-Phase-12 schema.';

-- ----------------------------------------------------------------------------
-- 4. Stripe customer resolution that survives the rolling-deploy window
-- ----------------------------------------------------------------------------
-- 20260605000001 §8a copied users.stripe_customer_id onto each personal org, so
-- at migration time both columns agree. They can then diverge for the length of
-- the rolling deploy: an OLD replica serving POST /billing/checkout-session
-- creates a Stripe customer and writes it to public.users.stripe_customer_id
-- only. New code that reads just organizations.stripe_customer_id would then
--   - create a SECOND Stripe customer for that user (their new card sits on the
--     first one, so check_payment_method stays false), and
--   - meter GPU usage to nothing, because both metering call sites skip billing
--     when the resolved customer is NULL (backend/webhooks/router.py:310,
--     backend/jobs/service.py:133).
--
-- These two readers COALESCE to the pre-Phase-12 column, which is why
-- 20260606000001_drop_users_stripe_customer_id.sql must land in a LATER deploy:
-- see docs/runbook-phase-12-rollout.md step 8, which copies any remaining
-- values onto the orgs before dropping the column.
--
-- Read-only and SECURITY INVOKER: a SECURITY DEFINER version granted to
-- `authenticated` would expose any org's Stripe customer id through PostgREST
-- to anyone who can guess an org uuid.

CREATE OR REPLACE FUNCTION public.org_stripe_customer(_org_id UUID)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(o.stripe_customer_id, u.stripe_customer_id)
    FROM public.organizations o
    LEFT JOIN public.users u ON u.id = o.created_by AND o.is_personal
    WHERE o.id = _org_id;
$$;

COMMENT ON FUNCTION public.org_stripe_customer(UUID) IS
    'Stripe customer id for an organization, falling back to the pre-Phase-12 '
    'public.users.stripe_customer_id of a personal org''s creator. NULL when '
    'neither is set (org has never paid).';

REVOKE EXECUTE ON FUNCTION public.org_stripe_customer(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.org_stripe_customer(UUID) TO service_role;

-- Same resolution keyed by user, for the three readers that start from a user
-- row rather than an org: backend/user/export.py, backend/worker/deletion_cron.py
-- and backend/admin/router.py. Keyed on organizations.created_by, the column
-- carrying the unique index in §1, so the answer is one value by construction --
-- the LEFT JOIN through organization_memberships those three used instead
-- returns one row PER MEMBERSHIP, which multiplied users across the admin list
-- and the deletion cron.
CREATE OR REPLACE FUNCTION public.user_stripe_customer(_user_id UUID)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(
        (SELECT o.stripe_customer_id
           FROM public.organizations o
          WHERE o.created_by = _user_id AND o.is_personal),
        (SELECT u.stripe_customer_id
           FROM public.users u
          WHERE u.id = _user_id)
    );
$$;

COMMENT ON FUNCTION public.user_stripe_customer(UUID) IS
    'Stripe customer id held by a user''s personal organization, falling back to '
    'the pre-Phase-12 public.users.stripe_customer_id. NULL when neither is set.';

REVOKE EXECUTE ON FUNCTION public.user_stripe_customer(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.user_stripe_customer(UUID) TO service_role;


-- ----------------------------------------------------------------------------
-- 5. protect_last_owner must not fire on a cascading parent delete
-- ----------------------------------------------------------------------------
-- 20260605000001 section 5 raises check_violation whenever the last `owner`
-- membership row of an organization disappears. Row triggers also fire for the
-- DELETEs that a referential CASCADE performs, and organization_memberships
-- cascades from BOTH parents (organization_id -> organizations,
-- user_id -> public.users; see 20260605000001 lines 49-50). Every user is the
-- sole owner of their personal org, so without the guard below:
--
--   * DELETE FROM auth.users (backend/user/deletion.py:197, the GDPR hard
--     delete) cascades public.users -> the personal-org membership -> raise.
--     R2 objects and the Stripe customer are already purged by then, so the
--     account would be stuck pending and the cron would retry it forever.
--     This happens with organizations_enabled = false: it is a schema rule,
--     not a routing one.
--   * DELETE FROM public.organizations (organizations/router.py delete_org)
--     cascades its own memberships -> raise, so no org could ever be deleted.
--
-- Postgres runs a CASCADE as a separate DELETE statement issued from the
-- parent's internal AFTER trigger, so by the time this BEFORE trigger sees the
-- child row the parent row is already gone from the current snapshot. A missing
-- parent is therefore an exact test for "this row is going away with its
-- parent", and a direct DELETE of a sole owner is still refused.
--
-- The tolerance is deliberately asymmetric. A missing ORGANIZATION is enough on
-- its own: the org is being deleted, so it has no future in which an owner
-- could matter. A missing USER is only enough for a PERSONAL org. A shared org
-- outlives the user, and with zero owners nothing could ever administer it
-- again, so that case still raises -- backend/user/deletion.py hands those orgs
-- over before it deletes the auth row, and this is the backstop that makes any
-- other path fail loudly instead of stranding the org.
CREATE OR REPLACE FUNCTION public.protect_last_owner()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    remaining_owners INT;
BEGIN
    -- The org itself is going away, so there is no owner left to protect.
    IF TG_OP = 'DELETE'
       AND NOT EXISTS (SELECT 1 FROM public.organizations WHERE id = OLD.organization_id)
    THEN
        RETURN OLD;
    END IF;

    -- Only the USER is going away. A personal org has exactly one member --
    -- guard_personal_org_single_member() in section 7 enforces that -- so its
    -- owner leaving strands nobody. A SHARED org would be left with zero
    -- owners, and no owner-only route or memberships_write_owners RLS policy
    -- would have a satisfiable caller ever again -- so shared orgs fall through
    -- to the check below on purpose. backend/user/deletion.py promotes an heir
    -- (or deletes the emptied org) before the cascade reaches here; this branch
    -- is what makes a path that skipped that step fail loudly instead of
    -- silently breaking the invariant.
    IF TG_OP = 'DELETE'
       AND NOT EXISTS (SELECT 1 FROM public.users WHERE id = OLD.user_id)
       AND EXISTS (SELECT 1 FROM public.organizations
                    WHERE id = OLD.organization_id AND is_personal)
    THEN
        RETURN OLD;
    END IF;

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

COMMENT ON FUNCTION public.protect_last_owner() IS
    'Refuses to remove or demote an organization''s last owner, except when the '
    'membership row is disappearing with its own parent org, or with its own '
    'user row out of a PERSONAL org. A shared org is still protected when only '
    'the user row is deleted.';


-- ----------------------------------------------------------------------------
-- 6. no_duplicate_pending only ever applied to PENDING invitations
-- ----------------------------------------------------------------------------
-- 20260605000001 line 79 declares it as an unconditional
-- UNIQUE (organization_id, email), so the second invitation ever sent to an
-- address raises unique_violation even when the first was revoked or accepted
-- and the member later removed. organizations/router.py create_invitation does
-- a plain INSERT, so that surfaced as a 500. Replaced with the partial index
-- the name always described. lower(email) matches the case-insensitive email
-- comparison the accept path uses (organizations/service.py accept_invitation).
ALTER TABLE public.organization_invitations
    DROP CONSTRAINT IF EXISTS no_duplicate_pending;

CREATE UNIQUE INDEX IF NOT EXISTS organization_invitations_one_pending
    ON public.organization_invitations (organization_id, lower(email))
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

COMMENT ON INDEX public.organization_invitations_one_pending IS
    'At most one live invitation per (org, email). Revoked and accepted rows are '
    'excluded so an address can be re-invited.';


-- ----------------------------------------------------------------------------
-- 7. A personal org has exactly one member
-- ----------------------------------------------------------------------------
-- Section 5's user-gone tolerance rests on that sentence, and until now nothing
-- enforced it. require_path_role('owner') passes for the owner of their own
-- personal org, so the invite + accept flow would add a second member to it.
-- The owner then deletes their account, section 5 lets the only owner
-- membership go with the cascade, and the second member is left in an org with
-- zero owners -- which delete_organization refuses to remove because it is
-- personal (backend/organizations/router.py:195-199), so nobody can clean it up
-- and the departed user's email local part stays in its name. Rejecting the
-- second member is what makes that tolerance sound.
CREATE OR REPLACE FUNCTION public.guard_personal_org_single_member()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.organizations
                WHERE id = NEW.organization_id AND is_personal)
       AND EXISTS (SELECT 1 FROM public.organization_memberships
                    WHERE organization_id = NEW.organization_id
                      AND user_id <> NEW.user_id)
    THEN
        RAISE EXCEPTION
            'Organization % is personal and cannot have a second member',
            NEW.organization_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- CREATE TRIGGER does not validate existing rows, and nothing here repairs
-- one, because no database can hold a violating row at this point: all three
-- org migrations are new in this phase, so whatever runs this file created
-- organization_memberships empty in the same push. A database that already
-- applied an earlier copy of this file is also the one place a repair
-- statement could not reach -- supabase db push skips a version already in the
-- remote migration history, so nothing edited into this file runs there. Reset
-- such a database rather than expecting this file to fix it.
--
-- UPDATE OF as well: moving an existing membership row onto a personal org
-- reaches the same end state as inserting one there.
CREATE TRIGGER organization_memberships_personal_single_member
    BEFORE INSERT OR UPDATE OF organization_id, user_id
    ON public.organization_memberships
    FOR EACH ROW
    EXECUTE FUNCTION public.guard_personal_org_single_member();

COMMENT ON FUNCTION public.guard_personal_org_single_member() IS
    'Rejects a second member in a personal organization. protect_last_owner() '
    'tolerates the user-gone cascade for personal orgs only because of this.';
