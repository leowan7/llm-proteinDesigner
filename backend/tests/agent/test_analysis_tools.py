"""Tests for post-run analysis tool infrastructure (Phase 08, Plan 01).

Covers:
- agent.analysis.cache: get_cached / set_cached
- agent.analysis.ranking: rank_candidates / filter_candidates / compute_distribution_stats
- agent.analysis.tools: handle_load_job_results / handle_analyze_candidates / handle_flag_red_flags
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.analysis.cache import clear_cache, get_cached, set_cached

# ---------------------------------------------------------------------------
# Shared fixture data — 25 fake candidates with varied scores
# ---------------------------------------------------------------------------

def _make_candidates(n: int = 25) -> list[dict]:
    """Generate n fake candidates with varied scores covering edge cases."""
    candidates = []
    for i in range(n):
        # Vary scores so we get a range of quality
        rank = i + 1
        iptm = round(0.90 - i * 0.015, 3)           # 0.90 down to ~0.54
        plddt = round(0.88 - i * 0.012, 3)           # 0.88 down to ~0.60
        dg = round(-45.0 + i * 0.8, 2)               # -45 up to ~-26
        dsasa = round(950 - i * 15, 1)               # 950 down to ~590
        sc = round(0.72 - i * 0.008, 3)              # 0.72 down to ~0.52
        # Inject some red flags
        relaxed_clashes = 1 if i in (3, 7, 12) else 0   # clashes in some
        surface_hydro = round(0.30 + i * 0.015, 3)  # 0.30 up to ~0.66
        n_interface = max(4, 14 - i // 2)

        candidates.append({
            "rank": rank,
            "pdb_key": f"jobs/job-abc123/candidate_{rank:03d}.pdb",
            "scores": {
                "ipTM": iptm,
                "pLDDT": plddt,
                "dG": dg,
                "dSASA": dsasa,
                "ShapeComplementarity": sc,
                "Relaxed_Clashes": relaxed_clashes,
                "Surface_Hydrophobicity": surface_hydro,
                "n_InterfaceResidues": n_interface,
            },
        })
    return candidates


FAKE_CANDIDATES = _make_candidates(25)


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestCache:
    """In-memory candidate cache: get_cached / set_cached."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def test_get_cached_returns_none_for_unknown_job(self):
        """get_cached returns None when job_id has never been stored."""
        result = get_cached("nonexistent-job-id")
        assert result is None

    def test_set_and_get_cached_returns_stored_candidates(self):
        """set_cached stores; get_cached retrieves the exact same list."""
        job_id = "job-test-001"
        candidates = [{"rank": 1, "pdb_key": "a.pdb", "scores": {"ipTM": 0.8}}]
        set_cached(job_id, candidates)
        retrieved = get_cached(job_id)
        assert retrieved == candidates

    def test_cache_is_keyed_by_job_id(self):
        """Different job_ids store independently."""
        set_cached("job-a", [{"rank": 1}])
        set_cached("job-b", [{"rank": 2}])
        assert get_cached("job-a") == [{"rank": 1}]
        assert get_cached("job-b") == [{"rank": 2}]


# ---------------------------------------------------------------------------
# Ranking tests
# ---------------------------------------------------------------------------


class TestRankCandidates:
    """rank_candidates sorts by metric and adds percentile column."""

    def test_rank_candidates_sorts_descending_by_default(self):
        """rank_candidates sorts by specified metric descending by default."""
        from agent.analysis.ranking import rank_candidates

        ranked = rank_candidates(FAKE_CANDIDATES, sort_by="ipTM")
        iptm_values = [c["scores"]["ipTM"] for c in ranked]
        assert iptm_values == sorted(iptm_values, reverse=True)

    def test_rank_candidates_adds_percentile_column(self):
        """rank_candidates adds a 'percentile' field to each candidate."""
        from agent.analysis.ranking import rank_candidates

        ranked = rank_candidates(FAKE_CANDIDATES, sort_by="ipTM")
        for candidate in ranked:
            assert "percentile" in candidate
            assert 0 <= candidate["percentile"] <= 100

    def test_rank_candidates_percentile_boundaries(self):
        """Top candidate has higher percentile than bottom candidate."""
        from agent.analysis.ranking import rank_candidates

        ranked = rank_candidates(FAKE_CANDIDATES, sort_by="ipTM")
        # After descending sort, first item should have highest percentile
        assert ranked[0]["percentile"] > ranked[-1]["percentile"]

    def test_rank_candidates_p95_boundary(self):
        """Top 5% of candidates (p95) have percentile >= 95."""
        from agent.analysis.ranking import rank_candidates

        ranked = rank_candidates(FAKE_CANDIDATES, sort_by="ipTM")
        # Top candidate in 25 items should be >= p95 of the distribution
        top_candidate_percentile = ranked[0]["percentile"]
        assert top_candidate_percentile >= 90  # At least p90 for top item


