"""Hazard 3: with the flag OFF, an existing single user is unaffected by the merge.

Phase 12 cut billing, jobs and ``/user/usage`` over to org scope
unconditionally. ``settings.organizations_enabled`` gates only the
orgs/invitations router mount and the ``/health`` field (backend/main.py:128,
backend/main.py:179), so "flag off" is NOT the pre-Phase-12 code path: every one
of those routes now resolves an organization before it does anything. These
tests pin the flag-off behaviour of that resolution.

  * The orgs router is not mounted, so a client has no endpoint to learn an org
    id from and sends no ``X-Org-Id`` header.
  * With no header, ``get_active_org`` resolves the caller's personal org as
    ``owner`` (backend/auth/org_dependencies.py:64-69), so listing jobs,
    launching a job, adding a card, checking out and reading usage all still
    answer 2xx for a user who has never seen an org UI.
  * The deletion cron and the GDPR export resolve the Stripe customer as a
    scalar, so a user who also belongs to a team org is processed once rather
    than once per membership.

"Post-merge" here means: both org migrations applied, the drop-column migration
deliberately absent from this PR, the flag off, and no Stripe metadata stamped.
All of the above is Python, so it is proven here with mocks. The SQL half --
what ``public.personal_org_for`` and the two customer resolvers actually do
against Postgres, including the rolling-deploy window where an old replica
inserts a job with no ``created_by_user_id`` -- is proven in
tests/integration/test_flag_off_rolling_window.py.

Only ``get_current_user`` is overridden. ``get_active_org`` runs for real here:
it is the code under test.
"""

from __future__ import annotations

import datetime
import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auth.dependencies import get_current_user
from config import settings
from httpx import ASGITransport, AsyncClient
from main import app

USER_ID = "11111111-1111-4111-8111-111111111111"
PERSONAL_ORG_ID = "22222222-2222-4222-8222-222222222222"
JOB_ID = "33333333-3333-4333-8333-333333333333"
LEGACY_CUSTOMER = "cus_legacy_single_user"

# Shape of public.jobs.job_spec. The launch route parses it through
# agent.jobspec.JobSpec, which requires every field below.
JOB_SPEC = {
    "tool": "bindcraft",
    "target_pdb_path": f"users/{USER_ID}/jobs/{JOB_ID}/inputs/target.cif",
    "target_chain": "A",
    "hotspot_residues": [45, 48],
    "parameters": {"num_designs": 4},
    "validation_results": [
        {"check_name": "pdb_quality", "status": "pass", "message": "OK"},
    ],
    "estimated_cost_usd": 4.2,
    "rationale": "BindCraft suits this target.",
}


@pytest.fixture(autouse=True)
def _flag_is_off():
    """Every test in this module describes the flag-OFF landing.

    Stated as a precondition rather than assumed: with the flag on, the orgs
    router mounts and a 404 assertion below would be measuring something else.
    """
    assert not settings.organizations_enabled, (
        "this module proves the flag-OFF landing; "
        "run it with ORGANIZATIONS_ENABLED unset"
    )


def _override_user(user_id: str = USER_ID):
    async def _dep():
        return user_id
    return _dep


