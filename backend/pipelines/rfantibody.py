"""RFantibody pipeline: config generator and result parser.

RFantibody designs antibody/nanobody binders through a 3-stage Quiver pipeline:
  1. rfdiffusion — generate CDR loop backbones on a framework scaffold
  2. proteinmpnn — assign amino acid sequences to CDR loops
  3. rf2 — predict and score antibody-antigen complex structures

The handler selects a bundled framework PDB (VHH nanobody or scFv) and
passes CDR loop length ranges as a string (e.g. "H1:8,H2:7,H3:10-16").

Expected runtime: 15-60 minutes per batch on A100 80GB.
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
            Dict with pipeline parameters for the handler.
        """
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # Build hotspots string: "A50,A51,A80"
        hotspots_str = ",".join(f"{chain}{res}" for res in hotspots) if hotspots else ""

        # CDR loop length ranges: "H1:8,H2:7,H3:10-16"
        # Single value = fixed length, range = variable (e.g. H3:8-16)
        cdr_lengths = params.get("cdr_lengths", "H1:8,H2:7,H3:10-16")

        # Framework preset: "VHH" (nanobody) or "scFv" (full antibody)
        framework = params.get("framework", "VHH")

        return {
            "target_pdb": target_local_path,
            "framework": framework,
            "cdr_lengths": cdr_lengths,
            "hotspots": hotspots_str,
            "num_designs": params.get("num_designs", 100),
            "mpnn_seqs_per_backbone": params.get("mpnn_seqs_per_backbone", 5),
            "mpnn_temperature": params.get("mpnn_temperature", 0.2),
            "rf2_recycles": params.get("rf2_recycles", 10),
        }

    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Parse RFantibody output into CandidateResult list.

        Expects output dict with 'candidates' list where each entry has
        'rank', 'pdb_key', and scores: ipTM, pLDDT, pAE, pTM.

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
