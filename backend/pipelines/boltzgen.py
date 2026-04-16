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
from pipelines.base import ToolPipeline


class BoltzGenPipeline(ToolPipeline):
    """Pipeline for BoltzGen structure generation jobs."""

    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
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
        chain = job_spec.get("target_chain", "A")
        hotspots = job_spec.get("hotspot_residues", [])

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
                "include": [{"chain": {"id": chain}}],
            },
        }

        # Add binding_types if hotspot residues are specified
        if hotspots:
            binding_str = ",".join(str(r) for r in sorted(hotspots))
            file_entity["file"]["binding_types"] = [
                {"chain": {"id": chain, "binding": binding_str}},
            ]

        # Protein entity for the binder (designable sequence)
        binder_entity = {
            "protein": {
                "id": "B",
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
