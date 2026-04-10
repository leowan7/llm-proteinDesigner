"""Tool handler functions for post-run analysis agent tools.

Implements the server-side logic for three analysis tools:
    handle_load_job_results   — Load candidates from DB with ownership check
    handle_analyze_candidates — Rank, filter, and annotate candidates
    handle_flag_red_flags     — Detect known problematic metric combinations

These handlers follow the same _handle_* pattern as backend/agent/tools.py.
Each returns a JSON string for the Claude tool_result content block.

Security: load_job_results enforces per-user ownership on all DB queries
(T-08-01). analyze_candidates and flag_red_flags read from the in-memory
cache, which is only populated by ownership-checked load_job_results calls
(T-08-02 mitigation — no direct DB bypass path exists).
"""

import json
from typing import Any

from agent.analysis.cache import get_cached, set_cached
from agent.analysis.ranking import (
    compute_distribution_stats,
    filter_candidates,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# Metric thresholds (per D-09, D-10)
# Green = strong; red = red_flag; lower_is_better flips the comparison
# ---------------------------------------------------------------------------

METRIC_THRESHOLDS: dict[str, dict] = {
    "ipTM": {
        "green": 0.7,
        "red": 0.45,
        "lower_is_better": False,
    },
    "pLDDT": {
        "green": 0.8,
        "red": 0.7,
        "lower_is_better": False,
    },
    "dG": {
        "green": -30,
        "red": -10,
        "lower_is_better": True,  # more negative = better
    },
    "dSASA": {
        "green": 800,
        "red": 400,
        "lower_is_better": False,
    },
    "ShapeComplementarity": {
        "green": 0.65,
        "red": 0.5,
        "lower_is_better": False,
    },
    "Relaxed_Clashes": {
        "green": 0,
        "red": 2,
        "lower_is_better": True,  # 0 = best, >2 = red flag
    },
    "Surface_Hydrophobicity": {
        "green": 0.4,
        "red": 0.6,
        "lower_is_better": True,  # lower = less aggregation risk
    },
}


def _assess_threshold(metric: str, value: float) -> str:
    """Return 'strong', 'passable', or 'red_flag' for a metric value.

    Args:
        metric: Metric name matching a key in METRIC_THRESHOLDS.
        value: Numeric score value to assess.

    Returns:
        'strong' if value meets the green threshold,
        'red_flag' if value crosses the red threshold,
        'passable' otherwise.
        Returns 'unknown' if the metric is not in METRIC_THRESHOLDS.
    """
    thresholds = METRIC_THRESHOLDS.get(metric)
    if thresholds is None:
        return "unknown"

    lower_is_better = thresholds.get("lower_is_better", False)
    green = thresholds["green"]
    red = thresholds["red"]

    if lower_is_better:
        # Lower value is better: green if <= green threshold, red_flag if >= red threshold
        if value <= green:
            return "strong"
        elif value >= red:
            return "red_flag"
        else:
            return "passable"
    else:
        # Higher value is better: green if >= green threshold, red_flag if <= red threshold
        if value >= green:
            return "strong"
        elif value <= red:
            return "red_flag"
        else:
            return "passable"


def _annotate_with_thresholds(candidate: dict) -> dict:
    """Add threshold assessments to a candidate dict.

    Args:
        candidate: Candidate dict with 'scores' sub-dict.

    Returns:
        Copy of candidate with added 'threshold_assessments' key mapping
        metric names to 'strong', 'passable', or 'red_flag'.
    """
    scores = candidate.get("scores", {})
    assessments = {}
    for metric, value in scores.items():
        if isinstance(value, (int, float)):
            assessments[metric] = _assess_threshold(metric, float(value))

    result = dict(candidate)
    result["threshold_assessments"] = assessments
    return result


async def handle_load_job_results(tool_input: dict, user_id: str) -> str:
    """Load completed job candidates and scores from DB.

    Checks cache first. On cache miss, fetches from DB with strict ownership
    verification (WHERE j.id = $1 AND j.user_id = $2) to prevent data leakage
    across user accounts (T-08-01).

    Args:
        tool_input: Must contain 'job_id' (string UUID).
        user_id: Authenticated user ID for ownership check.

    Returns:
        JSON string with one of:
        - status='success': candidates (top 20 if >20 total), distribution_stats,
          total_candidates, tool
        - status='zero_output': diagnostic info for empty result sets (D-08)
        - status='error': error message if job not found / not owned / not complete
    """
    job_id = tool_input.get("job_id")
    if not job_id:
        return json.dumps({"status": "error", "message": "job_id is required."})

    # Check cache first to avoid redundant DB queries
    cached = get_cached(job_id)
    if cached is not None:
        return _format_load_response(cached, job_id, tool="cached")

    # Fetch from DB with ownership check
    try:
        from db.connection import get_db_pool

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Ownership check: only return jobs owned by this user
            job_row = await conn.fetchrow(
                """SELECT j.tool, j.status, j.job_spec
                   FROM public.jobs j
                   WHERE j.id = $1 AND j.user_id = $2""",
                job_id,
                user_id,
            )

            if job_row is None:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Job {job_id} not found or you do not have access to it."
                    ),
                })

            job_status = job_row["status"]
            if job_status != "complete":
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Job {job_id} is not complete yet (status: {job_status}). "
                        "Results are only available after a job finishes."
                    ),
                })

            tool = job_row["tool"]
            job_spec_raw = job_row["job_spec"]
            job_spec: dict[str, Any] = (
                json.loads(job_spec_raw)
                if isinstance(job_spec_raw, str)
                else dict(job_spec_raw)
            )

            # Fetch all candidates ordered by rank
            candidate_rows = await conn.fetch(
                """SELECT rank, pdb_key, scores
                   FROM public.job_candidates
                   WHERE job_id = $1
                   ORDER BY rank""",
                job_id,
            )

    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Failed to load job results: {exc}",
        })

    # Parse scores JSONB and build candidate dicts
    candidates = []
    for row in candidate_rows:
        scores_raw = row["scores"]
        scores: dict = (
            json.loads(scores_raw) if isinstance(scores_raw, str) else dict(scores_raw)
        )
        candidates.append({
            "rank": row["rank"],
            "pdb_key": row["pdb_key"],
            "scores": scores,
        })

    # Cache for subsequent analyze_candidates / flag_red_flags calls
    set_cached(job_id, candidates)

    return _format_load_response(candidates, job_id, tool=tool, job_spec=job_spec)


