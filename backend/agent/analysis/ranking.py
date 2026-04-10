"""Pandas-based ranking, filtering, and distribution stats for design candidates.

All functions accept and return plain Python lists of dicts (not DataFrames)
so callers do not need to know about pandas. Pandas is used internally for
vectorised operations.

Score values are expected under each candidate's 'scores' dict key.
"""

import pandas as pd


def rank_candidates(
    candidates: list[dict],
    sort_by: str,
    ascending: bool = False,
) -> list[dict]:
    """Sort candidates by a specified score metric and add a percentile column.

    Args:
        candidates: List of candidate dicts. Each must have a 'scores' dict
                    containing the sort_by key.
        sort_by: Name of the score metric to rank by (e.g. 'ipTM', 'dG').
        ascending: If True, lower values rank higher (for metrics where lower
                   is better, e.g. dG or Relaxed_Clashes). Default False.

    Returns:
        New list of candidate dicts, sorted by sort_by, with an additional
        top-level 'percentile' key (0-100 integer) indicating where each
        candidate sits in the metric distribution. Higher percentile = higher
        value in the original distribution (regardless of ascending flag).

    Raises:
        KeyError: If sort_by is not present in any candidate's scores dict.
    """
    if not candidates:
        return []

    df = pd.DataFrame(candidates)

    # Flatten scores dict into separate columns for easier manipulation
    scores_df = pd.json_normalize(df["scores"])

    if sort_by not in scores_df.columns:
        raise KeyError(
            f"Metric '{sort_by}' not found in candidate scores. "
            f"Available: {list(scores_df.columns)}"
        )

    df["_sort_value"] = scores_df[sort_by]

    # Compute percentile rank based on the raw metric value (not the sort order).
    # pct_rank gives fraction in [0, 1]; multiply by 100 and round.
    # This is always computed as "higher raw value = higher percentile" so the
    # caller can compare a candidate's absolute position in the distribution.
    df["percentile"] = (
        scores_df[sort_by]
        .rank(pct=True, method="average")
        .mul(100)
        .round(1)
    )

    df_sorted = df.sort_values("_sort_value", ascending=ascending).drop(
        columns=["_sort_value"]
    )

    return df_sorted.to_dict(orient="records")


def filter_candidates(
    candidates: list[dict],
    criteria: dict,
) -> list[dict]:
    """Filter candidates using a criteria dict with AND logic.

    Args:
        candidates: List of candidate dicts with 'scores' sub-dict.
        criteria: Dict mapping metric name to a comparison operator dict.
                  Supported operators:
                  - {"metric": {">": value}}
                  - {"metric": {"<": value}}
                  - {"metric": {">=": value}}
                  - {"metric": {"<=": value}}
                  - {"metric": {"between": [low, high]}}
                  All criteria are applied with AND logic (all must match).

    Returns:
        Filtered list of candidate dicts that satisfy all criteria.
        Returns empty list if no candidates match.

    Example:
        filter_candidates(candidates, {"pLDDT": {">": 0.85}, "dG": {"<": -30}})
    """
    if not candidates or not criteria:
        return list(candidates)

    result = list(candidates)

    for metric, ops in criteria.items():
        filtered = []
        for candidate in result:
            value = candidate.get("scores", {}).get(metric)
            if value is None:
                # Skip candidates missing the metric
                continue

            passes = True
            for operator, threshold in ops.items():
                if operator == ">":
                    passes = passes and (value > threshold)
                elif operator == "<":
                    passes = passes and (value < threshold)
                elif operator == ">=":
                    passes = passes and (value >= threshold)
                elif operator == "<=":
                    passes = passes and (value <= threshold)
                elif operator == "between":
                    low, high = threshold[0], threshold[1]
                    passes = passes and (low <= value <= high)
                else:
                    # Unknown operator — skip this condition
                    pass

            if passes:
                filtered.append(candidate)

        result = filtered

    return result


def compute_distribution_stats(candidates: list[dict]) -> dict[str, dict]:
    """Compute per-metric distribution statistics across all candidates.

    Args:
        candidates: List of candidate dicts with 'scores' sub-dict.

    Returns:
        Dict mapping metric name to a stats dict with keys:
        'min', 'max', 'mean', 'p25', 'p75', 'p95'.
        Only numeric score keys are included.
        Returns empty dict if candidates list is empty.
    """
    if not candidates:
        return {}

    # Build a flat DataFrame from all scores dicts
    scores_list = [c.get("scores", {}) for c in candidates]
    df = pd.DataFrame(scores_list)

    # Keep only numeric columns
    numeric_cols = df.select_dtypes(include="number").columns

    stats = {}
    for col in numeric_cols:
        series = df[col].dropna()
        if series.empty:
            continue
        stats[col] = {
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": float(series.mean()),
            "p25": float(series.quantile(0.25)),
            "p75": float(series.quantile(0.75)),
            "p95": float(series.quantile(0.95)),
        }

    return stats
