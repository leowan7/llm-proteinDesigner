"""PXDesign pipeline: config generator and result parser.

PXDesign uses a YAML task specification with:
  - target: file path, chains with crop ranges and hotspots
  - binder_length: integer or dict {min, max}
  - preset: "basic" (no MSA) or "extended" (requires MSA)
  - N_sample: number of designs to generate

Only the "basic" preset is supported in v1 (extended mode requires MSA
preparation, deferred to a future release).

Crop must be a list of string residue ranges (e.g. ["1-116"]), NOT a boolean.
The handler determines chain length from the CIF and fills crop accordingly.

Expected runtime: 10-30 minutes per batch on A100 80GB.
"""

from jobs.models import CandidateResult
from pipelines.base import ToolPipeline


class PXDesignPipeline(ToolPipeline):
    """Pipeline for PXDesign binder generation jobs."""

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Build YAML task spec dict for PXDesign.

        The crop field is set to None here — the handler fills it with the
        actual chain length (e.g. ["1-116"]) after reading the target CIF.

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target structure inside the container.

        Returns:
            Dict with keys: yaml_spec (dict), preset (str), num_designs (int).
        """
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # PXDesign accepts integer (80) or dict ({"min": 50, "max": 100})
        binder_length = params.get("binder_length", 80)

        num_designs = params.get("num_designs", 100)

        yaml_spec = {
            "target": {
                "file": target_local_path,
                "chains": {
                    chain: {
                        "crop": None,  # handler fills after reading CIF
                        "hotspots": hotspots,
                    },
                },
            },
            "binder_length": binder_length,
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
