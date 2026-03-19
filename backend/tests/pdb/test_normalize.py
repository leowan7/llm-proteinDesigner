"""Tests for PDB normalization pipeline (INPUT-01, INPUT-05)."""
import pytest


class TestPDBUpload:
    """INPUT-01: User can upload a PDB file as the target structure."""

    def test_upload_valid_pdb(self, test_pdb_path):
        """Valid PDB file is parsed without error and returns StructureSummary."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_upload_invalid_file_rejected(self, temp_dir):
        """Non-PDB file (e.g. .txt) raises ValueError."""
        pytest.skip("STUB — implementation in Plan 02-02")


class TestNormalization:
    """INPUT-05: System normalizes uploaded/fetched PDB files."""

    def test_mse_conversion(self, test_pdb_path, temp_dir):
        """MSE (selenomethionine) residues converted to MET in output."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_nmr_model_selection(self, temp_dir):
        """Multi-model NMR structure: only model 0 retained in output."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_altloc_handling(self, temp_dir):
        """Alternate location atoms: highest occupancy selected."""
        pytest.skip("STUB — implementation in Plan 02-02")

    def test_normalization_returns_change_summary(self, test_pdb_path, temp_dir):
        """normalize_structure returns NormalizationResult with list of changes."""
        pytest.skip("STUB — implementation in Plan 02-02")
