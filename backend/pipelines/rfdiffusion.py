"""RFdiffusion pipeline: config generator and result parser.

RFdiffusion uses Hydra CLI overrides for configuration. The handler inside
the Docker container receives a list of CLI override strings and passes them
to the inference script.

Expected runtime: 10-30 minutes per batch on A100 80GB.
"""

from jobs.models import CandidateResult
from pipelines.base import ToolPipeline, merge_pilot_params


class RFdiffusionPipeline(ToolPipeline):
    """Pipeline for RFdiffusion binder design jobs."""

    @property
    def gpu_sku(self) -> str:
        """RFdiffusion fits in 24GB (A10G)."""
        return "A10G-24GB"

    def pilot_preset(self) -> dict:
        """Pilot: 2 designs — minimal validation that RFdiffusion + MPNN + AF2
        all connect. AF2 on A10G is slow (~30 min/design); 2 designs caps the
        pilot at ~70 min instead of ~5 hours for 10 designs."""
        return {"num_designs": 2}

    def smoke_preset(self) -> dict:
        """Smoke: N=1, 50 diffusion steps, AF2 stubbed — cheapest possible
        end-to-end pipeline validation. Proves RFdiffusion + ProteinMPNN can
        emit a designed PDB; AF2 scoring is skipped and replaced with stub
        floats so the full serialization path is exercised without paying
        the ~20-minute A10G multimer cost.

        See docs/SMOKE-TEST-SPEC.md.
        """
        return {
            "num_designs": 1,
            "diffusion_steps": 50,
            "skip_af2": True,
            "binder_length": {"min": 55, "max": 65},
        }

    def mini_pilot_preset(self) -> dict:
        """Mini-pilot: N=2, full 100 diffusion steps, real AF2 scoring.

        Final success gate for Phase 4 per-tool agents. Must produce two
        candidates with real parseable PDBs and real ipTM/pLDDT floats.
        """
        return {
            "num_designs": 2,
            "diffusion_steps": 50,  # cut from RFdiffusion default 50 to keep runtime bounded
            "skip_af2": False,
            "binder_length": {"min": 55, "max": 65},
        }

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        # Clamp to pilot preset when job_tier=pilot.
        job_spec = merge_pilot_params(job_spec, self.pilot_preset())
        """Build Hydra CLI override args from JobSpec parameters.

        Key mappings:
        - target_chain + binder_length -> contigmap.contigs string
        - hotspot_residues -> ppi.hotspot_res
        - num_designs -> inference.num_designs

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target PDB inside the container.

        Returns:
            Dict with keys: hydra_args (list[str]), num_designs (int), checkpoint (str).
        """
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # Binder length range from parameters (default 50-100 residues).
        binder_length = params.get("binder_length", {"min": 50, "max": 100})
        binder_min = binder_length.get("min", 50) if isinstance(binder_length, dict) else 50
        binder_max = binder_length.get("max", 100) if isinstance(binder_length, dict) else 100

        num_designs = params.get("num_designs", 10)

        # Build contig string: [ChainResRange/0 BinderLenRange]
        # The /0 gap means "break chain here" — target on left, binder on right.
        # Note: the container's run_pipeline.py overrides this with the actual
        # residue range from the PDB file. This placeholder is kept for config
        # serialization but is not used directly by RFdiffusion.
        contig_str = f"[{chain}1-999/0 {binder_min}-{binder_max}]"

        hydra_args = [
            f"inference.input_pdb={target_local_path}",
            f"contigmap.contigs={contig_str}",
            f"inference.num_designs={num_designs}",
            "inference.ckpt_override_path=Complex_base_ckpt.pt",
        ]

        # Add hotspot residues if specified.
        if hotspots:
            hotspot_str = "[" + ",".join(f"{chain}{res}" for res in hotspots) + "]"
            hydra_args.append(f"ppi.hotspot_res={hotspot_str}")

        return {
            "hydra_args": hydra_args,
            "num_designs": num_designs,
            "checkpoint": "Complex_base_ckpt.pt",
        }

    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Parse RFdiffusion output into CandidateResult list.

        Expects output dict with 'candidates' list where each entry has
        'rank', 'pdb_key', and 'scores' (ipTM, pLDDT, i_pAE).

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
        """RFdiffusion timeout: 30 minutes."""
        return 1_800_000
