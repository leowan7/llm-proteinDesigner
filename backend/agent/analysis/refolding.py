"""Refolding job submission tool handler for post-run analysis.

Allows the agent to create draft refolding validation jobs (AF2-multimer or Boltz2)
directly from the analysis conversation. The user selects candidate ranks;
this handler creates draft job rows in the DB with job_spec referencing both
the original target PDB and the candidate binder PDB.

Decision context (from 08-CONTEXT.md):
  D-06: Agent can launch refolding jobs from analysis conversation
  D-07: Agent recommends + user confirms before calling this tool (enforced in TOOL_DEFINITIONS description)

Security:
  T-08-06: Parent job ownership verified with WHERE id = $1 AND user_id = $2
           Refolding jobs created under same user_id as parent
"""

import json
import logging
import uuid

logger = logging.getLogger(__name__)

# Allowed refolding tools
_VALID_REFOLDING_TOOLS = {"boltzgen", "alphafold2_multimer"}


async def handle_submit_refolding_job(tool_input: dict, user_id: str) -> str:
    """Create draft refolding validation jobs for selected candidates.

    For each candidate rank, assembles a job_spec that references:
      - The original target PDB (via RCSB accession or upload MinIO key)
      - The candidate's binder PDB key (from the candidate cache)

    The resulting jobs are created with status='draft'. They can be launched
    from the job page or by the user saying "launch refolding jobs".

    Security:
      - Parent job ownership verified with WHERE id = $1 AND user_id = $2 (T-08-06)
      - Refolding jobs are always created under the same user_id as the parent

    Args:
        tool_input: Dict with required keys:
          - parent_job_id (str): UUID of the completed parent design job
          - candidate_ranks (list[int]): Ranks of candidates to create refolding jobs for
          Optional keys:
          - refolding_tool (str): 'boltzgen' or 'alphafold2_multimer'; defaults to 'boltzgen'
        user_id: Authenticated user ID for ownership check and job creation.

    Returns:
        JSON string with status='success' and list of created job IDs,
        or status='error' with message.
    """
    parent_job_id = tool_input.get("parent_job_id")
    candidate_ranks = tool_input.get("candidate_ranks")
    refolding_tool = tool_input.get("refolding_tool", "boltzgen")

    # Input validation
    if not parent_job_id:
        return json.dumps({"status": "error", "message": "parent_job_id is required."})
    if not candidate_ranks:
        return json.dumps({"status": "error", "message": "candidate_ranks is required (list of integers)."})
    if refolding_tool not in _VALID_REFOLDING_TOOLS:
        return json.dumps({
            "status": "error",
            "message": (
                f"Invalid refolding_tool '{refolding_tool}'. "
                f"Must be one of: {sorted(_VALID_REFOLDING_TOOLS)}."
            ),
        })

    # Load candidates from cache (load_job_results must be called first)
    from agent.analysis.cache import get_cached
    candidates = get_cached(parent_job_id)
    if candidates is None:
        return json.dumps({
            "status": "error",
            "message": (
                f"Job {parent_job_id} is not loaded. "
                "Call load_job_results first, then call submit_refolding_job."
            ),
        })

    # Build rank -> candidate lookup dict
    rank_lookup: dict[int, dict] = {c.get("rank"): c for c in candidates}

    # Validate all requested ranks exist in the cache
    missing_ranks = [r for r in candidate_ranks if r not in rank_lookup]
    if missing_ranks:
        return json.dumps({
            "status": "error",
            "message": (
                f"Candidate rank(s) {missing_ranks} not found in loaded results for job {parent_job_id}. "
                f"Available ranks: {sorted(rank_lookup.keys())}."
            ),
        })

    # Fetch parent job from DB with ownership check (T-08-06)
    try:
        from db.connection import get_db_pool

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            parent_row = await conn.fetchrow(
                """SELECT tool, job_spec, status
                   FROM public.jobs
                   WHERE id = $1 AND user_id = $2""",
                parent_job_id,
                user_id,
            )

            if parent_row is None:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Parent job {parent_job_id} not found or you do not have access to it."
                    ),
                })

            if parent_row["status"] != "complete":
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Parent job {parent_job_id} is not complete "
                        f"(status: {parent_row['status']}). "
                        "Refolding jobs can only be created for completed design runs."
                    ),
                })

            parent_tool = parent_row["tool"]
            job_spec_raw = parent_row["job_spec"]
            parent_job_spec: dict = (
                json.loads(job_spec_raw) if isinstance(job_spec_raw, str) else dict(job_spec_raw)
            )

            # Determine target PDB source for refolding worker.
            # The original target_pdb_path was a container-local /tmp path that no longer exists.
            # Instead extract pdb_id (RCSB accession) or target_pdb_key (upload MinIO key).
            pdb_id = parent_job_spec.get("pdb_id")
            target_pdb_key = parent_job_spec.get("target_pdb_key")

            if pdb_id:
                target_pdb_source = f"rcsb:{pdb_id}"
            elif target_pdb_key:
                target_pdb_source = f"upload:{target_pdb_key}"
            else:
                # Fallback: extract accession from the path basename if possible
                original_path = parent_job_spec.get("target_pdb_path", "")
                import os
                basename = os.path.basename(original_path)
                accession = os.path.splitext(basename)[0]
                target_pdb_source = f"rcsb:{accession}" if accession else "unknown"

            # Create one draft refolding job per requested candidate rank
            created_jobs = []
            for rank in candidate_ranks:
                candidate = rank_lookup[rank]
                binder_pdb_key = candidate.get("pdb_key", "")
                new_job_id = str(uuid.uuid4())

                refolding_job_spec = {
                    "tool": refolding_tool,
                    "mode": "refolding_validation",
                    "parent_job_id": parent_job_id,
                    "parent_tool": parent_tool,
                    "target_pdb_source": target_pdb_source,
                    "binder_pdb_key": binder_pdb_key,
                    "candidate_rank": rank,
                    "parameters": {"num_designs": 1},
                }

                await conn.execute(
                    """INSERT INTO public.jobs (id, user_id, tool, status, job_spec, created_at)
                       VALUES ($1, $2::uuid, $3, 'draft', $4::jsonb, NOW())""",
                    new_job_id,
                    user_id,
                    refolding_tool,
                    json.dumps(refolding_job_spec),
                )

                created_jobs.append({"job_id": new_job_id, "candidate_rank": rank})
                logger.info(
                    "Created refolding job %s for parent %s rank %d",
                    new_job_id, parent_job_id, rank,
                )

    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Failed to create refolding jobs: {exc}",
        })

    return json.dumps({
        "status": "success",
        "refolding_jobs": created_jobs,
        "message": (
            f"Created {len(created_jobs)} refolding job(s) using {refolding_tool}. "
            "Launch them from the job page or say 'launch refolding jobs'."
        ),
    })
