"""Tests for delivering the designs a failed run already saved.

PRs #33-#35 made the containers upload each design as it finishes, so a run
that times out, crashes or is killed leaves its finished designs in Storage
instead of losing them to the ``rmtree`` in its ``finally``. Nothing served
them: ``get_job`` read ``job_candidates``, which is written only under the
webhook's ``complete`` branch, and the bulk download 404'd on any status other
than ``complete``.

These pin the two API surfaces a customer of a partial run reaches:

    * ``GET /jobs/{id}`` lists the saved designs as candidates and flags the
      set ``partial``.
    * ``GET /jobs/{id}/download`` archives them under a ``_designs_partial``
      filename, and 404s when the run banked nothing.

Both cover ``cancelled`` as well as ``failed``: a cancel bills the GPU seconds
already burned (``jobs/service.py::cancel_job_by_id``), and the job page shows
a download button for it (``frontend/src/pages/JobPage.tsx``, the
``showSummary`` branch), so its designs need the same route out.

What a listing contains is pinned in ``tests/storage/test_job_design_keys.py``;
here the lister is patched, so these cover the routing and the response shape
only.
"""

import io
import json
import random
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from auth.dependencies import get_current_user
from auth.org_dependencies import get_active_org
from httpx import ASGITransport, AsyncClient
from jobs.router import _ChunkSink
from main import app

JOB_ID = "job-partial"
LAUNCHER_ID = "u-launcher"
PREFIX = f"users/{LAUNCHER_ID}/jobs/{JOB_ID}/outputs/"


def _override_user(user_id: str = "u1"):
    async def _dep():
        return user_id
    return _dep


def _override_active_org(role: str = "scientist", org_id: str = "org-personal"):
    async def _dep():
        return (org_id, role)
    return _dep


def _job_row(status: str, **overrides):
    """A ``public.jobs`` row as asyncpg hands it to ``get_job``.

    ``user_id`` is the original launcher's, which is deliberately not the
    caller's: outputs are keyed to whoever started the run (Phase 12 org
    scoping), so a row whose user_id differs from the authenticated user is the
    normal case of a teammate reading a colleague's job.
    """
    row = {
        "id": JOB_ID,
        "user_id": LAUNCHER_ID,
        "status": status,
        "stage": "design",
        "job_spec": json.dumps({"tool": "bindcraft"}),
        "gpu_seconds": 1200,
        "gpu_cost_usd": None,
        "started_at": datetime(2026, 10, 8, tzinfo=UTC),
        "completed_at": None,
        "error_category": "Timed out",
        "results": None,
        "created_at": datetime(2026, 10, 8, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _make_pool(row, cand_rows=()):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=list(cand_rows))
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def _get_job(pool, signer=None, **lister):
    """GET /jobs/{JOB_ID} with the auth deps overridden.

    ``lister`` is passed straight to the ``list_job_design_keys`` patch, so a
    caller picks either ``return_value=`` or ``side_effect=``. ``signer``
    replaces the stub presigner, for the one test that needs signing to fail.
    """
    app.dependency_overrides[get_current_user] = _override_user()
    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        with (
            patch("jobs.router.get_db_pool", return_value=pool),
            patch(
                "jobs.router.generate_presigned_get_url",
                side_effect=signer or (lambda k: f"https://signed/{k}"),
            ),
            patch("jobs.router.list_job_design_keys", **lister) as mock_list,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    f"/jobs/{JOB_ID}", cookies={"access_token": "fake-token"}
                )
            return response, mock_list
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)