def _format_load_response(
    candidates: list[dict],
    job_id: str,
    tool: str,
    job_spec: dict | None = None,
) -> str:
    """Format the load_job_results response payload.

    Args:
        candidates: Full list of candidate dicts.
        job_id: UUID of the job.
        tool: Tool name used for this job.
        job_spec: Original job parameters dict (optional).

    Returns:
        JSON string with formatted response.
    """
    total = len(candidates)

    if total == 0:
        return json.dumps({
            "status": "zero_output",
            "job_id": job_id,
            "tool": tool,
            "job_spec": job_spec or {},
            "diagnostic": (
                "This job produced zero passing candidates. "
                "[Agent: analyze the job parameters and target properties to explain "
                "what likely went wrong and suggest adjustments — "
                "e.g., relax filter thresholds, adjust hotspot residues, "
                "try a different tool or parameter set.]"
            ),
        })

    # Compute distribution stats over all candidates
    distribution_stats = compute_distribution_stats(candidates)

    # Return top 20 candidates by rank for large result sets (D-05 context window)
    display_candidates = candidates[:20] if total > 20 else candidates

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "tool": tool,
        "total_candidates": total,
        "candidates": display_candidates,
        "distribution_stats": distribution_stats,
        "note": (
            f"Showing top {len(display_candidates)} of {total} candidates by rank. "
            "Use analyze_candidates to re-rank by a different metric."
            if total > 20
            else f"Showing all {total} candidates."
        ),
    })


async def handle_analyze_candidates(tool_input: dict, user_id: str) -> str:
    """Rank and filter job candidates by specific metrics with threshold annotations.

    Operates on the in-memory cache; load_job_results must be called first.

    Args:
        tool_input: Must contain 'job_id' and 'sort_by'. Optional: 'filters'
                    (criteria dict), 'limit' (int, default 10).
        user_id: Authenticated user ID (not used directly — cache is already
                 ownership-verified via load_job_results).

    Returns:
        JSON string with ranked, filtered, annotated candidates and distribution
        stats. Returns error if job is not in cache.
    """
    job_id = tool_input.get("job_id")
    sort_by = tool_input.get("sort_by")
    filters = tool_input.get("filters")
    limit = int(tool_input.get("limit", 10))

    if not job_id:
        return json.dumps({"status": "error", "message": "job_id is required."})
    if not sort_by:
        return json.dumps({"status": "error", "message": "sort_by is required."})

    cached = get_cached(job_id)
    if cached is None:
        return json.dumps({
            "status": "error",
            "message": (
                f"Job {job_id} is not loaded. "
                "Call load_job_results first to fetch candidates."
            ),
        })

    candidates = list(cached)

    # Apply optional filters before ranking
    if filters:
        candidates = filter_candidates(candidates, filters)

    if not candidates:
        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "candidates": [],
            "total_after_filter": 0,
            "message": "No candidates match the specified filters.",
        })

    # Rank by specified metric (respect lower_is_better for dG, clashes, etc.)
    ascending = METRIC_THRESHOLDS.get(sort_by, {}).get("lower_is_better", False)
    try:
        ranked = rank_candidates(candidates, sort_by=sort_by, ascending=ascending)
    except KeyError as exc:
        return json.dumps({"status": "error", "message": str(exc)})

    # Slice to limit
    top_candidates = ranked[:limit]

    # Annotate each candidate with threshold assessments (per D-09, D-10)
    annotated = [_annotate_with_thresholds(c) for c in top_candidates]

    # Distribution stats over the filtered (pre-slice) set
    distribution_stats = compute_distribution_stats(candidates)

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "sort_by": sort_by,
        "total_after_filter": len(candidates),
        "candidates": annotated,
        "distribution_stats": distribution_stats,
    })


