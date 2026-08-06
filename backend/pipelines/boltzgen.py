"""BoltzGen pipeline: config generator and result parser.

BoltzGen uses a YAML design specification with two entity types:
  - file entity: target structure (CIF format, chains starting at residue 1)
  - protein entity: binder to be designed (sequence length range)

Hotspot residues are specified as binding_types on the file entity.
The binder is a separate protein entity with a designable sequence range.

Expected runtime: 15-60 minutes per batch on A100 80GB, depending on
num_designs (intermediate count) and budget (final filtered count).
"""

from jobs.models import CandidateResult
from pdb_utils.pipeline_normalize import (
    group_hotspots_by_chain,
    parse_target_chains,
)

from pipelines.base import ToolPipeline, merge_pilot_params


class BoltzGenPipeline(ToolPipeline):
    """Pipeline for BoltzGen structure generation jobs."""

    @property
    def gpu_sku(self) -> str:
        """BoltzGen fits in 40GB for the protein-anything protocol."""
        return "A100-40GB"

    def pilot_preset(self) -> dict:
        """Pilot: 2 final designs, low intermediate count. ~10-15 min run —
        minimal validation that the BoltzGen model loads and emits binders."""
        return {"budget": 2, "num_designs": 500}

    def smoke_preset(self) -> dict:
        """Smoke: 1 design, budget 1, shortest binder. Cheapest possible
        end-to-end pipeline validation. Scores may be stubbed if BoltzGen
        doesn't emit a metrics CSV. See docs/SMOKE-TEST-SPEC.md.
        """
        return {
            "num_designs": 1,
            "budget": 1,
            "protocol": "protein-anything",
            "binder_length": {"min": 30, "max": 40},
        }

    def mini_pilot_preset(self) -> dict:
        """Mini-pilot: 2 designs, budget 2, real scoring.

        Final success gate for Phase 4. Must produce two candidates with real
        parseable PDBs and real ipTM/pLDDT/refolding_rmsd floats.
        """
        return {
            "num_designs": 2,
            "budget": 2,
            "protocol": "protein-anything",
            "binder_length": {"min": 50, "max": 70},
        }

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        # Clamp to pilot preset when job_tier=pilot.
        job_spec = merge_pilot_params(job_spec, self.pilot_preset())
        """Build YAML design spec dict for BoltzGen.

        Produces the correct BoltzGen YAML format:
          entities:
            - file:
                path: <target_cif>
                include:
                  - chain: {id: <chain>}
                binding_types:           # only if hotspots specified
                  - chain: {id: <chain>, binding: "50,51,52"}
            - protein:
                id: B
                sequence: "50..100"      # binder length range

        Args:
            job_spec: Deserialized JobSpec dict.
            target_local_path: Path to target structure inside the container.

        Returns:
            Dict with keys: yaml_spec (dict), protocol (str),
            num_designs (int), budget (int).
        """
        params = job_spec.get("parameters", {})
        # target_chain may name one chain ("A") or several ("A,B"):
        # BoltzGen's include: and binding_types: are both per-chain lists.
        chains = parse_target_chains(job_spec.get("target_chain", "A")) or ["A"]
        hotspots_by_chain = group_hotspots_by_chain(
            job_spec.get("hotspot_residues", []), chains
        )

        # Binder length range from parameters.
        binder_length = params.get("binder_length", {"min": 50, "max": 100})
        if isinstance(binder_length, dict):
            min_len = binder_length.get("min", 50)
            max_len = binder_length.get("max", 100)
        else:
            min_len, max_len = 50, 100

        # File entity for the target structure
        file_entity = {
            "file": {
                "path": target_local_path,
                "include": [{"chain": {"id": c}} for c in chains],
            },
        }

        # Add binding_types for each chain that carries hotspots
        binding_entries = [
            {"chain": {"id": c, "binding": ",".join(
                str(r) for r in sorted(hotspots_by_chain[c])
            )}}
            for c in chains if hotspots_by_chain[c]
        ]
        if binding_entries:
            file_entity["file"]["binding_types"] = binding_entries

        # Protein entity for the binder (designable sequence). The id must
        # not collide with a target chain — "B" is right for a single "A"
        # target but clashes with the second protomer of an "A,B" target.
        taken = {str(c).strip().upper() for c in chains}
        binder_id = next(
            (ltr for ltr in "BCDEFGHIJKLMNOPQRSTUVWXYZA" if ltr not in taken),
            "B",
        )
        binder_entity = {
            "protein": {
                "id": binder_id,
                "sequence": f"{min_len}..{max_len}",
            },
        }

        yaml_spec = {"entities": [file_entity, binder_entity]}

        # num_designs = intermediate trajectory count (10,000-60,000 typical)
        # budget = final filtered design count (10-100 typical)
        return {
            "yaml_spec": yaml_spec,
            "protocol": params.get("protocol", "protein-anything"),
            "num_designs": params.get("num_designs", 10000),
            "budget": params.get("budget", 60),
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
