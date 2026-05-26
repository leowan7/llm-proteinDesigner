"""Tests for pre-flight PDB validation (AGENT-04)."""

from pdb_utils.validate import check_hotspot_accessibility, run_preflight_checks
from pdb_utils.models import StructureSummary


def _make_summary(
    resolution: float | None = 2.0,
    method: str = "X-ray",
    residue_count: int = 3,
) -> StructureSummary:
    """Helper: construct a StructureSummary for preflight check tests."""
    return StructureSummary(
        pdb_id="TEST",
        protein_name="Test Protein",
        resolution=resolution,
        method=method,
        chain_count=1,
        selected_chain="A",
        residue_count=residue_count,
        normalization_changes=[],
    )


class TestHotspotAccessibility:
    """AGENT-04: Buried hotspot residues flagged with warning."""

    def test_buried_hotspot(self, test_pdb_path):
        """Residue with low SASA returns accessible=False and warning containing 'buried'."""
        # test_structure.pdb has 3 ALA residues in a single straight chain.
        # In a minimal straight-chain structure, residue 1 has reduced SASA
        # compared to isolated residues; we check that the function returns
        # a HotspotCheck with the correct shape.
        results = check_hotspot_accessibility(test_pdb_path, "A", [1], sasa_threshold=9999.0)
        assert len(results) == 1
        check = results[0]
        assert check.residue_number == 1
        assert check.accessible is False
        assert check.warning is not None
        assert "buried" in check.warning.lower() or "SASA" in check.warning

    def test_accessible_hotspot(self, test_pdb_path):
        """Surface-exposed residue (sasa_threshold=0.0) returns accessible=True, warning=None."""
        results = check_hotspot_accessibility(test_pdb_path, "A", [1], sasa_threshold=0.0)
        assert len(results) == 1
        check = results[0]
        assert check.accessible is True
        assert check.warning is None

    def test_missing_residue(self, test_pdb_path):
        """Residue number not in chain returns accessible=False with 'not found' warning."""
        results = check_hotspot_accessibility(test_pdb_path, "A", [999])
        assert len(results) == 1
        check = results[0]
        assert check.accessible is False
        assert check.warning is not None
        assert "not found" in check.warning.lower()


class TestPDBQualityChecks:
    """AGENT-04: PDB quality pre-flight checks."""

    def test_low_resolution_warning(self, test_pdb_path):
        """Structure with resolution > 3.0 produces a ValidationResult with status='warn'."""
        summary = _make_summary(resolution=4.0)
        results = run_preflight_checks(summary, test_pdb_path)
        resolution_checks = [r for r in results if r.check_name == "resolution"]
        assert len(resolution_checks) >= 1
        assert resolution_checks[0].status == "warn"

    def test_no_standard_residues_fails(self, test_pdb_path, tmp_path):
        """Structure with no standard amino acids produces a ValidationResult with status='fail'."""
        # Write a PDB with only HOH (water) records — no amino acids
        water_pdb = tmp_path / "water.pdb"
        water_pdb.write_text(
            "HETATM    1  O   HOH A   1       1.000   1.000   1.000  1.00  0.00           O\n"
            "END\n"
        )
        summary = _make_summary()
        results = run_preflight_checks(summary, str(water_pdb))
        aa_checks = [r for r in results if r.check_name == "standard_residues"]
        assert len(aa_checks) >= 1
        assert aa_checks[0].status == "fail"
