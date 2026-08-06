"""BindCraft pipeline: config generator and result parser.

BindCraft uses a JSON settings file for configuration. The handler inside
the Docker container writes this JSON to disk and passes it to the BindCraft
entry point along with protocol and filter set file paths.

Expected runtime: 30 min - 4 hours per batch on A100 80GB.
BindCraft may return zero passing candidates — this is expected behavior, not failure.
"""

from jobs.models import CandidateResult

from pipelines.base import ToolPipeline, merge_pilot_params


class BindCraftPipeline(ToolPipeline):
    """Pipeline for BindCraft binder optimization jobs."""

    @property
    def gpu_sku(self) -> str:
        """BindCraft needs 80GB VRAM (AF2 multimer + ColabDesign footprint)."""
        return "A100-80GB"

    def pilot_preset(self) -> dict:
        """Pilot: 2 final designs — completes in ~20 min, proves the tool works."""
        return {"num_designs": 2}

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Build JSON settings dict for BindCraft.

        Pilot runs clamp ``num_designs`` to 2 via ``merge_pilot_params``.

        Args:
            job_spec: Deserialized JobSpec dict. May carry ``job_tier``.
            target_local_path: Path to target PDB inside the container.

        Returns:
            Dict with keys: settings_json (dict), protocol (str), filter_set (str).
        """
        job_spec = merge_pilot_params(job_spec, self.pilot_preset())
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # Binder length range from parameters.
        binder_length = params.get("binder_length", {"min": 50, "max": 100})
        if isinstance(binder_length, dict):
            lengths = [binder_length.get("min", 50), binder_length.get("max", 100)]
        else:
            lengths = [50, 100]

        num_designs = params.get("num_designs", 10)

        # Build hotspot string: comma-separated residue indices.
        hotspot_str = ",".join(str(res) for res in hotspots) if hotspots else ""

        settings_json = {
            "starting_pdb": target_local_path,
            "chains": chain,
            "target_hotspot_residues": hotspot_str,
            "lengths": lengths,
            "number_of_final_designs": num_designs,
            "binder_name": "design",
            "design_path": "/tmp/outputs/",
        }

        return {
            "settings_json": settings_json,
            "protocol": "default_4stage_multimer.json",
            "filter_set": "default_filters.json",
        }

    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Parse BindCraft output into CandidateResult list.

        Expects output dict with 'candidates' list where each entry has
        'rank', 'pdb_key', and scores: ipTM, pLDDT, RMSD, shape_complementarity, SAP.

        Args:
            output: RunPod handler output dict.

        Returns:
            List of CandidateResult objects sorted by rank. May be empty
            if BindCraft filtered all candidates (zero-output case).
        """
        candidates = output.get("candidates", [])
        return [
            CandidateResult(
                rank=c.get("rank", idx + 1),
                pdb_key=c["pdb_key"],
                scores=c.get("scores", {}),
            )
            for idx, c in enumerate(candidates)
        ]

    @property
    def execution_timeout_ms(self) -> int:
        """BindCraft timeout: 4 hours."""
        return 14_400_000

    @property
    def presigned_url_expiry_seconds(self) -> int:
        """BindCraft needs 6 hours for presigned URLs due to long runtime."""
        return 21_600
