"""Shared job+candidates serializer (Phase 13, RESEARCH §5.13).

Extracted so the /api/v1/jobs/{id} endpoint can return inline candidate metadata
with 24h presigned download URLs in a single response. Inverts the webhook
handler's candidate-persistence shape (webhooks/router.py:222-246): reads rows
from ``public.job_candidates`` rather than writing them.
"""

import json

from storage.client import generate_presigned_get_url


async def serialize_job_with_candidates(
    job_id: str,
    pool,
    expires_in: int = 86400,
) -> dict | None:
    """Read job + candidates from DB, return inline dict with 24h presigned URLs.

    Returns ``None`` if no job row matches.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, tool, status, name, created_at, completed_at,
                      results, organization_id, gpu_cost_usd
               FROM public.jobs
               WHERE id = $1""",
            job_id,
        )
        if not row:
            return None
        candidates = await conn.fetch(
            """SELECT rank, pdb_key, scores
               FROM public.job_candidates
               WHERE job_id = $1
               ORDER BY rank""",
            job_id,
        )
    return {
        "id": str(row["id"]),
        "tool": row["tool"],
        "status": row["status"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "organization_id": str(row["organization_id"]) if row["organization_id"] else None,
        "gpu_cost_usd": float(row["gpu_cost_usd"]) if row["gpu_cost_usd"] else None,
        "candidates": [
            {
                "rank": c["rank"],
                "pdb_key": c["pdb_key"],
                "scores": json.loads(c["scores"]) if isinstance(c["scores"], str) else c["scores"],
                "download_url": generate_presigned_get_url(c["pdb_key"], expires_in=expires_in),
            }
            for c in candidates
        ],
    }
