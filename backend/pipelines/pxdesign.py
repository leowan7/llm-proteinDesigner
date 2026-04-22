"""PXDesign pipeline: config generator and result parser.

PXDesign uses a YAML task specification with:
  - target: file path, chains with crop ranges and hotspots
  - binder_length: integer or dict {min, max}
  - preset: "basic" (no MSA), "extended" (requires MSA), or "custom"
  - N_sample: number of designs to generate

Only "basic" mode is used in Phase 4 smoke/mini_pilot — extended requires MSA
preparation (deferred to a future release).

Crop must be a list of string residue ranges (e.g. ["1-116"]), NOT a boolean.
The handler determines chain length from the CIF and fills crop accordingly.

Expected runtime (A100-80GB, PD-L1 IgV target):
  - smoke (N=1, basic):       ~8-12 min (JAX JIT + sampling + AF2 IG)
  - mini_pilot (N=2, basic):  ~12-18 min
  - pilot (N=2, basic):       ~12-18 min
"""

from jobs.models import CandidateResult
from pipelines.base import ToolPipeline, merge_pilot_params


class PXDesignPipeline(ToolPipeline):
    """Pipeline for PXDesign binder generation jobs."""

    @property
    def gpu_sku(self) -> str:
        """PXDesign needs 80GB (DeepSpeed + JAX + AF2 footprint)."""
        return "A100-80GB"

    def pilot_preset(self) -> dict:
        """Pilot: 2 designs with basic preset — minimal pipeline validation.

        10 designs @ basic takes ~30-40 min; 2 cuts that to ~10-15 min and
        is enough to prove PXDesign + AF2-IG self-validation end-to-end.
        """
        return {"num_designs": 2, "preset": "preview"}

    def smoke_preset(self) -> dict:
        """Smoke: N=1, basic preset, no post-filter.

        Fastest possible config that proves the pipeline runs end-to-end.
        Scores may be stubbed/real depending on whether AF2-IG runs.
        Used by docker/pxdesign/run_pipeline.py when tier == "smoke".
        """
        return {
            "num_designs": 1,
            "preset": "preview",
            "post_filter": False,
            "binder_length": 80,
        }

    def mini_pilot_preset(self) -> dict:
        """Mini-pilot: N=2, basic preset, full post-scoring.

        Final success gate — every candidate must have real (non-zero, non-NaN)
        scores. Used by docker/pxdesign/run_pipeline.py when tier == "mini_pilot".
        """
        return {
            "num_designs": 2,
            "preset": "preview",
            "post_filter": True,
            "binder_length": 80,
        }

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
        # Clamp to pilot preset when job_tier=pilot.
        job_spec = merge_pilot_params(job_spec, self.pilot_preset())
        params = job_spec.get("parameters", {})
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

        # PXDesign accepts integer (80) or dict ({"min": 50, "max": 100})
        binder_length = params.get("binder_length", 80)
        num_designs = params.get("num_designs", 100)
        preset = params.get("preset", "basic")

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
            "preset": preset,
            "N_sample": num_designs,
        }

        return {
            "yaml_spec": yaml_spec,
            "preset": preset,
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