# ---------------------------------------------------------------------------
# GET /jobs/{id}
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_failed_job_serves_the_designs_it_saved_and_flags_them_partial():
    """The whole point of the change: a failed run's designs are reachable.

    Storage is read under the launcher's own prefix, every key becomes a
    candidate with a presigned URL, and ``partial`` tells the caller this is
    what survived rather than the full set.
    """
    pool = _make_pool(_job_row("failed"))

    response, mock_list = await _get_job(
        pool, return_value=[PREFIX + "design_1.pdb", PREFIX + "design_2.pdb"]
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["partial"] is True
    assert [c["pdb_key"] for c in body["candidates"]] == [
        PREFIX + "design_1.pdb",
        PREFIX + "design_2.pdb",
    ]
    assert [c["rank"] for c in body["candidates"]] == [1, 2]
    assert [c["download_url"] for c in body["candidates"]] == [
        f"https://signed/{PREFIX}design_1.pdb",
        f"https://signed/{PREFIX}design_2.pdb",
    ]

    # No per-design metrics exist for a run that never reached its collect
    # step, so the scores are empty rather than guessed
    assert all(c["scores"] == {} for c in body["candidates"])

    # Read under the launcher's user_id, not the caller's ("u1")
    mock_list.assert_called_once_with(LAUNCHER_ID, JOB_ID)


@pytest.mark.anyio
async def test_a_failed_job_that_saved_nothing_is_not_partial():
    """``partial`` means "some designs, not all", so an empty set is not partial.

    A run killed before its first upload has nothing to deliver, and the caller
    must not be sent looking for it.
    """
    pool = _make_pool(_job_row("failed"))

    response, _ = await _get_job(pool, return_value=[])

    assert response.status_code == 200
    assert response.json()["candidates"] == []
    assert response.json()["partial"] is False


@pytest.mark.anyio
async def test_a_complete_job_reads_its_candidates_from_the_database_not_storage():
    """The completed path is untouched: DB rows, real scores, not partial.

    A complete run has ``job_candidates`` rows carrying the scores its collect
    step computed. Asserting Storage is never consulted keeps the new branch
    from swallowing the ranked, scored list a finished run is supposed to
    return.
    """
    cand_rows = [
        {
            "rank": 1,
            "pdb_key": PREFIX + "design_7.pdb",
            "scores": json.dumps({"iptm": 0.82}),
        },
    ]
    pool = _make_pool(_job_row("complete", error_category=None), cand_rows)

    response, mock_list = await _get_job(pool, return_value=[PREFIX + "ignored.pdb"])

    assert response.status_code == 200
    body = response.json()
    assert body["partial"] is False
    assert [c["pdb_key"] for c in body["candidates"]] == [PREFIX + "design_7.pdb"]
    assert body["candidates"][0]["scores"] == {"iptm": 0.82}
    mock_list.assert_not_called()


@pytest.mark.anyio
async def test_an_unreadable_bucket_still_returns_the_job():
    """A Storage outage costs the design list, not the whole job read.

    The caller usually wants the status and the error category as much as the
    designs, and a 500 here would deny both. The swallowed exception is handed
    to Sentry explicitly, because returning a 200 hides it from
    FastApiIntegration's own capture.
    """
    pool = _make_pool(_job_row("failed"))

    with patch("jobs.router.sentry_sdk.capture_exception") as mock_capture:
        response, _ = await _get_job(pool, side_effect=RuntimeError("bucket is down"))

    assert response.status_code == 200
    assert response.json()["candidates"] == []
    assert response.json()["partial"] is False
    mock_capture.assert_called_once()


@pytest.mark.anyio
async def test_a_signing_failure_degrades_the_same_way_as_an_unreadable_bucket():
    """Signing sits inside the same try as the listing, so it fails the same way.

    Both run in one thread hop (``_list_and_sign``), so a fault that lets the
    listing through but breaks ``generate_presigned_get_url`` -- bad
    credentials, a bad endpoint -- costs the design list rather than the job
    read. Worth pinning because the signing used to sit outside that ``try``,
    where the same fault would have 500ed the whole request and denied the
    caller the status and error category too.
    """
    pool = _make_pool(_job_row("failed"))

    with patch("jobs.router.sentry_sdk.capture_exception") as mock_capture:
        response, _ = await _get_job(
            pool,
            signer=RuntimeError("no credentials"),
            return_value=[PREFIX + "design_1.pdb"],
        )

    assert response.status_code == 200
    assert response.json()["candidates"] == []
    assert response.json()["partial"] is False
    mock_capture.assert_called_once()


@pytest.mark.anyio
async def test_a_cancelled_job_serves_its_designs_too():
    """A cancel bills the GPU seconds burned, so it owes the output.

    ``jobs/service.py::cancel_job_by_id`` meters the elapsed seconds whenever
    ``gpu_seconds > 0``, with no check that anything was delivered, so a
    cancelled run whose designs stayed unreachable was money taken for
    nothing the customer could download.
    """
    pool = _make_pool(_job_row("cancelled"))

    response, mock_list = await _get_job(pool, return_value=[PREFIX + "design_1.pdb"])

    assert response.status_code == 200
    assert response.json()["partial"] is True
    assert [c["pdb_key"] for c in response.json()["candidates"]] == [
        PREFIX + "design_1.pdb"
    ]
    mock_list.assert_called_once_with(LAUNCHER_ID, JOB_ID)


# ---------------------------------------------------------------------------
# GET /jobs/{id}/download
# ---------------------------------------------------------------------------

def _s3_mock(*keys, body=b"ATOM"):
    """An S3 client mock serving ``keys`` under the job's output prefix.

    One key per page, so a listing that reads only the first page -- the
    1000-key cap of a bare ``list_objects_v2`` -- loses every key but one.
    ``body`` is what every object returns.
    """
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": k}]} for k in keys
    ]
    s3.get_object = MagicMock(
        side_effect=lambda Bucket, Key: {"Body": io.BytesIO(body)}
    )
    return s3


