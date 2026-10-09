"""Tests for the two readers of a job's ``outputs/`` prefix in ``storage/client.py``.

``job_has_saved_designs`` decides money: since the webhook commit on this
branch, a crashed or timed-out run of a tool in
``webhooks/router.py::_STREAMS_DESIGNS_MID_RUN`` is billed exactly when it
returns True.

``list_job_design_keys`` decides delivery: for a run whose terminal state is
``failed`` or ``cancelled`` it is the only record of the designs
save-as-you-go banked, because no ``job_candidates`` row is written on either
path. ``jobs/router.py`` turns its keys into the candidate list and the
presigned URLs a customer downloads.

Both go through the same generator -- ``job_has_saved_designs`` only as far
as its first hit -- so the prefix they read and the suffixes they accept are
pinned here. The webhook, reaper and jobs tests patch these two out
and assert the call, so this file is the only place the listing itself runs.
"""
import os

os.environ.setdefault("TESTING", "true")

import pytest
from unittest.mock import MagicMock, patch

from storage.client import job_has_saved_designs, list_job_design_keys

USER_ID = "user-uuid"
JOB_ID = "job-uuid"
PREFIX = f"users/{USER_ID}/jobs/{JOB_ID}/outputs/"


def _s3_yielding(*pages):
    """A boto3 client mock whose list_objects_v2 paginator yields ``pages``."""
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    client = MagicMock()
    client.get_paginator.return_value = paginator
    return client, paginator


def test_a_design_pdb_counts():
    client, paginator = _s3_yielding(
        {"Contents": [{"Key": PREFIX + "metrics.csv"}]},
        {"Contents": [{"Key": PREFIX + "design_1.pdb"}]},
    )

    with patch("storage.client.get_s3_client", return_value=client):
        assert job_has_saved_designs(USER_ID, JOB_ID) is True

    # Read under the job's own output prefix, the one get_upload_urls writes to
    assert paginator.paginate.call_args[1]["Prefix"] == PREFIX


def test_the_metrics_csvs_are_not_designs():
    """A BindCraft run uploads two CSVs beside its designs.

    ``docker/bindcraft/run_pipeline.py`` sends ``metrics.csv`` and
    ``bindcraft_results.csv`` through the same endpoint, which flattens every
    upload into the one ``outputs/`` prefix. Both are gated on at least one
    accepted design (run_pipeline.py:1856, :1863), so a CSV-only prefix is the
    case where designs were accepted but no PDB upload of any of them landed.
    Nothing reached Storage, so it stays unbilled.
    """
    client, _ = _s3_yielding(
        {
            "Contents": [
                {"Key": PREFIX + "metrics.csv"},
                {"Key": PREFIX + "bindcraft_results.csv"},
            ]
        }
    )

    with patch("storage.client.get_s3_client", return_value=client):
        assert job_has_saved_designs(USER_ID, JOB_ID) is False


def test_an_empty_prefix_is_false_not_an_error():
    """list_objects_v2 omits ``Contents`` entirely for a prefix with no keys."""
    client, _ = _s3_yielding({"KeyCount": 0})

    with patch("storage.client.get_s3_client", return_value=client):
        assert job_has_saved_designs(USER_ID, JOB_ID) is False


# ---------------------------------------------------------------------------
# list_job_design_keys: the delivery read
# ---------------------------------------------------------------------------

def test_every_design_pdb_is_listed_sorted_and_across_pages():
    """Drains the paginator, keeps only PDBs, and returns them in key order.

    Three properties in one listing because they are one line of code each and
    a failed run needs all three: the customer gets every design the GPU
    banked, not the first page of them; the CSVs that share the prefix are not
    offered as structures; and the order is stable, so two reads of the same
    failed job agree on which design is rank 1.
    """
    client, paginator = _s3_yielding(
        {
            "Contents": [
                {"Key": PREFIX + "design_2.pdb"},
                {"Key": PREFIX + "metrics.csv"},
            ]
        },
        {"Contents": [{"Key": PREFIX + "design_1.pdb"}]},
    )

    with patch("storage.client.get_s3_client", return_value=client):
        keys = list_job_design_keys(USER_ID, JOB_ID)

    assert keys == [PREFIX + "design_1.pdb", PREFIX + "design_2.pdb"]
    assert paginator.paginate.call_args[1]["Prefix"] == PREFIX


def test_a_cif_design_is_delivered_too():
    """BoltzGen and PxDesign can save a design as a ``.cif``.

    Both name each upload after the file their pipeline produced --
    ``design_{rank:03d}{ext}`` at ``docker/boltzgen/run_pipeline.py:2043-2044``
    and ``docker/pxdesign/run_pipeline.py:1882-1883`` -- so a run of either
    whose designs are CIFs would deliver nothing under a ``.pdb``-only filter.
    """
    client, _ = _s3_yielding(
        {
            "Contents": [
                {"Key": PREFIX + "design_001.cif"},
                {"Key": PREFIX + "summary.csv"},
            ]
        }
    )

    with patch("storage.client.get_s3_client", return_value=client):
        assert list_job_design_keys(USER_ID, JOB_ID) == [PREFIX + "design_001.cif"]


def test_an_empty_prefix_lists_nothing_rather_than_raising():
    """A run that died before its first upload has no partial set to serve.

    ``jobs/router.py`` reports this as a failed job with an empty candidate
    list, and the bulk download 404s on it.
    """
    client, _ = _s3_yielding({"KeyCount": 0})

    with patch("storage.client.get_s3_client", return_value=client):
        assert list_job_design_keys(USER_ID, JOB_ID) == []


def test_reaching_a_design_stops_the_paging():
    """The short-circuit, pinned by a page that fails if it is ever fetched.

    ``job_has_saved_designs`` is called per job in the reaper's sweep
    (``worker/cleanup.py``) and once per failed webhook, against a prefix that
    can hold tens of thousands of keys, so what it costs is the pages it takes
    to reach the first design rather than the whole prefix. The paginator here
    raises on its third page; ``any`` abandons the generator at the design on
    the second, so that page is never requested. ``list_job_design_keys``, which
    has to drain, does reach it -- the contrast is the proof that the saving
    belongs to this reader and not to the mock.
    """

    def pages():
        yield {"Contents": [{"Key": PREFIX + "metrics.csv"}]}
        yield {"Contents": [{"Key": PREFIX + "design_1.pdb"}]}
        raise RuntimeError("paged past the first design")

    client, paginator = _s3_yielding()
    paginator.paginate.return_value = pages()

    with patch("storage.client.get_s3_client", return_value=client):
        assert job_has_saved_designs(USER_ID, JOB_ID) is True

    paginator.paginate.return_value = pages()
    with patch("storage.client.get_s3_client", return_value=client):
        with pytest.raises(RuntimeError, match="paged past the first design"):
            list_job_design_keys(USER_ID, JOB_ID)
