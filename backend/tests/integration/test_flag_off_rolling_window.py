"""Hazard 3, SQL half: the schema survives the rolling-deploy window.

railway.toml runs ``supabase db push`` as the Railway ``preDeployCommand``, so
merging this PR applies 20260605000001 (organizations, jobs.organization_id NOT
NULL), 20260605000002 (jobs.created_by_user_id NOT NULL) and 20260605000003
(this PR's tolerance migration) to production BEFORE any new replica serves
traffic. With ``numReplicas = 2`` the old replicas then keep serving against the
new schema for the length of the deploy. Two things must hold in that window:

  * An INSERT INTO public.jobs that supplies neither new column must succeed.
    Old replicas do exactly that, and so do two paths this phase never touched
    (backend/agent/tools.py, backend/agent/analysis/refolding.py), which swallow
    their exception -- so without tolerance they would fail silently and
    permanently, not just during the deploy.
  * A user created by an old replica has a public.users row and no personal org.
    Resolving that lazily must be idempotent and concurrency-safe, or one user
    ends up with two personal orgs and billing reads the empty one.

It also pins the money-safety property that makes the drop-column migration a
separate, later PR: both Stripe resolvers fall back to the deprecated
public.users.stripe_customer_id, because an old replica writes a new customer
id to that column only.

The Python half -- which routes call which resolver, and what they answer with
no ``X-Org-Id`` header -- is in
backend/tests/organizations/test_flag_off_single_user.py.

Not skipped by default. The database URL defaults to the local Supabase stack
that ``supabase start`` brings up, so a missing database fails this file loudly
rather than reporting green with nothing run. Override with
SUPABASE_INTEGRATION_DB_URL.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

DB_URL = os.environ.get(
    "SUPABASE_INTEGRATION_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(DB_URL, min_size=1, max_size=10)
    try:
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture
async def make_user(pool: asyncpg.Pool):
    """Create auth.users + public.users rows and clean them up afterwards.

    public.users.id is a FK to auth.users(id) (20260318000000_init.sql:4), so
    seeding public.users alone raises ForeignKeyViolationError.
    """
    created: list[uuid.UUID] = []

    async def _make(stripe_customer_id: str | None = None) -> tuple[uuid.UUID, str]:
        user_id = uuid.uuid4()
        email = f"rolling-{user_id.hex[:8]}@bindwave-test.local"
        await pool.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, email,
        )
        await pool.execute(
            "INSERT INTO public.users (id, email, stripe_customer_id) "
            "VALUES ($1, $2, $3)",
            user_id, email, stripe_customer_id,
        )
        created.append(user_id)
        return user_id, email

    yield _make

    for user_id in created:
        # organizations.created_by is ON DELETE SET NULL, so orgs outlive the
        # user and have to go first; jobs and memberships cascade.
        await pool.execute(
            "DELETE FROM public.organizations WHERE created_by = $1", user_id,
        )
        await pool.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _insert_job_like_an_old_replica(pool: asyncpg.Pool, user_id: uuid.UUID):
    """The INSERT an old replica (and agent/tools.py) emits: no org columns."""
    return await pool.fetchrow(
        """INSERT INTO public.jobs (id, user_id, tool, status)
           VALUES ($1, $2, 'bindcraft', 'pending')
           RETURNING organization_id, created_by_user_id""",
        uuid.uuid4(), user_id,
    )


# ---------------------------------------------------------------------------
# The rolling-deploy INSERT
# ---------------------------------------------------------------------------


async def test_old_replica_job_insert_fills_both_new_columns(pool, make_user):
    """An INSERT with neither new column succeeds, with both filled.

    Both columns are NOT NULL as of 20260605000002, so without the BEFORE
    INSERT trigger this INSERT raises NotNullViolationError.
    """
    user_id, _ = await make_user()
    personal_org = await pool.fetchval(
        "SELECT public.personal_org_for($1)", user_id,
    )

    row = await _insert_job_like_an_old_replica(pool, user_id)

    assert row["organization_id"] == personal_org
    assert row["created_by_user_id"] == user_id


async def test_old_replica_insert_works_before_any_org_exists(pool, make_user):
    """Same INSERT for a user an old replica signed up: no org row yet.

    The trigger creates the personal org on the way through, so the job lands
    instead of failing NOT NULL.
    """
    user_id, _ = await make_user()
    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations WHERE created_by = $1", user_id,
    ) == 0

    row = await _insert_job_like_an_old_replica(pool, user_id)

    assert row["organization_id"] is not None
    assert await pool.fetchval(
        "SELECT is_personal FROM public.organizations WHERE id = $1",
        row["organization_id"],
    ) is True


async def test_trigger_does_not_override_an_explicit_org(pool, make_user):
    """New replicas pass both columns; the trigger must leave them alone."""
    user_id, _ = await make_user()
    team_org = await pool.fetchval(
        "INSERT INTO public.organizations (name, is_personal, created_by) "
        "VALUES ('Team', FALSE, $1) RETURNING id",
        user_id,
    )
    other_user, _ = await make_user()

    row = await pool.fetchrow(
        """INSERT INTO public.jobs
               (id, user_id, tool, status, organization_id, created_by_user_id)
           VALUES ($1, $2, 'bindcraft', 'pending', $3, $4)
           RETURNING organization_id, created_by_user_id""",
        uuid.uuid4(), user_id, team_org, other_user,
    )

    assert row["organization_id"] == team_org
    assert row["created_by_user_id"] == other_user


async def test_job_insert_for_an_unknown_user_still_fails(pool):
    """Tolerance is not permissiveness: a bogus user_id is still rejected."""
    with pytest.raises(asyncpg.PostgresError):
        await _insert_job_like_an_old_replica(pool, uuid.uuid4())


# ---------------------------------------------------------------------------
# personal_org_for: idempotent, concurrency-safe, self-repairing
# ---------------------------------------------------------------------------


async def test_personal_org_for_is_idempotent(pool, make_user):
    """Repeated calls return the same org and never a second one."""
    user_id, email = await make_user()

    first = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)
    second = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)

    assert first == second
    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations "
        "WHERE created_by = $1 AND is_personal", user_id,
    ) == 1
    # Named like the 20260605000001 backfill, so a lazily created org is
    # indistinguishable from a migrated one in the switcher.
    assert await pool.fetchval(
        "SELECT name FROM public.organizations WHERE id = $1", first,
    ) == f"{email.split('@')[0]} (Personal)"


async def test_personal_org_for_is_concurrency_safe(pool, make_user):
    """Eight concurrent first-calls converge on one org.

    This is the rolling-deploy shape: a user with no personal org issues
    several requests at once and every one of them takes the lazy-create path.
    """
    user_id, _ = await make_user()

    async def _call():
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT public.personal_org_for($1)", user_id)

    results = await asyncio.gather(*[_call() for _ in range(8)])

    assert len(set(results)) == 1, results
    assert None not in results
    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations "
        "WHERE created_by = $1 AND is_personal", user_id,
    ) == 1


async def test_a_second_personal_org_is_rejected(pool, make_user):
    """The partial unique index is what makes the ON CONFLICT above safe.

    Two personal orgs for one user would let billing read stripe_customer_id
    off the empty one and mint a duplicate Stripe customer for an existing
    payer.
    """
    user_id, _ = await make_user()
    await pool.fetchval("SELECT public.personal_org_for($1)", user_id)

    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO public.organizations (name, is_personal, created_by) "
            "VALUES ('Second personal', TRUE, $1)",
            user_id,
        )


async def test_personal_org_for_restores_a_missing_owner_membership(pool, make_user):
    """An org with no owner membership is invisible to is_member_of().

    So the membership is re-asserted on every call, not only at creation.
    """
    user_id, _ = await make_user()
    org_id = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)
    await pool.execute(
        "DELETE FROM public.organization_memberships "
        "WHERE organization_id = $1 AND user_id = $2",
        org_id, user_id,
    )

    again = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)

    assert again == org_id
    assert await pool.fetchval(
        "SELECT role::text FROM public.organization_memberships "
        "WHERE organization_id = $1 AND user_id = $2",
        org_id, user_id,
    ) == "owner"


# ---------------------------------------------------------------------------
# Stripe resolution across the window
# ---------------------------------------------------------------------------


async def test_org_stripe_customer_falls_back_to_the_legacy_column(pool, make_user):
    """An old replica writes the new customer id to public.users only.

    Reading organizations.stripe_customer_id alone would return NULL here, and
    both metering call sites skip billing on NULL -- GPU time delivered and
    never charged.
    """
    user_id, _ = await make_user(stripe_customer_id="cus_written_by_old_replica")
    org_id = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)

    assert await pool.fetchval(
        "SELECT public.org_stripe_customer($1)", org_id,
    ) == "cus_written_by_old_replica"
    assert await pool.fetchval(
        "SELECT public.user_stripe_customer($1)", user_id,
    ) == "cus_written_by_old_replica"


async def test_org_stripe_customer_prefers_the_org_column(pool, make_user):
    """Once the org column is set it wins; the legacy column is only a fallback."""
    user_id, _ = await make_user(stripe_customer_id="cus_legacy")
    org_id = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)
    await pool.execute(
        "UPDATE public.organizations SET stripe_customer_id = 'cus_org' WHERE id = $1",
        org_id,
    )

    assert await pool.fetchval(
        "SELECT public.org_stripe_customer($1)", org_id,
    ) == "cus_org"
    assert await pool.fetchval(
        "SELECT public.user_stripe_customer($1)", user_id,
    ) == "cus_org"


async def test_a_team_org_never_inherits_a_personal_customer(pool, make_user):
    """The fallback is guarded by is_personal.

    Without that guard a brand-new team org with no card would be billed to its
    creator's personal Stripe customer -- someone else's card, silently.
    """
    user_id, _ = await make_user(stripe_customer_id="cus_personal_card")
    team_org = await pool.fetchval(
        "INSERT INTO public.organizations (name, is_personal, created_by) "
        "VALUES ('Team', FALSE, $1) RETURNING id",
        user_id,
    )

    assert await pool.fetchval(
        "SELECT public.org_stripe_customer($1)", team_org,
    ) is None


async def test_user_stripe_customer_is_one_value_for_a_multi_org_member(pool, make_user):
    """A user in several orgs resolves to exactly one customer.

    The shape this replaced joined organization_memberships, which returns one
    row PER MEMBERSHIP.
    """
    user_id, _ = await make_user(stripe_customer_id="cus_legacy")
    personal = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)
    await pool.execute(
        "UPDATE public.organizations SET stripe_customer_id = 'cus_personal' "
        "WHERE id = $1", personal,
    )
    for name, customer in (("Team A", "cus_team_a"), ("Team B", "cus_team_b")):
        team = await pool.fetchval(
            "INSERT INTO public.organizations "
            "(name, is_personal, created_by, stripe_customer_id) "
            "VALUES ($1, FALSE, $2, $3) RETURNING id",
            name, user_id, customer,
        )
        await pool.execute(
            "INSERT INTO public.organization_memberships "
            "(organization_id, user_id, role) VALUES ($1, $2, 'owner')",
            team, user_id,
        )

    rows = await pool.fetch(
        "SELECT public.user_stripe_customer($1) AS c", user_id,
    )

    assert len(rows) == 1
    assert rows[0]["c"] == "cus_personal"


async def test_deletion_cron_enumerates_each_user_once(pool, make_user):
    """The cron's own query, verbatim, returns one row for a multi-org user.

    One row per membership would hard-delete the user once per org and count
    each pass in the run total.
    """
    user_id, email = await make_user(stripe_customer_id="cus_legacy")
    personal = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)
    team = await pool.fetchval(
        "INSERT INTO public.organizations (name, is_personal, created_by) "
        "VALUES ('Team', FALSE, $1) RETURNING id",
        user_id,
    )
    await pool.execute(
        "INSERT INTO public.organization_memberships "
        "(organization_id, user_id, role) VALUES ($1, $2, 'owner')",
        team, user_id,
    )
    await pool.execute(
        "UPDATE public.users SET deletion_requested_at = now() - interval '40 days' "
        "WHERE id = $1", user_id,
    )
    assert personal is not None

    # Same statement as backend/worker/deletion_cron.py.
    rows = await pool.fetch(
        """SELECT u.id, u.email,
                  public.user_stripe_customer(u.id) AS stripe_customer_id
           FROM public.users u
           WHERE u.deletion_requested_at IS NOT NULL
             AND u.deletion_requested_at < NOW() - ($1 || ' days')::interval""",
        "30",
    )

    mine = [r for r in rows if r["id"] == user_id]
    assert len(mine) == 1
    assert mine[0]["email"] == email
    assert mine[0]["stripe_customer_id"] == "cus_legacy"


async def test_users_stripe_customer_id_column_still_exists(pool):
    """The drop-column migration is deliberately NOT in this PR.

    Merging runs ``supabase db push``, so shipping
    20260606000001_drop_users_stripe_customer_id.sql here would drop the column
    the two resolvers above fall back to, in the same deploy that still has old
    replicas writing to it. This test fails the day that migration sneaks in.
    """
    assert await pool.fetchval(
        """SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'users'
             AND column_name = 'stripe_customer_id'""",
    ) == 1


# ---------------------------------------------------------------------------
# protect_last_owner must not block a cascading delete
# ---------------------------------------------------------------------------


async def _sole_owner_org(pool: asyncpg.Pool, user_id: uuid.UUID) -> uuid.UUID:
    org_id = await pool.fetchval(
        "INSERT INTO public.organizations (name, is_personal, created_by) "
        "VALUES ('Cascade', FALSE, $1) RETURNING id",
        user_id,
    )
    await pool.execute(
        "INSERT INTO public.organization_memberships "
        "(organization_id, user_id, role) VALUES ($1, $2, 'owner')",
        org_id, user_id,
    )
    return org_id


async def test_deleting_an_org_is_not_blocked_by_its_last_owner(pool, make_user):
    """DELETE FROM public.organizations must cascade through its memberships.

    Postgres runs a referential ON DELETE CASCADE as a separate DELETE issued
    from the parent's internal AFTER trigger, so the membership's BEFORE DELETE
    trigger fires while the organizations row is already gone. An unguarded
    protect_last_owner counts one remaining owner, raises, and the org becomes
    undeletable -- which is what DELETE /organizations/{id} does.
    """
    user_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, user_id)

    await pool.execute("DELETE FROM public.organizations WHERE id = $1", org_id)

    assert await pool.fetchval(
        "SELECT count(*) FROM public.organization_memberships "
        "WHERE organization_id = $1", org_id,
    ) == 0


async def test_deleting_a_user_is_not_blocked_by_their_personal_org(pool, make_user):
    """The GDPR hard delete must survive the same cascade.

    backend/user/deletion.py deletes the auth.users row, which cascades to
    public.users and from there to organization_memberships. This is
    flag-independent: it runs for every existing single-tenant customer, whose
    personal org made them its sole owner. A personal org has exactly one
    member, so its owner leaving strands nobody and the trigger tolerates it.
    """
    user_id, _ = await make_user()
    personal_id = await pool.fetchval("SELECT public.personal_org_for($1)", user_id)

    try:
        await pool.execute("DELETE FROM auth.users WHERE id = $1", user_id)

        assert await pool.fetchval(
            "SELECT count(*) FROM public.users WHERE id = $1", user_id,
        ) == 0
        assert await pool.fetchval(
            "SELECT count(*) FROM public.organization_memberships WHERE user_id = $1",
            user_id,
        ) == 0
    finally:
        # organizations.created_by is ON DELETE SET NULL, so once the user row
        # is gone the make_user fixture's created_by cleanup matches nothing and
        # this org would outlive the test.
        await pool.execute(
            "DELETE FROM public.organizations WHERE id = $1", personal_id,
        )


async def test_a_raw_auth_delete_is_refused_while_a_shared_org_needs_an_owner(
    pool, make_user,
):
    """The tolerance stops at personal orgs, on purpose.

    A SHARED org left with zero owners has no satisfiable caller for any
    owner-only route or for the memberships_write_owners RLS policy, ever again,
    and no API can repair it. So a delete path that did not hand the org over
    first has to fail here rather than break that invariant quietly. The
    supported path is execute_hard_delete, covered below.
    """
    owner_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, owner_id)

    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await pool.execute("DELETE FROM auth.users WHERE id = $1", owner_id)

        # The failed statement rolled back, so the user is still there.
        assert await pool.fetchval(
            "SELECT count(*) FROM public.users WHERE id = $1", owner_id,
        ) == 1
    finally:
        await pool.execute("DELETE FROM public.organizations WHERE id = $1", org_id)


async def test_removing_the_last_owner_directly_is_still_refused(pool, make_user):
    """The cascade guard must not have turned the protection off.

    Both parent rows are present here, so the trigger has to raise 23514 exactly
    as it did before the guard.
    """
    user_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, user_id)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "DELETE FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            org_id, user_id,
        )

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "UPDATE public.organization_memberships "
            "SET role = 'scientist'::public.org_role "
            "WHERE organization_id = $1 AND user_id = $2",
            org_id, user_id,
        )


# ---------------------------------------------------------------------------
# One LIVE invitation per address, not one ever
# ---------------------------------------------------------------------------


async def _invite(pool: asyncpg.Pool, org_id, email: str, invited_by):
    return await pool.fetchval(
        """INSERT INTO public.organization_invitations
               (organization_id, email, role, token, invited_by, expires_at)
           VALUES ($1, $2, 'viewer', $3, $4, now() + interval '7 days')
           RETURNING id""",
        org_id, email, uuid.uuid4().hex, invited_by,
    )


async def test_a_second_live_invitation_to_one_address_is_rejected(pool, make_user):
    """organization_invitations_one_pending is what makes the 409 reachable."""
    user_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, user_id)
    await _invite(pool, org_id, "dupe@bindwave-test.local", user_id)

    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _invite(pool, org_id, "dupe@bindwave-test.local", user_id)

    # Same address, different case: the index is on lower(email), so this is
    # the same person and must collide too.
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _invite(pool, org_id, "DUPE@bindwave-test.local", user_id)


async def test_a_settled_invitation_does_not_block_a_re_invite(pool, make_user):
    """Revoked and accepted invitations must not lock the address out forever.

    The index is partial (accepted_at IS NULL AND revoked_at IS NULL). A plain
    UNIQUE (organization_id, email) would mean a member who leaves can never be
    invited back, and a mistyped role could never be corrected.
    """
    user_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, user_id)
    email = "returning@bindwave-test.local"

    first = await _invite(pool, org_id, email, user_id)
    await pool.execute(
        "UPDATE public.organization_invitations SET revoked_at = now() WHERE id = $1",
        first,
    )
    second = await _invite(pool, org_id, email, user_id)
    assert second != first

    await pool.execute(
        "UPDATE public.organization_invitations SET accepted_at = now() WHERE id = $1",
        second,
    )
    third = await _invite(pool, org_id, email, user_id)
    assert third not in (first, second)


# ---------------------------------------------------------------------------
# execute_hard_delete: the supported delete path hands organizations over first
# ---------------------------------------------------------------------------


async def _add_member(pool, org_id, user_id, role="scientist", created_at=None):
    await pool.execute(
        "INSERT INTO public.organization_memberships "
        "(organization_id, user_id, role, created_at) "
        "VALUES ($1, $2, $3::public.org_role, COALESCE($4, now()))",
        org_id, user_id, role, created_at,
    )


async def _hard_delete_then_cascade(pool, user_id, email):
    """Run the real execute_hard_delete, then the cascade it delegates.

    R2, Stripe and the GoTrue admin API are the three calls that leave the
    process, so they are the three that get patched. delete_auth_user is one of
    them, and the DELETE it issues is what makes the trigger fire -- so this
    runs that DELETE itself, in the same place in the sequence, unpatched
    against the real schema.
    """
    from user import deletion

    with patch.object(deletion, "get_db_pool", AsyncMock(return_value=pool)), \
         patch.object(deletion, "list_and_delete_user_objects", MagicMock(return_value=0)), \
         patch.object(deletion, "send_deletion_completed_email", AsyncMock()), \
         patch.object(deletion, "delete_auth_user", MagicMock()) as auth_delete:
        await deletion.execute_hard_delete(str(user_id), email, None)

    assert auth_delete.call_count == 1, (
        "execute_hard_delete returned before the auth delete, so the hand-over "
        "under test never ran"
    )
    await pool.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def test_hard_delete_promotes_the_longest_standing_member_of_a_shared_org(
    pool, make_user,
):
    """A team org the leaver solely owned keeps working, with a new owner.

    Without the hand-over the cascade takes the only owner's membership with it
    and leaves the org with members but no owner: every owner-only route 403s
    for everyone, and memberships_write_owners can never be satisfied again.
    The heir is the longest-standing remaining member, ordered by (created_at,
    user_id) so two members added at the same instant still resolve to one
    answer.
    """
    owner_id, owner_email = await make_user()
    senior_id, _ = await make_user()
    junior_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, owner_id)
    await _add_member(
        pool, org_id, junior_id,
        created_at=datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC),
    )
    await _add_member(
        pool, org_id, senior_id,
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    personal_id = await pool.fetchval("SELECT public.personal_org_for($1)", owner_id)
    job = await _insert_job_like_an_old_replica(pool, owner_id)
    assert job["organization_id"] == personal_id

    await pool.execute(
        "UPDATE public.users SET deletion_requested_at = now() WHERE id = $1", owner_id,
    )
    try:
        await _hard_delete_then_cascade(pool, owner_id, owner_email)

        assert await pool.fetchval(
            "SELECT count(*) FROM public.organizations WHERE id = $1", org_id,
        ) == 1, "the shared org must survive its owner"
        assert await pool.fetchval(
            "SELECT role::text FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            org_id, senior_id,
        ) == "owner"
        assert await pool.fetchval(
            "SELECT role::text FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            org_id, junior_id,
        ) == "scientist", "only one heir is promoted"
        # The personal org carries the email local part in its name, so leaving
        # it behind would mean PII surviving a GDPR erasure.
        assert await pool.fetchval(
            "SELECT count(*) FROM public.organizations WHERE id = $1", personal_id,
        ) == 0
        assert await pool.fetchval(
            "SELECT count(*) FROM public.jobs WHERE user_id = $1", owner_id,
        ) == 0
    finally:
        await pool.execute("DELETE FROM public.organizations WHERE id = $1", org_id)


async def test_hard_delete_removes_an_org_the_leaver_was_alone_in(pool, make_user):
    """Nothing to hand over: a one-member org goes with its only member.

    Promotion finds no heir here, so the org would otherwise be left with zero
    members AND zero owners -- unreachable, unrepairable, and still holding the
    org name. Both the personal org and an unshared team org take this path.
    """
    owner_id, owner_email = await make_user()
    org_id = await _sole_owner_org(pool, owner_id)
    personal_id = await pool.fetchval("SELECT public.personal_org_for($1)", owner_id)

    await pool.execute(
        "UPDATE public.users SET deletion_requested_at = now() WHERE id = $1", owner_id,
    )
    await _hard_delete_then_cascade(pool, owner_id, owner_email)

    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])",
        [org_id, personal_id],
    ) == 0


async def test_hard_delete_leaves_an_org_that_has_another_owner_alone(pool, make_user):
    """Two owners: the org needs neither a promotion nor a deletion."""
    leaver_id, leaver_email = await make_user()
    co_owner_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, leaver_id)
    await _add_member(pool, org_id, co_owner_id, role="owner")

    await pool.execute(
        "UPDATE public.users SET deletion_requested_at = now() WHERE id = $1", leaver_id,
    )
    try:
        await _hard_delete_then_cascade(pool, leaver_id, leaver_email)

        assert await pool.fetchval(
            "SELECT count(*) FROM public.organization_memberships "
            "WHERE organization_id = $1",
            org_id,
        ) == 1, "only the leaver's membership goes"
        assert await pool.fetchval(
            "SELECT role::text FROM public.organization_memberships "
            "WHERE organization_id = $1 AND user_id = $2",
            org_id, co_owner_id,
        ) == "owner"
    finally:
        await pool.execute("DELETE FROM public.organizations WHERE id = $1", org_id)


async def test_hard_delete_touches_no_org_when_the_user_cancelled(pool, make_user):
    """The hand-over sits inside the late FOR UPDATE guard's transaction.

    A user who cancels in the R2/Stripe window keeps their account, so they must
    also keep their organizations -- a promotion or an org delete here would be
    an unrecoverable side effect of a deletion that did not happen.
    """
    owner_id, owner_email = await make_user()
    member_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, owner_id)
    await _add_member(pool, org_id, member_id)

    # deletion_requested_at stays NULL, so the step-0 guard aborts immediately.
    from user import deletion

    with patch.object(deletion, "get_db_pool", AsyncMock(return_value=pool)), \
         patch.object(deletion, "list_and_delete_user_objects", MagicMock(return_value=0)), \
         patch.object(deletion, "send_deletion_completed_email", AsyncMock()), \
         patch.object(deletion, "delete_auth_user", MagicMock()) as auth_delete:
        await deletion.execute_hard_delete(str(owner_id), owner_email, None)

    assert auth_delete.call_count == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations WHERE id = $1", org_id,
    ) == 1
    assert await pool.fetchval(
        "SELECT role::text FROM public.organization_memberships "
        "WHERE organization_id = $1 AND user_id = $2",
        org_id, member_id,
    ) == "scientist"


async def test_an_expired_pending_invitation_blocks_the_index_until_swept(
    pool, make_user,
):
    """Why create_invitation sweeps: expiry is invisible to the partial index.

    expires_at > now() cannot join the predicate -- now() is not IMMUTABLE and a
    partial index predicate must be -- so a long-dead invitation still holds the
    one-live-invitation slot. The sweep in
    backend/organizations/router.py create_invitation is what frees it.
    """
    user_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, user_id)
    email = "expired@bindwave-test.local"

    stale = await _invite(pool, org_id, email, user_id)
    await pool.execute(
        "UPDATE public.organization_invitations "
        "SET expires_at = now() - interval '1 day' WHERE id = $1",
        stale,
    )

    # Still blocking, even though nobody could accept it.
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _invite(pool, org_id, email, user_id)

    # The exact statement create_invitation issues, including the case-folded
    # address match, frees the slot.
    swept = await pool.execute(
        """UPDATE public.organization_invitations
              SET revoked_at = now()
            WHERE organization_id = $1
              AND lower(email) = lower($2)
              AND accepted_at IS NULL
              AND revoked_at IS NULL
              AND expires_at <= now()""",
        org_id, "EXPIRED@bindwave-test.local",
    )
    assert swept == "UPDATE 1", swept

    fresh = await _invite(pool, org_id, email, user_id)
    assert fresh != stale
    # A live invitation is untouched by a second sweep, so re-inviting twice in
    # a row still collides.
    assert await pool.execute(
        """UPDATE public.organization_invitations
              SET revoked_at = now()
            WHERE organization_id = $1
              AND lower(email) = lower($2)
              AND accepted_at IS NULL
              AND revoked_at IS NULL
              AND expires_at <= now()""",
        org_id, email,
    ) == "UPDATE 0"
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _invite(pool, org_id, email, user_id)


async def test_a_personal_org_refuses_a_second_member(pool, make_user):
    """The invariant protect_last_owner's user-gone tolerance depends on.

    Without guard_personal_org_single_member, the invite + accept flow could put
    a second member into someone's personal org -- require_path_role('owner')
    passes, they own it -- and deleting that owner's account would then leave
    the second member in an org with zero owners that delete_organization
    refuses to remove (backend/organizations/router.py:195-199).
    """
    owner_id, _ = await make_user()
    stranger_id, _ = await make_user()
    personal_id = await pool.fetchval("SELECT public.personal_org_for($1)", owner_id)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _add_member(pool, personal_id, stranger_id)

    # The owner's own membership is re-asserted on every call, and must not trip
    # over the guard.
    assert await pool.fetchval(
        "SELECT public.personal_org_for($1)", owner_id,
    ) == personal_id
    # A shared org is unaffected.
    shared_id = await _sole_owner_org(pool, owner_id)
    await _add_member(pool, shared_id, stranger_id)
    await pool.execute("DELETE FROM public.organizations WHERE id = $1", shared_id)


async def test_hard_delete_keeps_a_removed_members_jobs_out_of_the_cascade(
    pool, make_user,
):
    """A job owned by someone else must survive their old org being deleted.

    remove_member deletes the membership row and nothing else
    (backend/organizations/router.py:311-315), so an org can hold jobs whose
    owner is no longer a member of it. When the last remaining member is then
    hard-deleted, execute_hard_delete drops that org -- and
    jobs.organization_id is ON DELETE CASCADE
    (20260605000001_organizations.sql:213), so without the re-parent those jobs
    would be destroyed for a user who was never deleted.
    """
    leaver_id, leaver_email = await make_user()
    stayer_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, leaver_id)
    await _add_member(pool, org_id, stayer_id)

    job_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.jobs (id, user_id, tool, status, organization_id) "
        "VALUES ($1, $2, 'bindcraft', 'pending', $3)",
        job_id, stayer_id, org_id,
    )
    # The org owner removes them from the team; the job stays where it is.
    await pool.execute(
        "DELETE FROM public.organization_memberships "
        "WHERE organization_id = $1 AND user_id = $2",
        org_id, stayer_id,
    )
    assert await pool.fetchval(
        "SELECT organization_id FROM public.jobs WHERE id = $1", job_id,
    ) == org_id

    await pool.execute(
        "UPDATE public.users SET deletion_requested_at = now() WHERE id = $1", leaver_id,
    )
    await _hard_delete_then_cascade(pool, leaver_id, leaver_email)

    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations WHERE id = $1", org_id,
    ) == 0, "the org had one member left and goes with them"
    assert await pool.fetchval(
        "SELECT organization_id FROM public.jobs WHERE id = $1", job_id,
    ) == await pool.fetchval("SELECT public.personal_org_for($1)", stayer_id)


async def _wait_until_blocked(pool, needle: str, timeout: float = 15.0):
    """Wait until another session is stuck on a lock inside a statement.

    Makes the interleaving below deterministic without a sleep: the joiner's
    uncommitted INSERT holds FOR KEY SHARE on the organizations row, which is
    what both the locking SELECT and the DELETE have to wait for. The needle
    travels as a parameter, so this query cannot match itself.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await pool.fetchval(
            """SELECT count(*) FROM pg_stat_activity
                WHERE wait_event_type = 'Lock'
                  AND query LIKE '%' || $1 || '%'""",
            needle,
        ):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"nothing blocked on {needle!r} within {timeout}s")