async def _download(status, *keys, body=b"ATOM"):
    pool = _make_pool(_job_row(status))
    app.dependency_overrides[get_current_user] = _override_user()
    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        with (
            patch("jobs.router.get_db_pool", return_value=pool),
            patch(
                "jobs.router.get_s3_client",
                return_value=_s3_mock(*keys, body=body),
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(
                    f"/jobs/{JOB_ID}/download", cookies={"access_token": "fake-token"}
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)


@pytest.mark.anyio
async def test_the_bulk_download_of_a_failed_job_archives_what_was_saved():
    """A failed run's archive is served, and its name says the set is partial.

    Before this branch the gate was ``status != "complete"`` -> 404, so the
    designs sat in the bucket with no route to them.
    """
    response = await _download(
        "failed", PREFIX + "design_1.pdb", PREFIX + "metrics.csv"
    )

    assert response.status_code == 200
    assert "application/zip" in response.headers["content-type"]
    assert response.headers["content-disposition"] == (
        f'attachment; filename="job_{JOB_ID}_designs_partial.zip"'
    )

    # The CSVs sharing the prefix still travel with the designs, as they do for
    # a complete run
    assert set(zipfile.ZipFile(io.BytesIO(response.content)).namelist()) == {
        "design_1.pdb",
        "metrics.csv",
    }


@pytest.mark.anyio
async def test_the_bulk_download_of_a_failed_job_with_no_designs_is_a_404():
    """An archive of nothing reads as "your designs were lost"; 404 does not.

    The DESIGN_SUFFIXES check in download_all_designs is why a CSV-only
    prefix -- a BindCraft run that wrote its metrics and then died -- still
    404s.
    """
    response = await _download("failed", PREFIX + "metrics.csv")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_the_bulk_download_of_a_cancelled_job_is_served_not_404ed():
    """The job page already shows this button; before, it 404d.

    ``frontend/src/pages/JobPage.tsx`` renders ``RunSummaryCard`` -- and so its
    "Download all designs" button -- for a cancelled run, while the endpoint
    admitted ``complete`` only.
    """
    response = await _download("cancelled", PREFIX + "design_1.pdb")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="job_{JOB_ID}_designs_partial.zip"'
    )


@pytest.mark.anyio
async def test_a_partial_run_that_saved_only_cifs_is_still_served():
    """The ``.pdb`` guard would 404 a BoltzGen or PxDesign run of CIFs.

    Those two name each upload after the file their pipeline produced
    (``docker/boltzgen/run_pipeline.py:2043-2044``), so the guard that
    distinguishes "designs were banked" from "only CSVs landed" has to accept
    every suffix a design can carry.
    """
    response = await _download("failed", PREFIX + "design_001.cif")

    assert response.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == [
        "design_001.cif"
    ]


@pytest.mark.anyio
async def test_a_complete_job_keeps_its_plain_designs_filename():
    """Nothing about the finished path moves: no ``_partial`` in the name."""
    response = await _download("complete", PREFIX + "design_1.pdb")

    assert response.status_code == 200
    # Spelled in full rather than as a "partial" substring check, because
    # JOB_ID itself contains that word
    assert response.headers["content-disposition"] == (
        f'attachment; filename="job_{JOB_ID}_designs.zip"'
    )


@pytest.mark.anyio
async def test_an_archive_larger_than_one_read_chunk_arrives_whole():
    """The response reads the archive back in chunks; every chunk is served.

    The body is deliberately incompressible and bigger than one read, so a
    chunk loop that stopped after the first would hand back a truncated ZIP --
    the same silent data loss this module exists to close, one layer out.
    """
    body = random.Random(0).randbytes(200_000)

    response = await _download("failed", PREFIX + "design_1.pdb", body=body)

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.read("design_1.pdb") == body


@pytest.mark.anyio
async def test_the_archive_is_served_as_it_is_built():
    """Bytes reach the client before the last object has been fetched.

    The archive used to be assembled whole in a ``BytesIO``. Paginating the
    listing removed the 1000-key cap that had bounded that, leaving peak
    memory a function of ``num_designs`` -- 60000 for boltzgen
    (``agent/wizard.py:139``) -- so one download of a large run could take the
    process down and every other request with it. Streaming keeps one object
    resident, and the observable consequence is ordering: a body message is
    sent while objects are still being fetched.

    Driven through ASGI rather than the test client because httpx's
    ``ASGITransport`` buffers the whole response before returning it, which
    hides exactly the property under test.
    """
    keys = [PREFIX + f"design_{i}.pdb" for i in range(1, 6)]
    events = []
    s3 = _s3_mock(*keys, body=random.Random(1).randbytes(70_000))
    serve_object = s3.get_object.side_effect

    def _record_fetch(Bucket, Key):
        events.append(("fetch", Key))
        return serve_object(Bucket=Bucket, Key=Key)

    s3.get_object.side_effect = _record_fetch

    delivered = []

    async def receive():
        # Once, then wait: the middleware calls receive() again after the body
        # and rejects a second http.request. The response completing wins.
        if delivered:
            await anyio.sleep_forever()
        delivered.append(True)
        return {"type": "http.request", "body": b"", "more_body": False}

    served = bytearray()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            events.append(("served", len(message["body"])))
            served.extend(message["body"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/jobs/{JOB_ID}/download",
        "raw_path": f"/jobs/{JOB_ID}/download".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("test-client", 1234),
        "server": ("test", 80),
    }

    app.dependency_overrides[get_current_user] = _override_user()
    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        with (
            patch("jobs.router.get_db_pool", return_value=_make_pool(_job_row("failed"))),
            patch("jobs.router.get_s3_client", return_value=s3),
        ):
            await app(scope, receive, send)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)

    kinds = [kind for kind, _ in events]
    assert "served" in kinds, events
    assert kinds.index("served") < len(kinds) - 1 - kinds[::-1].index("fetch"), events

    # Interleaved and still a whole archive, not a torn one
    with zipfile.ZipFile(io.BytesIO(bytes(served))) as zf:
        assert zf.namelist() == [k.split("/")[-1] for k in keys]
        assert zf.testzip() is None


@pytest.mark.anyio
async def test_the_sink_hands_over_each_entry_once():
    """``_ChunkSink.drain`` returns what was written since the last drain.

    A drain that did not clear would repeat every earlier entry's bytes and
    the archive would arrive corrupt; one that aliased the caller's buffer
    could hand back bytes that ``zipfile`` had since reused.
    """
    sink = _ChunkSink()

    assert sink.write(b"ab") == 2
    assert sink.drain() == b"ab"
    assert sink.drain() == b""

    mutable = bytearray(b"cd")
    sink.write(mutable)
    mutable[0:1] = b"X"
    assert sink.drain() == b"cd"


async def test_a_fault_part_way_through_the_archive_is_captured_not_silent():
    """A broken object mid-archive leaves a short zip under a 200.

    ``http.response.start`` goes out before the generator is first pulled, so
    once bytes are flowing there is no route back to a 500: the client keeps
    what it received and the archive ends with no central directory, which is
    a file that will not open. Nothing retries and the status line says
    success, so the explicit capture is the only server-side signal that a
    download of a partial set was itself partial -- which is what this pins,
    together with the 200 that cannot be taken back and the bytes that do not
    open.
    """
    keys = [PREFIX + f"design_{i}.pdb" for i in range(1, 6)]
    s3 = _s3_mock(*keys, body=random.Random(2).randbytes(70_000))
    serve_object = s3.get_object.side_effect
    broken = keys[2]

    def _break_on_the_third(Bucket, Key):
        if Key == broken:
            raise OSError("connection reset mid-archive")
        return serve_object(Bucket=Bucket, Key=Key)

    s3.get_object.side_effect = _break_on_the_third

    delivered = []

    async def receive():
        if delivered:
            await anyio.sleep_forever()
        delivered.append(True)
        return {"type": "http.request", "body": b"", "more_body": False}

    served = bytearray()
    start = {}

    async def send(message):
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body" and message.get("body"):
            served.extend(message["body"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/jobs/{JOB_ID}/download",
        "raw_path": f"/jobs/{JOB_ID}/download".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("test-client", 1234),
        "server": ("test", 80),
    }

    app.dependency_overrides[get_current_user] = _override_user()
    app.dependency_overrides[get_active_org] = _override_active_org()
    try:
        with (
            patch("jobs.router.get_db_pool", return_value=_make_pool(_job_row("failed"))),
            patch("jobs.router.get_s3_client", return_value=s3),
            patch("jobs.router.sentry_sdk.capture_exception") as mock_capture,
        ):
            with pytest.raises(Exception):
                await app(scope, receive, send)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_active_org, None)

    # The customer was already told this was a 200.
    assert start["status"] == 200

    # The fault reached Sentry, carrying the exception the fetch raised.
    mock_capture.assert_called_once()
    assert isinstance(mock_capture.call_args[0][0], OSError)

    # ...and what the client holds is not an archive.
    assert served, "no bytes were sent before the fault"
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(bytes(served))).namelist()
