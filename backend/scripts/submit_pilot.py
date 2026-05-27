"""Submit a pilot job directly — bypass the agent chat to save Claude API cost.

Usage (inside the backend container):
    docker compose exec backend python /app/scripts/submit_pilot.py 4Z18 bindcraft
    docker compose exec backend python /app/scripts/submit_pilot.py 7TPN boltzgen --hotspots 45,48,52

Positional args:
    pdb_id   RCSB accession (fetched fresh every run; no reliance on /tmp).
    tool     One of: bindcraft, boltzgen, rfdiffusion, rfantibody, pxdesign.

Optional:
    --chain A                 Target chain (default "A").
    --hotspots 45,48,52       Comma-separated residue numbers (default empty).
    --user test@example.com   Owning user's email (default: first user row).
    --tier pilot              pilot | full_design (default pilot).

What it does (same path the /jobs/launch endpoint follows, minus the Stripe
check and chat UI):
    1. Fetch the PDB file from RCSB.
    2. Create a jobs row with status='draft' + a minimal but complete JobSpec.
    3. Upload the PDB to MinIO under users/{uid}/jobs/{jid}/inputs/target.pdb.
    4. Rewrite target_pdb_path in the spec to that S3 key.
    5. Call launch_job() which flips status->queued and enqueues run_job.

The worker picks it up within a second. Watch Modal + MinIO logs normally.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import httpx

# Make "import config" / "import jobs.dispatch" work when this file is run as
# a script (python /app/scripts/submit_pilot.py ...). /app is the backend root
# in the container; running tests from the same container already relies on
# this being on sys.path, but be defensive if invoked with a different CWD.
sys.path.insert(0, "/app")

from agent.jobspec import JobSpec, ValidationResult  # noqa: E402
from config import settings  # noqa: E402
from db.connection import get_db_pool  # noqa: E402
from jobs.dispatch import launch_job  # noqa: E402
from storage.client import get_s3_client  # noqa: E402


# Minimal default parameters per tool. These mirror what the agent wizard
# would have produced for a simple pilot. Tune only if you need to.
#
# IMPORTANT: Kept in sync with backend/pipelines/<tool>.py::pilot_preset().
# The worker does NOT call pipeline.generate_config(), so the pilot-preset
# clamp there is effectively dead code — the container reads these raw
# parameters directly via job_spec.parameters. That means whatever we set
# here is what the GPU actually runs. Pilot tier = 2 designs across the
# board so end-to-end validation finishes in minutes, not hours.
_TOOL_PARAMS: dict[str, dict] = {
    "bindcraft": {
        "num_designs": 1,
        "design_cycles": 4,
        "mpnn_sampling_temp": 0.1,
        "filter_score_threshold": 80.0,
    },
    "boltzgen": {
        "budget": 2,
        "num_designs": 2,
    },
    "rfdiffusion": {
        "num_designs": 1,
    },
    "rfantibody": {
        "num_designs": 2,
        # Pilot-only clamps. Default container params (mpnn_seqs=5,
        # rf2_recycles=10) produce 2 backbones * 5 seqs * 10 recycles = 100
        # RF2 forward passes, which pushes the RF2 stage past 60 min on
        # A100-40GB. For pilot E2E validation we only need the 3-stage
        # Quiver pipeline to emit something scorable, so cut aggressively.
        "mpnn_seqs_per_backbone": 2,
        "rf2_recycles": 3,
        "framework": "VHH",
    },
    "pxdesign": {
        "num_designs": 2,
        "preset": "preview",
    },
}


async def _fetch_pdb(pdb_id: str) -> bytes:
    url = f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _upload_pdb(pdb_bytes: bytes, user_id: str, job_id: str) -> str:
    key = f"users/{user_id}/jobs/{job_id}/inputs/target.pdb"
    client = get_s3_client()
    # Ensure bucket exists (first run may hit a fresh MinIO).
    try:
        client.head_bucket(Bucket=settings.s3_bucket_name)
    except Exception:
        client.create_bucket(Bucket=settings.s3_bucket_name)
    client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=pdb_bytes,
        ContentType="chemical/x-pdb",
    )
    return key


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdb_id")
    ap.add_argument("tool", choices=list(_TOOL_PARAMS.keys()))
    ap.add_argument("--chain", default="A")
    ap.add_argument("--hotspots", default="",
                    help="Comma-separated residue numbers (optional).")
    ap.add_argument("--user", default=None,
                    help="Email of owning user. Defaults to first users row.")
    ap.add_argument("--tier", default="pilot", choices=["pilot", "full_design"])
    ap.add_argument("--budget-hours", type=int, default=4)
    args = ap.parse_args()

    pdb_id = args.pdb_id.upper()
    hotspots = [int(x) for x in args.hotspots.split(",") if x.strip()]

    pool = await get_db_pool()

    # Resolve user.
    async with pool.acquire() as conn:
        if args.user:
            user_row = await conn.fetchrow(
                "SELECT id FROM public.users WHERE email = $1", args.user,
            )
        else:
            user_row = await conn.fetchrow(
                "SELECT id FROM public.users ORDER BY id LIMIT 1",
            )
    if not user_row:
        print("No user found; create one in Supabase first.", file=sys.stderr)
        return 2
    user_id = str(user_row["id"])

    # Pre-allocate the job_id so we can upload under users/{uid}/jobs/{jid}/...
    job_id = str(uuid.uuid4())

    # Fetch + upload PDB.
    print(f"Fetching {pdb_id} from RCSB...", flush=True)
    pdb_bytes = await _fetch_pdb(pdb_id)
    print(f"  got {len(pdb_bytes)} bytes", flush=True)

    print("Uploading to MinIO...", flush=True)
    s3_key = _upload_pdb(pdb_bytes, user_id=user_id, job_id=job_id)
    print(f"  s3://{settings.s3_bucket_name}/{s3_key}", flush=True)

    # Build JobSpec.
    spec = JobSpec(
        tool=args.tool,
        target_pdb_path=s3_key,
        target_chain=args.chain,
        hotspot_residues=hotspots,
        parameters=_TOOL_PARAMS[args.tool],
        validation_results=[
            ValidationResult(
                check_name="script_submit",
                status="pass",
                message="Submitted via submit_pilot.py (agent bypass).",
            ),
        ],
        estimated_cost_usd=0.0,
        rationale="Direct pilot submission via submit_pilot.py — no agent reasoning.",
    )

    # Create draft jobs row (launch_job's UPDATE requires it to exist).
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.jobs (id, user_id, status, tool, job_spec, job_tier, total_budget_hours)
            VALUES ($1, $2, 'draft', $3, $4::jsonb, $5, $6)
            """,
            job_id, user_id, args.tool, spec.model_dump_json(),
            args.tier, args.budget_hours,
        )

    print(f"Dispatching job {job_id} (tool={args.tool}, tier={args.tier})...",
          flush=True)
    await launch_job(
        job_id=job_id,
        job_spec=spec,
        user_id=user_id,
        pool=pool,
        job_tier=args.tier,
        total_budget_hours=args.budget_hours,
    )

    print(f"\nQueued. job_id = {job_id}", flush=True)
    print("Watch:", flush=True)
    print(f"  docker compose logs -f worker | grep {job_id[:8]}", flush=True)
    print(f"  modal app logs ranomics-{args.tool}-prod", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
