"""End-to-end smoke test against a real staging /api/v1 surface.

Skipped by default in CI. Run manually before tagging a release:

    BINDWAVE_E2E_ENABLED=1 \
    BINDWAVE_API_KEY=bw_live_... \
    BINDWAVE_BASE_URL=https://staging-api.bindwave.com/api/v1 \
    pytest tests/test_e2e.py -m e2e -v

Uses a minimal-cost test job (rfdiffusion 1-design) and cancels it immediately
so the bill is negligible.
"""

import os

import pytest

from bindwave import Client, JobStatus

pytestmark = pytest.mark.skipif(
    not os.environ.get("BINDWAVE_E2E_ENABLED"),
    reason="E2E disabled; set BINDWAVE_E2E_ENABLED=1 to run",
)


@pytest.mark.e2e
def test_e2e_submit_get_cancel():
    client = Client(
        base_url=os.environ.get(
            "BINDWAVE_BASE_URL", "https://staging-api.bindwave.com/api/v1"
        )
    )

    # Submit a tiny test job.
    job = client.jobs.submit(
        tool="rfdiffusion",
        parameters={"target_pdb_id": "1ubq", "num_designs": 1, "binder_length": 60},
        name="bindwave-python e2e smoke",
    )
    assert job.id
    assert job.status in {JobStatus.QUEUED, JobStatus.RUNNING}

    # Cancel immediately so we don't burn budget.
    cancelled = client.jobs.cancel(job.id)
    assert cancelled.status in {
        JobStatus.CANCELLED,
        JobStatus.COMPLETE,
        JobStatus.FAILED,
    }

    # Get-after-cancel returns the terminal job.
    final = client.jobs.get(job.id)
    assert final.id == job.id