class TestFilterCandidates:
    """filter_candidates applies criteria dict with AND logic."""

    def test_filter_candidates_greater_than(self):
        """filter_candidates with > threshold returns only matching rows."""
        from agent.analysis.ranking import filter_candidates

        threshold = 0.80
        filtered = filter_candidates(FAKE_CANDIDATES, {"ipTM": {">": threshold}})
        for candidate in filtered:
            assert candidate["scores"]["ipTM"] > threshold

    def test_filter_candidates_less_than(self):
        """filter_candidates with < threshold returns only rows below value."""
        from agent.analysis.ranking import filter_candidates

        filtered = filter_candidates(FAKE_CANDIDATES, {"dG": {"<": -35.0}})
        for candidate in filtered:
            assert candidate["scores"]["dG"] < -35.0

    def test_filter_candidates_and_logic(self):
        """Multiple criteria are applied with AND logic."""
        from agent.analysis.ranking import filter_candidates

        filtered = filter_candidates(
            FAKE_CANDIDATES,
            {"ipTM": {">": 0.70}, "pLDDT": {">": 0.75}},
        )
        for candidate in filtered:
            assert candidate["scores"]["ipTM"] > 0.70
            assert candidate["scores"]["pLDDT"] > 0.75

    def test_filter_candidates_between(self):
        """filter_candidates with 'between' returns rows within range."""
        from agent.analysis.ranking import filter_candidates

        low, high = 0.60, 0.75
        filtered = filter_candidates(
            FAKE_CANDIDATES,
            {"ipTM": {"between": [low, high]}},
        )
        for candidate in filtered:
            val = candidate["scores"]["ipTM"]
            assert low <= val <= high


class TestComputeDistributionStats:
    """compute_distribution_stats returns per-metric statistics."""

    def test_compute_distribution_stats_returns_all_metrics(self):
        """Returns stats dict keyed by all numeric score names."""
        from agent.analysis.ranking import compute_distribution_stats

        stats = compute_distribution_stats(FAKE_CANDIDATES)
        # Should have an entry for each score key
        assert "ipTM" in stats
        assert "pLDDT" in stats
        assert "dG" in stats
        assert "dSASA" in stats

    def test_compute_distribution_stats_correct_keys(self):
        """Each metric entry has min, max, mean, p25, p75, p95."""
        from agent.analysis.ranking import compute_distribution_stats

        stats = compute_distribution_stats(FAKE_CANDIDATES)
        for metric_stats in stats.values():
            assert "min" in metric_stats
            assert "max" in metric_stats
            assert "mean" in metric_stats
            assert "p25" in metric_stats
            assert "p75" in metric_stats
            assert "p95" in metric_stats

    def test_compute_distribution_stats_values_ordered(self):
        """min <= p25 <= mean <= p75 <= max for ipTM."""
        from agent.analysis.ranking import compute_distribution_stats

        stats = compute_distribution_stats(FAKE_CANDIDATES)
        s = stats["ipTM"]
        assert s["min"] <= s["p25"]
        assert s["p25"] <= s["p75"]
        assert s["p75"] <= s["max"]


# ---------------------------------------------------------------------------
# handle_load_job_results tests
# ---------------------------------------------------------------------------


def _make_db_rows(n: int):
    """Create n fake asyncpg-like row dicts for job_candidates query."""
    rows = []
    for i in range(n):
        rows.append({
            "rank": i + 1,
            "pdb_key": f"jobs/job-xyz/candidate_{i+1:03d}.pdb",
            "scores": json.dumps({
                "ipTM": round(0.90 - i * 0.015, 3),
                "pLDDT": round(0.88 - i * 0.012, 3),
                "dG": round(-45.0 + i * 0.8, 2),
                "dSASA": round(950 - i * 15, 1),
                "ShapeComplementarity": round(0.72 - i * 0.008, 3),
                "Relaxed_Clashes": 1 if i in (3, 7) else 0,
                "Surface_Hydrophobicity": round(0.30 + i * 0.015, 3),
                "n_InterfaceResidues": max(4, 14 - i // 2),
            }),
        })
    return rows


def _make_mock_pool(job_rows, candidate_rows):
    """Build a mock asyncpg pool that returns preset rows."""
    mock_conn = AsyncMock()

    # fetchrow for job ownership check
    if job_rows:
        mock_conn.fetchrow = AsyncMock(return_value={
            "tool": "bindcraft",
            "status": "complete",
            "job_spec": json.dumps({"num_designs": 100}),
        })
    else:
        mock_conn.fetchrow = AsyncMock(return_value=None)

    # fetch for candidates query
    # asyncpg rows support dict-like access — use plain dicts
    mock_conn.fetch = AsyncMock(return_value=candidate_rows)

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire)

    return mock_pool


