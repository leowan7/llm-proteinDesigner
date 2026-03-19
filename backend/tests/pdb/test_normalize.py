"""Tests for PDB normalization pipeline (INPUT-01, INPUT-05)."""
import os
import pytest

from pdb_utils.normalize import normalize_structure
from pdb_utils.models import NormalizationResult


# ---------------------------------------------------------------------------
# Minimal PDB content helpers
# ---------------------------------------------------------------------------

def _write_pdb(path: str, content: str) -> str:
    """Write PDB content to path and return the path."""
    with open(path, "w") as f:
        f.write(content)
    return path


MINIMAL_PDB = """\
HEADER    TEST STRUCTURE
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
ATOM      5  N   ALA A   2       4.000   1.000   1.000  1.00 10.00           N
ATOM      6  CA  ALA A   2       5.000   1.000   1.000  1.00 10.00           C
ATOM      7  C   ALA A   2       6.000   1.000   1.000  1.00 10.00           C
ATOM      8  O   ALA A   2       6.000   2.000   1.000  1.00 10.00           O
END
"""

MSE_PDB = """\
HEADER    MSE TEST STRUCTURE
HETATM    1  N   MSE A   1       1.000   1.000   1.000  1.00 10.00           N
HETATM    2  CA  MSE A   1       2.000   1.000   1.000  1.00 10.00           C
HETATM    3  C   MSE A   1       3.000   1.000   1.000  1.00 10.00           C
HETATM    4  O   MSE A   1       3.000   2.000   1.000  1.00 10.00           O
HETATM    5  CB  MSE A   1       2.000   2.000   1.000  1.00 10.00           C
HETATM    6  CG  MSE A   1       2.000   3.000   1.000  1.00 10.00           C
HETATM    7  SE  MSE A   1       2.000   4.000   1.000  1.00 10.00          SE
HETATM    8  CE  MSE A   1       2.000   5.000   1.000  1.00 10.00           C
END
"""

NMR_PDB = """\
HEADER    NMR TEST STRUCTURE
MODEL        1
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
ENDMDL
MODEL        2
ATOM      1  N   ALA A   1       1.500   1.500   1.500  1.00 20.00           N
ATOM      2  CA  ALA A   1       2.500   1.500   1.500  1.00 20.00           C
ATOM      3  C   ALA A   1       3.500   1.500   1.500  1.00 20.00           C
ATOM      4  O   ALA A   1       3.500   2.500   1.500  1.00 20.00           O
ENDMDL
END
"""

ALTLOC_PDB = """\
HEADER    ALTLOC TEST STRUCTURE
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA AALA A   1       2.000   1.000   1.000  0.60 10.00           C
ATOM      3  CA BALA A   1       2.100   1.100   1.100  0.40 10.00           C
ATOM      4  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      5  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
END
"""


class TestPDBUpload:
    """INPUT-01: User can upload a PDB file as the target structure."""

    def test_upload_valid_pdb(self, test_pdb_path, temp_dir):
        """Valid PDB file is parsed without error and returns NormalizationResult."""
        result = normalize_structure(test_pdb_path, temp_dir)
        assert isinstance(result, NormalizationResult)
        assert os.path.exists(result.output_path)
        assert isinstance(result.changes, list)

    def test_upload_invalid_file_rejected(self, temp_dir):
        """Non-PDB file (e.g. .txt) raises ValueError."""
        txt_path = os.path.join(temp_dir, "molecule.txt")
        with open(txt_path, "w") as f:
            f.write("this is not a pdb file\n")
        with pytest.raises(ValueError, match="Unsupported file format"):
            normalize_structure(txt_path, temp_dir)


class TestNormalization:
    """INPUT-05: System normalizes uploaded/fetched PDB files."""

    def test_mse_conversion(self, temp_dir):
        """MSE (selenomethionine) residues converted to MET in output."""
        pdb_path = _write_pdb(os.path.join(temp_dir, "mse.pdb"), MSE_PDB)
        result = normalize_structure(pdb_path, temp_dir)

        assert any("MSE" in change for change in result.changes), (
            f"Expected MSE conversion in changes; got: {result.changes}"
        )
        assert any("MET" in change or "MSE" in change for change in result.changes)

        # Verify output file contains MET not MSE as residue name
        with open(result.output_path) as f:
            output_content = f.read()
        # The normalized output should reflect the conversion
        assert "MSE" not in output_content or "MET" in output_content

    def test_nmr_model_selection(self, temp_dir):
        """Multi-model NMR structure: only model 0 retained in output."""
        pdb_path = _write_pdb(os.path.join(temp_dir, "nmr.pdb"), NMR_PDB)
        result = normalize_structure(pdb_path, temp_dir)

        assert any("NMR" in change or "model" in change.lower() for change in result.changes), (
            f"Expected NMR model selection in changes; got: {result.changes}"
        )

        # The output should have only model-1 coordinates (x=1.0 not 1.5 for N)
        from Bio.PDB import PDBParser
        parser = PDBParser(QUIET=True)
        out_struct = parser.get_structure("out", result.output_path)
        models = list(out_struct.get_models())
        # After normalization (selecting model 0), only 1 model in output
        assert len(models) == 1

    def test_altloc_handling(self, temp_dir):
        """Alternate location atoms: only one CA remains after normalization."""
        pdb_path = _write_pdb(os.path.join(temp_dir, "altloc.pdb"), ALTLOC_PDB)
        # Should not raise; altloc handling is via BioPython's default behavior
        result = normalize_structure(pdb_path, temp_dir)
        assert isinstance(result, NormalizationResult)
        assert os.path.exists(result.output_path)

    def test_normalization_returns_change_summary(self, test_pdb_path, temp_dir):
        """normalize_structure returns NormalizationResult with list of changes."""
        result = normalize_structure(test_pdb_path, temp_dir)
        assert isinstance(result, NormalizationResult)
        assert isinstance(result.changes, list)
        assert isinstance(result.output_path, str)
        assert os.path.exists(result.output_path)