async def handle_flag_red_flags(tool_input: dict, user_id: str) -> str:
    """Scan all candidates from a loaded job for problematic metric combinations.

    Checks four red flag patterns (per D-12):
    1. ipTM > 0.7 AND ShapeComplementarity < 0.5 — high confidence / poor geometric fit
    2. dG < -30 AND Surface_Hydrophobicity > 0.6 — favorable energy / aggregation-prone
    3. Relaxed_Clashes > 0 — structural clashes survive Rosetta relaxation
    4. pLDDT < 0.7 — low backbone confidence / foldability concern

    Args:
        tool_input: Must contain 'job_id'.
        user_id: Authenticated user ID (cache already ownership-verified).

    Returns:
        JSON string with:
        - 'red_flags': list of {rank, flag, metrics} for flagged candidates
        - 'clean_count': number of candidates with no flags
        - 'flagged_count': number of candidates with at least one flag
    """
    job_id = tool_input.get("job_id")
    if not job_id:
        return json.dumps({"status": "error", "message": "job_id is required."})

    cached = get_cached(job_id)
    if cached is None:
        return json.dumps({
            "status": "error",
            "message": (
                f"Job {job_id} is not loaded. "
                "Call load_job_results first to fetch candidates."
            ),
        })

    red_flags = []

    for candidate in cached:
        scores = candidate.get("scores", {})
        rank = candidate.get("rank", "?")
        candidate_flags = []

        iptm = scores.get("ipTM")
        sc = scores.get("ShapeComplementarity")
        dg = scores.get("dG")
        surface_hydro = scores.get("Surface_Hydrophobicity")
        clashes = scores.get("Relaxed_Clashes")
        plddt = scores.get("pLDDT")

        # Flag 1: High ipTM + low ShapeComplementarity
        # Likely false positive — confidence doesn't match geometric fit
        if iptm is not None and sc is not None:
            if iptm > 0.7 and sc < 0.5:
                candidate_flags.append({
                    "flag": (
                        "High confidence but poor geometric fit — likely false positive"
                    ),
                    "metrics": {"ipTM": iptm, "ShapeComplementarity": sc},
                })

        # Flag 2: Very favorable dG + high Surface_Hydrophobicity
        # Energetically favorable but aggregation-prone
        if dg is not None and surface_hydro is not None:
            if dg < -30 and surface_hydro > 0.6:
                candidate_flags.append({
                    "flag": (
                        "Energetically favorable but aggregation-prone"
                    ),
                    "metrics": {"dG": dg, "Surface_Hydrophobicity": surface_hydro},
                })

        # Flag 3: Relaxed_Clashes > 0
        # Structural clashes survive Rosetta relaxation — structural problem
        if clashes is not None and clashes > 0:
            candidate_flags.append({
                "flag": (
                    "Structural clashes survive relaxation — deprioritize"
                ),
                "metrics": {"Relaxed_Clashes": clashes},
            })

        # Flag 4: pLDDT < 0.7
        # Low backbone confidence — foldability concern
        if plddt is not None and plddt < 0.7:
            candidate_flags.append({
                "flag": "Low backbone confidence — foldability concern",
                "metrics": {"pLDDT": plddt},
            })

        if candidate_flags:
            for flag_entry in candidate_flags:
                red_flags.append({
                    "rank": rank,
                    "flag": flag_entry["flag"],
                    "metrics": flag_entry["metrics"],
                })

    # Count candidates with at least one flag (deduplicate by rank)
    flagged_ranks = {entry["rank"] for entry in red_flags}
    flagged_count = len(flagged_ranks)
    clean_count = len(cached) - flagged_count

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "red_flags": red_flags,
        "flagged_count": flagged_count,
        "clean_count": clean_count,
        "total_candidates": len(cached),
    })
