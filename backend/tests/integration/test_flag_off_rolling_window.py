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
import os
import uuid

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
