"""RFantibody pipeline: config generator and result parser.

RFantibody designs antibody/nanobody binders by generating CDR loop sequences
for a given epitope. Configuration is a flat dict of design parameters passed
to the handler.

Expected runtime: 10-30 minutes per batch on A100 80GB.
"""

from jobs.models import CandidateResult
from pipelines.base import ToolPipeline


class RFantibodyPipeline(ToolPipeline):
    """Pipeline for RFantibody CDR loop design jobs."""

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Build config dict for RFantibody.

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target PDB inside the container.

        Returns:
            Dict with keys: epitope_residues (list[str]), cdr_design (list[str]),
            framework (str), num_designs (int), target_pdb (str).
        """
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # Map hotspot residues to chain notation (e.g. A30, A55).
        epitope_residues = [f"{chain}{res}" for res in hotspots]

        cdr_design = params.get("cdr_design", ["H1", "H2", "H3"])
        framework = params.get("framework", "VHH")
        num_designs = params.get("num_designs", 10)

        return {
            "target_pdb": target_local_path,
            "epitope_residues": epitope_residues,
            "cdr_design": cdr_design,
            "framework": framework,
            "num_designs": num_designs,
        }

    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Parse RFantibody output into CandidateResult list.

        Expects output dict with 'candidates' list where each entry has
        'rank', 'pdb_key', and scores: rf2_confidence, cdr_geometry.

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
        """RFantibody timeout: 1 hour."""
        return 3_600_000
