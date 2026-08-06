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

    def smoke_preset(self) -> dict:
        """Smoke: N=1, 2 trajectories, no filters — cheapest end-to-end proof.

        Unlike RFdiffusion there is no stage to stub out: BindCraft is one
        monolithic process that hallucinates, redesigns with MPNN and
        AF2-validates internally, so the only levers are *how much* of that
        it does. Hence every knob here is a cost bound rather than a
        skip-flag.

        ``num_designs`` maps to BindCraft's ``number_of_final_designs``,
        which counts *accepted* designs — it is a stop condition, not a work
        estimate. On a hard epitope BindCraft will keep burning trajectories
        chasing it until the 4 h session cap. Three separate bounds exist
        because none of them is sufficient alone:

        * ``max_trajectories=2`` caps BindCraft's own trajectory counter. It
          is NOT a complete bound: ``check_n_trajectories`` counts PDBs in
          ``Trajectory/Relaxed``, and a trajectory that terminates early
          (CA clash, final pLDDT < 0.7, or < 3 interface contacts) is moved
          to ``Trajectory/Clashing`` or ``Trajectory/LowConfidence`` and
          never counted. A target where every trajectory terminates loops
          forever under this setting alone.
        * ``filter_set="no_filters.json"`` (shipped by FreeBindCraft, all
          thresholds null) makes acceptance certain for any trajectory that
          reaches MPNN. Smoke exists to prove the plumbing emits a design,
          not to prove the design is good — scoring the design is what
          mini_pilot and pilot are for.
        * a wall-clock cap on the BindCraft subprocess, applied in
          ``docker/bindcraft/run_pipeline.py``, is the only true bound.

        ``advanced_overrides`` is JSON-patched into a copy of
        ``settings_advanced/default_4stage_multimer.json``; the default file
        on disk is never mutated. ``optimise_beta`` is off for smoke only:
        it triples design recycles on beta-rich targets (every Ig fold, so
        it fires on exactly the targets we care about) and is a pure quality
        lever, so it is the right thing to drop when buying a plumbing test.
        """
        return {
            "num_designs": 1,
            "max_trajectories": 2,
            "binder_length": {"min": 55, "max": 65},
            "filter_set": "no_filters.json",
            "advanced_overrides": {
                "max_trajectories": 2,
                # ~140 design iterations -> ~73. Halves per-trajectory cost.
                "soft_iterations": 40,
                "temporary_iterations": 25,
                "hard_iterations": 3,
                "greedy_iterations": 5,
                # Validate one MPNN sequence at 1 recycle instead of two at 3.
                "num_seqs": 8,
                "max_mpnn_sequences": 1,
                "num_recycles_validation": 1,
                # 3x design recycles on beta-rich targets — see docstring.
                "optimise_beta": False,
                # Plots/animations are already off via CLI flags; also clear
                # the settings so nothing writes into the archived work dir.
                "save_design_animations": False,
                "save_design_trajectory_plots": False,
                "zip_animations": False,
                "zip_plots": False,
            },
        }

    def mini_pilot_preset(self) -> dict:
        """Mini-pilot: N=2, 5 trajectories, real filters and real scoring.

        The success gate. Unlike smoke this keeps ``default_filters.json``
        and BindCraft's own iteration counts, so every score in
        ``final_design_stats.csv`` is a real AF2 number and a returned
        design is one BindCraft would have accepted in production. Only the
        trajectory ceiling and the wall-clock cap differ from a pilot.
        """
        return {
            "num_designs": 2,
            "max_trajectories": 5,
            "binder_length": {"min": 55, "max": 65},
            "filter_set": "default_filters.json",
            "advanced_overrides": {
                "max_trajectories": 5,
                "save_design_animations": False,
                "save_design_trajectory_plots": False,
                "zip_animations": False,
                "zip_plots": False,
            },
        }

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