def _ctx(conn):
    """Wrap a mock connection as an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _pool(
    *,
    job_rows: list[dict] | None = None,
    stripe_customer: str | None = LEGACY_CUSTOMER,
    usage_row: dict | None = None,
    profile_row: dict | None = None,
    delete_rows: list[dict] | None = None,
):
    """A query-sniffing pool standing in for the post-merge database.

    Answers the way that database answers for a user who existed before Phase
    12: one personal org, an owner membership on it, and a Stripe customer that
    the resolver finds. Which column the resolver found it in is a SQL question
    and is not decided here.

    The returned pool carries ``pool.queries`` (every statement, in order) so a
    test can assert on the SQL the route emitted.
    """
    queries: list[str] = []

    async def _fetchval(query, *args):
        queries.append(query)
        if "public.personal_org_for" in query:
            return PERSONAL_ORG_ID
        if "public.org_stripe_customer" in query:
            return stripe_customer
        return None

    async def _fetchrow(query, *args):
        queries.append(query)
        if "SELECT job_spec FROM public.jobs" in query:
            return {"job_spec": json.dumps(JOB_SPEC)}
        if "SELECT id, name FROM public.organizations" in query:
            return {"id": PERSONAL_ORG_ID, "name": "leo (Personal)"}
        if "m.role = 'owner'" in query:
            return {"email": "leo@example.com"}
        if "COUNT(*) AS job_count" in query:
            return usage_row
        if "public.user_stripe_customer" in query:
            return profile_row
        return None

    async def _fetch(query, *args):
        queries.append(query)
        if "FROM public.jobs j" in query:
            return job_rows if job_rows is not None else []
        if "public.user_stripe_customer" in query:
            return delete_rows if delete_rows is not None else []
        return []

    async def _execute(query, *args):
        queries.append(query)
        return "UPDATE 1"

    conn = AsyncMock()
    conn.fetchval = _fetchval
    conn.fetchrow = _fetchrow
    conn.fetch = _fetch
    conn.execute = _execute

    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=lambda: _ctx(conn))
    pool.fetchval = _fetchval
    pool.fetchrow = _fetchrow
    pool.fetch = _fetch
    pool.execute = _execute
    pool.queries = queries
    return pool


async def _request(method: str, url: str, *, json_body=None):
    """Issue one request with get_current_user overridden and no X-Org-Id."""
    app.dependency_overrides[get_current_user] = _override_user()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"access_token": "fake-token"},
        ) as client:
            return await client.request(method, url, json=json_body)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# The premise: no org UI is reachable, so no client can send X-Org-Id
# ---------------------------------------------------------------------------


async def test_orgs_router_is_not_mounted():
    """Flag off -> /organizations/* does not exist.

    This is what makes the header-less path the ONLY path post-merge, and
    therefore what the rest of this module is about.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/organizations/mine")
    assert resp.status_code == 404


async def test_health_reports_the_flag_off():
    """/health still advertises organizations_enabled=false after the merge.

    The status code is deliberately not asserted: /health probes the live DB and
    Redis (main.py), so it is 503 on a machine without them. The flag field is
    not health-gated, and it is what the rollout runbook reads.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.json()["organizations_enabled"] is False


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def test_list_jobs_with_no_org_header():
    """GET /jobs/ answers 200 and scopes to the resolved personal org.

    Includes a row with ``created_by_user_id`` NULL: that is exactly what an
    old replica writes during the rolling deploy, and the serialiser must not
    raise on it.
    """
    created = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=datetime.UTC)
    rows = [
        {
            "id": JOB_ID, "tool": "bindcraft", "status": "complete",
            "name": "run-1", "created_at": created, "completed_at": created,
            "gpu_cost_usd": 4.25, "candidate_count": "8",
            "session_id": None,
            "created_by_user_id": None,   # inserted by an old replica
            "created_by_email": None,
        },
    ]
    pool = _pool(job_rows=rows)
    with patch("jobs.router.get_db_pool", return_value=pool), \
         patch("auth.org_dependencies.get_db_pool", return_value=pool):
        resp = await _request("GET", "/jobs/")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["created_by_user_id"] is None
    assert body["jobs"][0]["gpu_cost_usd"] == 4.25
    assert any("public.personal_org_for" in q for q in pool.queries), pool.queries


async def test_launch_job_with_no_org_header():
    """POST /jobs/launch answers 200 and stamps the resolved personal org.

    require_role("owner", "scientist") gates this route, so it also proves the
    fallback's ``owner`` role is accepted here.
    """
    pool = _pool()
    captured = {}

    async def _fake_launch(**kwargs):
        captured.update(kwargs)

    with patch("jobs.router.get_db_pool", return_value=pool), \
         patch("auth.org_dependencies.get_db_pool", return_value=pool), \
         patch.object(settings, "stripe_secret_key", "sk_test_flag_off"), \
         patch("jobs.router.get_or_create_customer",
               new=AsyncMock(return_value=LEGACY_CUSTOMER)) as mock_customer, \
         patch("jobs.router.check_payment_method", return_value=True), \
         patch("jobs.router.launch_job", new=_fake_launch):
        resp = await _request("POST", "/jobs/launch", json_body={"job_id": JOB_ID})

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"
    # Billing is resolved against the personal org, not the user row.
    assert mock_customer.await_args.kwargs["org_id"] == PERSONAL_ORG_ID
    # And the audit column is filled, which is what keeps the job visible in
    # the scientist branch of /user/usage later.
    assert captured["organization_id"] == PERSONAL_ORG_ID
    assert captured["created_by_user_id"] == USER_ID


# ---------------------------------------------------------------------------
# Billing: adding a card and checking out
# ---------------------------------------------------------------------------


async def test_checkout_session_with_no_org_header():
    """POST /billing/checkout-session answers 200 for a header-less caller.

    This route is ``require_role("owner")``. If the no-header fallback returned
    any other role, every existing customer would get 403 the moment they tried
    to add a card -- with no org UI to fix it from, because the orgs router is
    not mounted.
    """
    pool = _pool()
    with patch("billing.router.get_db_pool", return_value=pool), \
         patch("auth.org_dependencies.get_db_pool", return_value=pool), \
         patch.object(settings, "stripe_secret_key", "sk_test_flag_off"), \
         patch("billing.router.get_or_create_customer",
               new=AsyncMock(return_value=LEGACY_CUSTOMER)), \
         patch("billing.router.create_setup_session",
               return_value="https://checkout.stripe.test/s/abc") as mock_setup:
        resp = await _request(
            "POST", "/billing/checkout-session",
            json_body={"return_url": "https://app.bindwave.com/settings"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == "https://checkout.stripe.test/s/abc"
    # The card is collected for the customer the resolver returned, so it lands
    # on the same Stripe customer the user already had.
    assert mock_setup.call_args.kwargs["stripe_customer_id"] == LEGACY_CUSTOMER


async def test_payment_status_with_no_org_header():
    """GET /billing/payment-status answers 200, not 400/403."""
    pool = _pool()
    with patch("billing.router.get_db_pool", return_value=pool), \
         patch("auth.org_dependencies.get_db_pool", return_value=pool), \
         patch.object(settings, "stripe_secret_key", "sk_test_flag_off"), \
         patch("billing.router.get_or_create_customer",
               new=AsyncMock(return_value=LEGACY_CUSTOMER)), \
         patch("billing.router.check_payment_method", return_value=True):
        resp = await _request("GET", "/billing/payment-status")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"has_payment_method": True}


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


async def test_usage_with_no_org_header_takes_the_owner_branch():
    """GET /user/usage answers 200 and does not filter by created_by_user_id.

    The fallback role is ``owner``, so the aggregate covers the whole personal
    org. Filtering by ``created_by_user_id`` would hide every job an old
    replica inserted without that column -- the user's own spend, missing from
    their own usage page.
    """
    usage_row = {
        "job_count": 3,
        "total_spend": 12.5,
        "period_start": datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC),
    }
    pool = _pool(usage_row=usage_row)
    with patch("user.router.get_db_pool", return_value=pool), \
         patch("auth.org_dependencies.get_db_pool", return_value=pool):
        resp = await _request("GET", "/user/usage")

    assert resp.status_code == 200, resp.text
    assert resp.json()["job_count"] == 3
    assert resp.json()["total_spend_usd"] == 12.5
    assert not any("created_by_user_id = $2" in q for q in pool.queries), pool.queries


# ---------------------------------------------------------------------------
# Deletion cron and GDPR export: one row per user, not one per membership
# ---------------------------------------------------------------------------


async def test_deletion_cron_processes_each_user_once():
    """The cron resolves the customer as a scalar, so memberships cannot fan out.

    The pre-fix shape joined organization_memberships, which returns one row
    per membership: a user in two team orgs would be hard-deleted three times
    in one run and counted three times in the return value.
    """
    from worker.deletion_cron import process_pending_deletions

    pool = _pool(delete_rows=[
        {"id": USER_ID, "email": "leo@example.com",
         "stripe_customer_id": LEGACY_CUSTOMER},
    ])
    with patch("worker.deletion_cron.get_db_pool", return_value=pool), \
         patch("worker.deletion_cron.execute_hard_delete",
               new=AsyncMock()) as mock_delete:
        deleted = await process_pending_deletions()

    assert deleted == 1
    assert mock_delete.await_count == 1
    assert mock_delete.await_args.args == (USER_ID, "leo@example.com", LEGACY_CUSTOMER)
    enumeration = [q for q in pool.queries if "public.user_stripe_customer" in q]
    assert enumeration, pool.queries
    assert "organization_memberships" not in enumeration[0]


async def test_gdpr_export_still_carries_the_stripe_customer():
    """The export ZIP's profile.json keeps the same stripe_customer_id key.

    GDPR export shape is a promise to the user; the Phase 12 column move must
    not silently drop the field. Asserted on the bytes actually uploaded.
    """
    from user.export import build_and_deliver_export

    profile = {
        "id": USER_ID, "email": "leo@example.com", "display_name": "Leo",
        "created_at": datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
        "tos_version": "2026-04-23",
        "tos_accepted_at": datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
        "data_retention_days": 90, "deletion_requested_at": None,
        "last_export_requested_at": None, "notification_preferences": "{}",
        "stripe_customer_id": LEGACY_CUSTOMER,
    }
    pool = _pool(profile_row=profile)
    s3 = MagicMock()
    with patch("user.export.get_db_pool", return_value=pool), \
         patch("user.export.get_s3_client", return_value=s3), \
         patch("user.export.generate_presigned_get_url",
               return_value="https://r2.test/export.zip"), \
         patch("user.export.send_export_ready_email", new=AsyncMock()):
        await build_and_deliver_export(USER_ID, "leo@example.com")

    body = s3.put_object.call_args.kwargs["Body"]
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        exported = json.loads(zf.read("profile.json"))
    assert exported["stripe_customer_id"] == LEGACY_CUSTOMER
    lookups = [q for q in pool.queries if "public.user_stripe_customer" in q]
    assert lookups, pool.queries
    assert "organization_memberships" not in lookups[0]