async def test_hard_delete_keeps_an_org_that_gains_a_member_mid_delete(
    pool, make_user,
):
    """A sole-member org that gains a member mid-delete must survive.

    execute_hard_delete runs at READ COMMITTED and re-parents other people's
    jobs between deciding which orgs are doomed and deleting them, so an
    invitation accepted in that window commits a membership the first snapshot
    cannot see. Delete by id alone and the org goes anyway: protect_last_owner
    tolerates the membership cascade because the parent row is already gone
    (migration 20260605000003 section 5), so the member who just joined loses
    the org and every job in it with nothing raised anywhere.

    Guarded, the org survives and the auth delete is refused instead -- a shared
    org with no owner is what section 5 will not tolerate -- and the cron's next
    pass promotes the new member and finishes.

    The interleaving is deterministic rather than timed: the joiner's INSERT is
    held open, and its foreign key's FOR KEY SHARE lock on the organizations row
    is what the delete blocks on.
    """
    leaver_id, leaver_email = await make_user()
    joiner_id, _ = await make_user()
    org_id = await _sole_owner_org(pool, leaver_id)
    await pool.execute(
        "UPDATE public.users SET deletion_requested_at = now() WHERE id = $1",
        leaver_id,
    )

    joiner_conn = await pool.acquire()
    committed = False
    try:
        tx = joiner_conn.transaction()
        await tx.start()
        await joiner_conn.execute(
            "INSERT INTO public.organization_memberships "
            "(organization_id, user_id, role) VALUES ($1, $2, 'scientist')",
            org_id, joiner_id,
        )
        deleter = asyncio.create_task(
            _hard_delete_then_cascade(pool, leaver_id, leaver_email),
        )
        try:
            await _wait_until_blocked(pool, "public.organization_memberships rival")
            await tx.commit()
            committed = True
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await asyncio.wait_for(deleter, timeout=30)
        finally:
            if not deleter.done():
                deleter.cancel()
            if not committed:
                await tx.rollback()
    finally:
        await pool.release(joiner_conn)

    assert await pool.fetchval(
        "SELECT count(*) FROM public.organizations WHERE id = $1", org_id,
    ) == 1, "the org gained a member before the delete and must survive it"
    assert await pool.fetchval(
        "SELECT count(*) FROM public.organization_memberships "
        "WHERE organization_id = $1 AND user_id = $2",
        org_id, joiner_id,
    ) == 1, "the new member's own row must not have gone with a cascade"

    # The cron retries, and this time the new member is visible from the start.
    await _hard_delete_then_cascade(pool, leaver_id, leaver_email)
    assert await pool.fetchval(
        "SELECT role::text FROM public.organization_memberships "
        "WHERE organization_id = $1 AND user_id = $2",
        org_id, joiner_id,
    ) == "owner", "the retry promotes the member it could not see the first time"