class TestHandleLoadJobResults:
    """handle_load_job_results fetches candidates and returns summary."""

    def setup_method(self):
        clear_cache()

    @pytest.mark.anyio
    async def test_load_returns_all_candidates_when_count_le_20(self):
        """Returns all candidates when candidate_count <= 20."""
        from agent.analysis.tools import handle_load_job_results

        candidate_rows = _make_db_rows(15)
        mock_pool = _make_mock_pool(job_rows=True, candidate_rows=candidate_rows)

        with patch("db.connection.get_db_pool", AsyncMock(return_value=mock_pool)):
            result_json = await handle_load_job_results(
                {"job_id": "job-test-15"}, user_id="user-abc"
            )

        result = json.loads(result_json)
        assert result["status"] == "success"
        assert len(result["candidates"]) == 15
        assert result["total_candidates"] == 15

    @pytest.mark.anyio
    async def test_load_returns_top_20_plus_stats_when_count_gt_20(self):
        """Returns top 20 + distribution stats when candidate_count > 20."""
        from agent.analysis.tools import handle_load_job_results

        candidate_rows = _make_db_rows(25)
        mock_pool = _make_mock_pool(job_rows=True, candidate_rows=candidate_rows)

        with patch("db.connection.get_db_pool", AsyncMock(return_value=mock_pool)):
            result_json = await handle_load_job_results(
                {"job_id": "job-test-25"}, user_id="user-abc"
            )

        result = json.loads(result_json)
        assert result["status"] == "success"
        assert len(result["candidates"]) == 20
        assert result["total_candidates"] == 25
        assert "distribution_stats" in result

    @pytest.mark.anyio
    async def test_load_includes_tool_field(self):
        """Response includes 'tool' field from job row."""
        from agent.analysis.tools import handle_load_job_results

        candidate_rows = _make_db_rows(5)
        mock_pool = _make_mock_pool(job_rows=True, candidate_rows=candidate_rows)

        with patch("db.connection.get_db_pool", AsyncMock(return_value=mock_pool)):
            result_json = await handle_load_job_results(
                {"job_id": "job-tool-test"}, user_id="user-abc"
            )

        result = json.loads(result_json)
        assert "tool" in result
        assert result["tool"] == "bindcraft"

    @pytest.mark.anyio
    async def test_load_returns_diagnostic_when_zero_candidates(self):
        """Returns diagnostic info when candidate_count == 0."""
        from agent.analysis.tools import handle_load_job_results

        mock_pool = _make_mock_pool(job_rows=True, candidate_rows=[])

        with patch("db.connection.get_db_pool", AsyncMock(return_value=mock_pool)):
            result_json = await handle_load_job_results(
                {"job_id": "job-zero"}, user_id="user-abc"
            )

        result = json.loads(result_json)
        assert result["status"] == "zero_output"
        assert "diagnostic" in result
        assert "tool" in result

    @pytest.mark.anyio
    async def test_load_returns_error_when_job_not_found(self):
        """Returns error JSON when job_id not found for this user."""
        from agent.analysis.tools import handle_load_job_results

        mock_pool = _make_mock_pool(job_rows=False, candidate_rows=[])

        with patch("db.connection.get_db_pool", AsyncMock(return_value=mock_pool)):
            result_json = await handle_load_job_results(
                {"job_id": "nonexistent-job"}, user_id="user-abc"
            )

        result = json.loads(result_json)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# handle_analyze_candidates tests
# ---------------------------------------------------------------------------


