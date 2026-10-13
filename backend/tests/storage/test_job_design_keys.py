"""Tests for ``storage/client.py::list_job_design_keys``, the function that
counts and names a job's saved designs.

It decides delivery: for a run whose terminal state is ``failed`` or
``cancelled`` it is the only record of the designs save-as-you-go banked,
because no ``job_candidates`` row is written on either path.
``jobs/router.py`` turns its keys into the candidate list and the presigned
URLs a customer downloads.

Since 2026-10-13 it also decides money on both failure routes: a failed run
is billed exactly when this returns at least one key
(``webhooks/router.py``, ``worker/cleanup.py::detect_stale_jobs``). That is
one predicate for both, which is the point.

The bulk download reads the same prefix with its own paginator
(``jobs/router.py:611``) because it archives every object it
finds, not only the designs. It reuses this module's ``DESIGN_SUFFIXES`` for
its own "is there anything to serve" check (``jobs/router.py:631``),
so the two cannot disagree about which objects are structures.

So the prefix it reads and the suffixes it accepts are pinned here. The
webhook, reaper and jobs tests patch it out and assert the call, so this file
is the only place the listing itself runs.
"""
import os

os.environ.setdefault("TESTING", "true")

from unittest.mock import MagicMock, patch

from storage.client import list_job_design_keys

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
        assert list_job_design_keys(USER_ID, JOB_ID) == [PREFIX + "design_1.pdb"]

    # Read under the job's own output prefix, the one get_upload_urls writes to
    assert paginator.paginate.call_args[1]["Prefix"] == PREFIX


def test_the_metrics_csvs_are_not_designs():
    """A BindCraft run uploads two CSVs beside its designs.

    ``docker/bindcraft/run_pipeline.py`` sends ``metrics.csv`` and
    ``bindcraft_results.csv`` through the same endpoint, which flattens every
    upload into the one ``outputs/`` prefix. Both are gated on at least one
    accepted design (docker/bindcraft/run_pipeline.py:1856, :1863), so a CSV-only prefix is the
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
        assert list_job_design_keys(USER_ID, JOB_ID) == []


def test_every_design_pdb_is_listed_sorted_and_across_pages():
    """Drains the paginator, drops non-designs, and returns them in key order.

    Three properties in one listing because they are one line of code each and
    a failed run needs all three: the customer gets every design the GPU
    banked, not the first page of them; the CSVs that share the prefix are not
    offered as structures; and the order is stable, so two reads of the same
    failed job agree on which design is rank 1. The suffix property this
    exercises is .pdb against .csv; .cif is kept too, and
    test_a_cif_design_is_delivered_too covers that.
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
