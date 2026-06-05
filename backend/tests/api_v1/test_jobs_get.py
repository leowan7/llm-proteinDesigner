"""Integration tests for GET /api/v1/jobs/{job_id}.

Requirements: API-06 (inline metadata + ranked candidates + 24h presigned URLs).
Downstream plan: Plan 13-03 ships api/v1/jobs.py + jobs/serialize.py.
"""

import pytest


def test_get_returns_inline_presigned_urls():
    """API-06: GET /api/v1/jobs/{id} returns inline presigned URLs for all output files.

    The response includes:
    - Job metadata (id, tool, status, name, created_at, completed_at, gpu_cost_usd)
    - candidates list ranked by rank field, each with pdb_key + scores + download_url
    - download_url is a 24h presigned S3 GET URL (expires_in=86400)

    Single response — no separate /download-urls endpoint needed (D-07).
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/jobs.py + jobs/serialize.py")