class TestHandleAnalyzeCandidates:
    """handle_analyze_candidates ranks, filters, and annotates candidates."""

    def setup_method(self):
        clear_cache()
        # Pre-populate cache so analyze_candidates can load from it
        set_cached("job-analyze-test", FAKE_CANDIDATES)

    @pytest.mark.anyio
    async def test_analyze_returns_ranked_list_with_annotations(self):
        """Returns ranked candidates with threshold annotations per metric."""
        from agent.analysis.tools import handle_analyze_candidates

        result_json = await handle_analyze_candidates(
            {"job_id": "job-analyze-test", "sort_by": "ipTM", "limit": 5},
            user_id="user-abc",
        )
        result = json.loads(result_json)
        assert result["status"] == "success"
        assert len(result["candidates"]) == 5

        # Each candidate should have threshold annotations
        for candidate in result["candidates"]:
            assert "threshold_assessments" in candidate

    @pytest.mark.anyio
    async def test_analyze_applies_absolute_threshold_annotations(self):
        """Strong candidates flagged 'strong', red flag candidates flagged 'red_flag'."""
        from agent.analysis.tools import handle_analyze_candidates

        result_json = await handle_analyze_candidates(
            {"job_id": "job-analyze-test", "sort_by": "ipTM", "limit": 25},
            user_id="user-abc",
        )
        result = json.loads(result_json)
        # Top candidates should have 'strong' label for ipTM
        top = result["candidates"][0]
        assert top["threshold_assessments"]["ipTM"] == "strong"

    @pytest.mark.anyio
    async def test_analyze_returns_error_when_not_cached(self):
        """Returns error if job not in cache (load_job_results not called)."""
        from agent.analysis.tools import handle_analyze_candidates

        result_json = await handle_analyze_candidates(
            {"job_id": "never-loaded-job", "sort_by": "ipTM"},
            user_id="user-abc",
        )
        result = json.loads(result_json)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# handle_flag_red_flags tests
# ---------------------------------------------------------------------------


class TestHandleFlagRedFlags:
    """handle_flag_red_flags detects known problematic metric combinations."""

    def setup_method(self):
        clear_cache()

    @pytest.mark.anyio
    async def test_flag_detects_high_iptm_low_shape_complementarity(self):
        """Flags candidates with high ipTM + low ShapeComplementarity combo."""
        from agent.analysis.tools import handle_flag_red_flags

        # Candidate that should trigger this flag: ipTM > 0.7 AND SC < 0.5
        flagged_candidate = {
            "rank": 1,
            "pdb_key": "test.pdb",
            "scores": {
                "ipTM": 0.85,
                "pLDDT": 0.82,
                "dG": -40.0,
                "dSASA": 800,
                "ShapeComplementarity": 0.42,  # < 0.5 triggers flag
                "Relaxed_Clashes": 0,
                "Surface_Hydrophobicity": 0.35,
                "n_InterfaceResidues": 12,
            },
        }
        set_cached("job-flag-test-sc", [flagged_candidate])

        result_json = await handle_flag_red_flags(
            {"job_id": "job-flag-test-sc"}, user_id="user-abc"
        )
        result = json.loads(result_json)
        assert result["flagged_count"] >= 1
        flag_descriptions = [f["flag"] for f in result["red_flags"]]
        assert any("false positive" in desc.lower() or "geometric" in desc.lower()
                   for desc in flag_descriptions)

    @pytest.mark.anyio
    async def test_flag_detects_relaxed_clashes(self):
        """Flags candidates with Relaxed_Clashes > 0."""
        from agent.analysis.tools import handle_flag_red_flags

        clash_candidate = {
            "rank": 1,
            "pdb_key": "test.pdb",
            "scores": {
                "ipTM": 0.75,
                "pLDDT": 0.80,
                "dG": -38.0,
                "dSASA": 750,
                "ShapeComplementarity": 0.62,
                "Relaxed_Clashes": 2,  # > 0 triggers flag
                "Surface_Hydrophobicity": 0.38,
                "n_InterfaceResidues": 11,
            },
        }
        set_cached("job-flag-test-clashes", [clash_candidate])

        result_json = await handle_flag_red_flags(
            {"job_id": "job-flag-test-clashes"}, user_id="user-abc"
        )
        result = json.loads(result_json)
        assert result["flagged_count"] >= 1
        flag_descriptions = [f["flag"] for f in result["red_flags"]]
        assert any("clash" in desc.lower() for desc in flag_descriptions)

    @pytest.mark.anyio
    async def test_flag_returns_empty_when_no_red_flags(self):
        """Returns empty red_flags list and flagged_count=0 for clean candidates."""
        from agent.analysis.tools import handle_flag_red_flags

        clean_candidate = {
            "rank": 1,
            "pdb_key": "clean.pdb",
            "scores": {
                "ipTM": 0.82,
                "pLDDT": 0.85,
                "dG": -42.0,
                "dSASA": 900,
                "ShapeComplementarity": 0.70,
                "Relaxed_Clashes": 0,
                "Surface_Hydrophobicity": 0.32,
                "n_InterfaceResidues": 14,
            },
        }
        set_cached("job-clean-test", [clean_candidate])

        result_json = await handle_flag_red_flags(
            {"job_id": "job-clean-test"}, user_id="user-abc"
        )
        result = json.loads(result_json)
        assert result["flagged_count"] == 0
        assert result["red_flags"] == []
        assert result["clean_count"] == 1
