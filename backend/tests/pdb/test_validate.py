"""Tests for pre-flight PDB validation (AGENT-04)."""
import pytest


class TestHotspotAccessibility:
    """AGENT-04: Buried hotspot residues flagged with warning."""

    def test_buried_hotspot(self, test_pdb_path):
        """Residue with SASA < 1.0 returns accessible=False and warning string."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_accessible_hotspot(self, test_pdb_path):
        """Surface-exposed residue returns accessible=True and warning=None."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_missing_residue(self, test_pdb_path):
        """Residue number not in chain returns accessible=False with 'not found' warning."""
        pytest.skip("STUB — implementation in Plan 02-02")


class TestPDBQualityChecks:
    """AGENT-04: PDB quality pre-flight checks."""

    def test_low_resolution_warning(self):
        """Structure with resolution > 3.0 produces warn status."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_no_standard_residues_fails(self):
        """Structure with no standard amino acids produces fail status."""
        pytest.skip("STUB — implementation in Plan 02-02")
