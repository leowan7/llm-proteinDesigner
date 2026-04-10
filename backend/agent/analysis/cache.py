"""In-memory candidate cache keyed by job_id.

Candidates are loaded once per session via load_job_results and cached here
so that analyze_candidates and flag_red_flags can operate without additional
DB round-trips.

The cache is module-level (process-local). In a multi-worker deployment each
worker has its own cache; a cache miss simply triggers a DB re-fetch.
"""

from typing import Optional

# Module-level cache dict: job_id -> list of candidate dicts
_CANDIDATE_CACHE: dict[str, list[dict]] = {}


def get_cached(job_id: str) -> Optional[list[dict]]:
    """Return cached candidates for job_id, or None if not present.

    Args:
        job_id: UUID string of the completed job.

    Returns:
        List of candidate dicts, or None if the job has not been loaded yet.
    """
    return _CANDIDATE_CACHE.get(job_id)


def set_cached(job_id: str, candidates: list[dict]) -> None:
    """Store candidates in the cache under job_id.

    Args:
        job_id: UUID string of the completed job.
        candidates: List of candidate dicts (each with 'rank', 'pdb_key', 'scores').
    """
    _CANDIDATE_CACHE[job_id] = candidates


def clear_cache() -> None:
    """Remove all entries from the cache.

    Intended for use in tests to ensure isolation between test cases.
    Do NOT call in production code — the cache is intentionally persistent
    for the lifetime of the process to avoid redundant DB queries.
    """
    _CANDIDATE_CACHE.clear()
