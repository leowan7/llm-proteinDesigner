"""PXDesign pipeline: config generator and result parser.

PXDesign uses a YAML task specification for configuration. The handler
converts the spec dict to YAML and passes it to the PXDesign entry point.

Only the "basic" preset is supported in v1 (extended mode requires MSA
preparation, deferred to a future release).

Expected runtime: 10-30 minutes per batch on A100 80GB.
"""

from jobs.models import CandidateResult
from pipelines.base import ToolPipeline


class PXDesignPipeline(ToolPipeline):
    """Pipeline for PXDesign binder generation jobs."""

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Build YAML task spec dict for PXDesign.

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target PDB inside the container.

        Returns:
            Dict with keys: yaml_spec (dict), preset (str), num_designs (int).
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

        num_designs = params.get("num_designs", 10)

        yaml_spec = {
            "target": {
                "file": target_local_path,
                "chains": {
                    chain: {
                        "crop": True,
                        "hotspots": hotspots,
                    },
                },
            },
            "binder_length": length_spec,
            "preset": "basic",
            "N_sample": num_designs,
        }

        return {
            "yaml_spec": yaml_spec,
            "preset": "basic",
            "num_designs": num_designs,
        }

    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Parse PXDesign output into CandidateResult list.

        Expects output dict with 'candidates' list where each entry has
        'rank', 'pdb_key', and scores: ipTM, pLDDT, pAE, filter_status.

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
        """PXDesign timeout: 2 hours."""
        return 7_200_000
