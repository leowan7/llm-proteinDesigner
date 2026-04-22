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
from pipelines.base import ToolPipeline, merge_pilot_params


class RFantibodyPipeline(ToolPipeline):
    """Pipeline for RFantibody CDR loop design jobs."""

    @property
    def gpu_sku(self) -> str:
        """RFantibody needs 40GB (RF2_ab + ProteinMPNN footprint)."""
        return "A100-40GB"

    def pilot_preset(self) -> dict:
        """Pilot: 2 designs — RF2_ab validation is slow per-design; 2 designs
        is enough to prove the nanobody/Fv pipeline works end-to-end without
        burning GPU-hours on full validation."""
        return {"num_designs": 2}

    def smoke_preset(self) -> dict:
        """Smoke: 1 VHH design, single short CDR, 1 RF2 recycle.

        Proves the end-to-end pipeline (RFdiffusion -> ProteinMPNN -> RF2 ->
        qvscorefile -> qvextract) runs without burning more than a few
        GPU-minutes. Scores are not filtered — any design that survives
        extraction counts as a pass. Used by tier="smoke" in the Modal
        wrapper (see docs/SMOKE-TEST-SPEC.md).
        """
        return {
            "num_designs": 1,
            "framework": "VHH",
            # Nanobody H-chain only; short fixed CDR3 for speed.
            "cdr_lengths": "H1:8,H2:7,H3:10",
            # PD-L1 PD-1 binding interface residues (chain A in 4ZQK).
            "hotspots": "A54,A56,A115",
            "mpnn_seqs_per_backbone": 1,
            "mpnn_temperature": 0.2,
            "rf2_recycles": 1,
            "diffuser_t": 25,
        }

    def mini_pilot_preset(self) -> dict:
        """Mini-pilot: 2 VHH designs, full Quiver scoring, 3 RF2 recycles.

        Final smoke gate. N=2 with real ipTM/pLDDT/pAE scores (no stubs).
        Expected runtime ~5-10 GPU-minutes on A100-40GB. Used by
        tier="mini_pilot" in the Modal wrapper.
        """
        return {
            "num_designs": 2,
            "framework": "VHH",
            "cdr_lengths": "H1:8,H2:7,H3:10-13",
            "hotspots": "A54,A56,A115,A123",
            "mpnn_seqs_per_backbone": 1,
            "mpnn_temperature": 0.2,
            "rf2_recycles": 3,
            "diffuser_t": 50,
        }

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        # Clamp to pilot preset when job_tier=pilot.
        job_spec = merge_pilot_params(job_spec, self.pilot_preset())
        """Build config dict for RFantibody.

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target PDB inside the container.

        Returns:
            Dict with pipeline parameters for the handler.

        Raises:
            ValueError: If hotspot_residues is empty. RFantibody (antibody-
                finetuned RFdiffusion) requires an epitope anchor. Without
                hotspots the model samples against undefined target geometry
                and often crashes deep in the RFdiffusion loop on targets with
                any disordered residue (e.g. the scipy ValueError "Non-positive
                determinant in rotation matrix" at a residue with a zero
                backbone frame). Fail fast with a clear message rather than
                burning ~150 GPU-seconds on an uninformative traceback.
        """
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        if not hotspots:
            raise ValueError(
                "RFantibody requires at least one hotspot residue to anchor "
                "the designed CDRs to an epitope. Set "
                "job_spec.hotspot_residues=[<residue_number>, ...] (the "
                "residues sit on the target chain specified by "
                "job_spec.target_chain). Running without hotspots produces "
                "undefined target geometry and RFdiffusion will crash on any "
                "target with a disordered residue."
            )

        # Build hotspots string: "A50,A51,A80"
        hotspots_str = ",".join(f"{chain}{res}" for res in hotspots)

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
