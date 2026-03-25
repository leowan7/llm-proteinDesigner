"""BoltzGen pipeline: config generator and result parser.

BoltzGen uses a YAML design specification for configuration. The handler
converts the spec dict to YAML and passes it to the BoltzGen entry point.

Expected runtime: 5-15 minutes per batch on A100 80GB.
"""

from jobs.models import CandidateResult
from pipelines.base import ToolPipeline


class BoltzGenPipeline(ToolPipeline):
    """Pipeline for BoltzGen structure generation jobs."""

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Build YAML design spec dict for BoltzGen.

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target PDB inside the container.

        Returns:
            Dict with keys: yaml_spec (dict), protocol (str),
            num_designs (int), budget (int).
        """
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # Binder length range from parameters.
        binder_length = params.get("binder_length", {"min": 50, "max": 100})
        if isinstance(binder_length, dict):
            length_spec = {
                "min": binder_length.get("min", 50),
                "max": binder_length.get("max", 100),
            }
        else:
            length_spec = {"min": 50, "max": 100}

        # Map hotspot residues to chain + residue index notation.
        hotspot_specs = [f"{chain}{res}" for res in hotspots]

        num_designs = params.get("num_designs", 10)
        budget = params.get("budget", 1000)
        protocol = params.get("protocol", "protein-anything")

        yaml_spec = {
            "entities": [
                {"file": target_local_path, "chains": [chain]},
            ],
            "binder": {
                "length": length_spec,
                "hotspots": hotspot_specs,
            },
        }

        return {
            "yaml_spec": yaml_spec,
            "protocol": protocol,
            "num_designs": num_designs,
            "budget": budget,
        }

    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Parse BoltzGen output into CandidateResult list.

        Expects output dict with 'candidates' list where each entry has
        'rank', 'pdb_key', and scores: refolding_rmsd, ipTM, pLDDT.

        Args:
            output: RunPod handler output dict.

        Returns:
            List of CandidateResult objects sorted by rank.
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
        """BoltzGen timeout: 2 hours."""
        return 7_200_000
