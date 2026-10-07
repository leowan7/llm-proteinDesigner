"""Tests for ``storage/client.py::job_has_saved_designs``.

The helper decides money: since the webhook commit on this branch, a crashed or
timed-out run of a tool in ``webhooks/router.py::_STREAMS_DESIGNS_MID_RUN`` is
billed exactly when this returns True. Every webhook test that reaches that
branch patches it out, so the prefix it reads and the keys it accepts are
pinned here instead.

Three behaviours covered:
    1. A design PDB under the job's own ``outputs/`` prefix counts.
    2. The metrics CSVs that land in that same prefix do not.
    3. An empty or absent prefix is False rather than an error.
"""
import os

os.environ.setdefault("TESTING", "true")

from unittest.mock import MagicMock, patch

from storage.client import job_has_saved_designs

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
    upload into the one ``outputs/`` prefix. A run that produced no accepted
    design can still have uploaded them, and must stay unbilled.
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
