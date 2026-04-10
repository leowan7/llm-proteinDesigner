"""Tests for backend/agent/analysis/refolding.py.

Covers handle_submit_refolding_job: job creation, ownership check,
cache validation, and error cases.

All DB and storage calls are mocked — no real infrastructure required.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_PARENT_JOB_ID = str(uuid.uuid4())
FAKE_USER_ID = str(uuid.uuid4())

FAKE_JOB_SPEC = {
    "tool": "bindcraft",
    "target_pdb_path": "/tmp/structures/4ZS7.pdb",
    "pdb_id": "4ZS7",
    "target_chain": "A",
    "hotspot_residues": [42, 43, 44],
    "parameters": {"num_designs": 100},
}

FAKE_CANDIDATES = [
    {
        "rank": i,
        "pdb_key": f"users/{FAKE_USER_ID}/jobs/{FAKE_PARENT_JOB_ID}/candidates/rank_{i}.pdb",
        "scores": {"ipTM": 0.75 + i * 0.02, "pLDDT": 0.82},
    }
    for i in range(1, 6)
]


def _make_mock_pool(job_row, execute_raises=False):
    """Build a mock asyncpg pool that returns job_row on fetchrow and optionally raises on execute."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=job_row)
    if execute_raises:
        mock_conn.execute = AsyncMock(side_effect=Exception("DB error"))
    else:
        mock_conn.execute = AsyncMock(return_value=None)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return mock_pool, mock_conn


# ---------------------------------------------------------------------------
# handle_submit_refolding_job tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_refolding_job_creates_draft_job():
    """handle_submit_refolding_job creates a job row with status 'draft'."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
        "status": "complete",
    }
    mock_pool, mock_conn = _make_mock_pool(fake_job_row)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        result_str = await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [1, 2],
                "refolding_tool": "boltzgen",
            },
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "success"

    # execute should have been called once per candidate rank
    assert mock_conn.execute.call_count == 2

    # Inspect the first INSERT call args
    first_call_args = mock_conn.execute.call_args_list[0][0]
    assert "INSERT INTO public.jobs" in first_call_args[0]
    assert "draft" in first_call_args[0] or "'draft'" in first_call_args[0] or "draft" in str(first_call_args)

    clear_cache()


@pytest.mark.asyncio
async def test_submit_refolding_job_uses_boltzgen_tool():
    """handle_submit_refolding_job defaults to boltzgen refolding tool."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
        "status": "complete",
    }
    mock_pool, mock_conn = _make_mock_pool(fake_job_row)

    captured_specs = []

    async def capture_execute(query, *args):
        """Capture job_spec JSON from INSERT call."""
        if "INSERT INTO public.jobs" in query:
            # job_spec is the 4th positional arg after query
            # Signature: execute(query, job_id, user_id, tool, job_spec_json, ...)
            if len(args) >= 4:
                try:
                    spec = json.loads(args[3])
                    captured_specs.append(spec)
                except (json.JSONDecodeError, IndexError):
                    pass

    mock_conn.execute = AsyncMock(side_effect=capture_execute)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        result_str = await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [1],
            },
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "success"

    # Verify the created job spec has boltzgen as tool
    assert len(captured_specs) >= 1
    assert captured_specs[0].get("tool") == "boltzgen"

    clear_cache()


@pytest.mark.asyncio
async def test_submit_refolding_job_fetches_target_pdb_from_parent_spec():
    """handle_submit_refolding_job uses the target PDB accession from parent job_spec."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
        "status": "complete",
    }
    mock_pool, mock_conn = _make_mock_pool(fake_job_row)

    captured_specs = []

    async def capture_execute(query, *args):
        if "INSERT INTO public.jobs" in query and len(args) >= 4:
            try:
                spec = json.loads(args[3])
                captured_specs.append(spec)
            except (json.JSONDecodeError, IndexError):
                pass

    mock_conn.execute = AsyncMock(side_effect=capture_execute)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [1],
            },
            user_id=FAKE_USER_ID,
        )

    assert len(captured_specs) >= 1
    spec = captured_specs[0]
    # target_pdb_source should reference the original PDB accession
    assert "target_pdb_source" in spec
    assert "4ZS7" in spec["target_pdb_source"]

    clear_cache()


@pytest.mark.asyncio
async def test_submit_refolding_job_error_if_rank_not_in_cache():
    """handle_submit_refolding_job returns error if candidate rank not found in cache."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)  # ranks 1-5

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
        "status": "complete",
    }
    mock_pool, _ = _make_mock_pool(fake_job_row)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        result_str = await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [99],  # rank 99 does not exist
            },
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "error"
    assert "99" in result["message"]

    clear_cache()


@pytest.mark.asyncio
async def test_submit_refolding_job_error_if_parent_not_found():
    """handle_submit_refolding_job returns error if parent job not found or not owned by user."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)

    # fetchrow returns None (job not found or wrong user)
    mock_pool, _ = _make_mock_pool(None)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        result_str = await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [1],
            },
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "error"
    assert "not found" in result["message"].lower() or "access" in result["message"].lower()

    clear_cache()


@pytest.mark.asyncio
async def test_submit_refolding_job_spec_contains_binder_pdb_key():
    """handle_submit_refolding_job job_spec contains binder_pdb_key from candidate."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
        "status": "complete",
    }
    mock_pool, mock_conn = _make_mock_pool(fake_job_row)

    captured_specs = []

    async def capture_execute(query, *args):
        if "INSERT INTO public.jobs" in query and len(args) >= 4:
            try:
                spec = json.loads(args[3])
                captured_specs.append(spec)
            except (json.JSONDecodeError, IndexError):
                pass

    mock_conn.execute = AsyncMock(side_effect=capture_execute)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [2],
            },
            user_id=FAKE_USER_ID,
        )

    assert len(captured_specs) >= 1
    spec = captured_specs[0]
    assert "binder_pdb_key" in spec
    # Binder key must reference rank_2
    assert "rank_2" in spec["binder_pdb_key"]

    clear_cache()


@pytest.mark.asyncio
async def test_submit_refolding_job_invalid_tool_rejected():
    """handle_submit_refolding_job rejects unknown refolding tools."""
    from agent.analysis.refolding import handle_submit_refolding_job
    from agent.analysis.cache import set_cached, clear_cache

    clear_cache()
    set_cached(FAKE_PARENT_JOB_ID, FAKE_CANDIDATES)

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
        "status": "complete",
    }
    mock_pool, _ = _make_mock_pool(fake_job_row)

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        result_str = await handle_submit_refolding_job(
            tool_input={
                "parent_job_id": FAKE_PARENT_JOB_ID,
                "candidate_ranks": [1],
                "refolding_tool": "unknown_tool",
            },
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "error"
    assert "unknown_tool" in result["message"] or "invalid" in result["message"].lower()

    clear_cache()
