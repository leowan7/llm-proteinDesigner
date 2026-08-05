"""PXDesign pipeline: config generator and result parser.

PXDesign uses a YAML task specification with:
  - target: file path, chains with crop ranges and hotspots
  - binder_length: a single integer (e.g. 80). PXDesign's YAML schema has
    NO range form — unlike RFdiffusion/BoltzGen/BindCraft in this repo,
    which take {"min": .., "max": ..}. Do not hand PXDesign a dict.
  - preset: "preview" (no MSA), "extended" (requires MSA), or "custom".
    Upstream's CLI is a click.Choice over exactly those three — "basic" is
    NOT a valid value. SMOKE-TEST-SPEC.md calls the no-MSA mode "Basic",
    which is where the wrong name in this file came from.
  - N_sample: number of designs to generate

Only "preview" mode is used in Phase 4 smoke/mini_pilot — extended requires
MSA preparation (deferred to a future release).

Crop must be a list of string residue ranges (e.g. ["1-116"]), NOT a boolean.
The handler determines chain length from the CIF and fills crop accordingly.

Expected runtime (A100-80GB, PD-L1 IgV target):
  - smoke (N=1, preview):       ~8-12 min (JAX JIT + sampling + AF2 IG)
  - mini_pilot (N=1, preview):  ~30-40 min (PXDesign-specific exception, see
                                 docs/SMOKE-TEST-SPEC.md Per-tool exceptions)
  - pilot (N=2, preview):       ~12-18 min
"""

from jobs.models import CandidateResult
from pdb_utils.pipeline_normalize import (
    group_hotspots_by_chain,
    parse_target_chains,
)
from pipelines.base import ToolPipeline, merge_pilot_params


class PXDesignPipeline(ToolPipeline):
    """Pipeline for PXDesign binder generation jobs."""

    @property
    def gpu_sku(self) -> str:
        """PXDesign needs 80GB (DeepSpeed + JAX + AF2 footprint)."""
        return "A100-80GB"

    def pilot_preset(self) -> dict:
        """Pilot: 2 designs with preview preset — minimal pipeline validation.

        10 designs @ preview takes ~30-40 min; 2 cuts that to ~10-15 min and
        is enough to prove PXDesign + AF2-IG self-validation end-to-end.
        """
        return {"num_designs": 2, "preset": "preview"}

    def smoke_preset(self) -> dict:
        """Smoke: N=1, preview preset, no post-filter.

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
        """Mini-pilot: N=1, preview preset, full post-scoring.

        Final success gate — the candidate must have real (non-zero, non-NaN)
        scores. Used by docker/pxdesign/run_pipeline.py when tier == "mini_pilot".

        NOTE: PXDesign mini_pilot uses N=1 (not N=2 like other tools) because
        each design takes ~35 GPU-min due to AF2-IG validation. One candidate
        with real scores is sufficient pipeline-end-to-end evidence. See
        docs/SMOKE-TEST-SPEC.md "Per-tool exceptions" for rationale.
        """
        return {
            "num_designs": 1,
            "preset": "preview",
            "post_filter": True,
            "binder_length": 80,
        }

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Build YAML task spec dict for PXDesign.

        The crop field is set to None here — the handler fills it with the
        actual chain length (e.g. ["1-116"]) after reading the target CIF.

        ``target_chain`` may name one chain ("A") or several ("A,B");
        PXDesign's ``target.chains`` is a per-chain map upstream. Hotspots
        may be bare ints (attributed to the first target chain) or
        chain-prefixed ("A296", "B264").

        NOTE: the container's run_pipeline.py rebuilds this spec from the
        cleaned CIF and is the authority — see
        ``docker/pxdesign/run_pipeline.py::build_yaml_spec``, which also
        renumbers hotspots into the cleaned coordinate space. This method
        exists for config serialization, so it mirrors the shape without
        the renumbering.

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target structure inside the container.

        Returns:
            Dict with keys: yaml_spec (dict), preset (str), num_designs (int).
        """
        # Clamp to pilot preset when job_tier=pilot.
        job_spec = merge_pilot_params(job_spec, self.pilot_preset())
        params = job_spec.get("parameters", {})
        chains = parse_target_chains(job_spec.get("target_chain", "A")) or ["A"]
        # Bucket hotspots per chain. A bare int belongs to the first target
        # chain (the historical single-chain shape); "B264" names its chain.
        hotspots_by_chain = group_hotspots_by_chain(
            job_spec.get("hotspot_residues", []), chains
        )

        # PXDesign takes a scalar length only (80) — never a {min, max} dict.
        # The container's build_yaml_spec is the enforcing choke point; this
        # method only serializes config, so it mirrors the value as given.
        binder_length = params.get("binder_length", 80)
        num_designs = params.get("num_designs", 100)
        # "preview", not "basic" — upstream's click.Choice rejects "basic".
        preset = params.get("preset", "preview")

        yaml_spec = {
            "target": {
                "file": target_local_path,
                "chains": {
                    chain: {
                        "crop": None,  # handler fills after reading CIF
                        "hotspots": hotspots_by_chain[chain],
                    }
                    for chain in chains
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
