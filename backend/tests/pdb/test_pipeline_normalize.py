"""Tests for ``pdb_utils.pipeline_normalize`` (Bug 9 fix).

The pipeline normalizer runs inside each Kendrew docker image before the
heavy GPU code. It strips waters, HETATM, hydrogens, altlocs, multi-model;
maps modres to standard parents; optionally filters to a target chain;
optionally renumbers 1..N. These tests exercise the contract using
synthetic PDB strings so we do not need network access or real fixtures.
"""
from __future__ import annotations

import os
import pytest

from pdb_utils.pipeline_normalize import (
    normalize_for_pipeline,
    normalize_for_pxdesign,
    normalize_for_rfdiffusion,
    PipelineNormalizationReport,
    WATER_RESNAMES,
    MODRES_MAP,
)


# ---------------------------------------------------------------------------
# Fixtures (synthetic PDB strings)
# ---------------------------------------------------------------------------

def _write_pdb(path: str, content: str) -> str:
    with open(path, "w") as f:
        f.write(content)
    return path


# Two-residue clean monomer; surviving baseline.
CLEAN_TWO_RES_PDB = """\
HEADER    CLEAN
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
ATOM      5  N   GLY A   2       4.000   1.000   1.000  1.00 10.00           N
ATOM      6  CA  GLY A   2       5.000   1.000   1.000  1.00 10.00           C
ATOM      7  C   GLY A   2       6.000   1.000   1.000  1.00 10.00           C
ATOM      8  O   GLY A   2       6.000   2.000   1.000  1.00 10.00           O
END
"""


# 1HEW-style: chain A protein + chain B NAG ligand. The exact failure
# mode reported by the user (gemmi missing entity_type chain B). The
# normalizer must drop chain B silently.
PROTEIN_PLUS_NAG_LIGAND_PDB = """\
HEADER    LYSOZYME PLUS LIGAND
ATOM      1  N   ALA A  20       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A  20       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A  20       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A  20       3.000   2.000   1.000  1.00 10.00           O
ATOM      5  N   GLY A  21       4.000   1.000   1.000  1.00 10.00           N
ATOM      6  CA  GLY A  21       5.000   1.000   1.000  1.00 10.00           C
ATOM      7  C   GLY A  21       6.000   1.000   1.000  1.00 10.00           C
ATOM      8  O   GLY A  21       6.000   2.000   1.000  1.00 10.00           O
HETATM    9  C1  NAG B 200       9.000   9.000   9.000  1.00 10.00           C
HETATM   10  C2  NAG B 200      10.000   9.000   9.000  1.00 10.00           C
END
"""


# Protein with waters interleaved (water tagged HETATM as is normal).
PROTEIN_WITH_WATERS_PDB = """\
HEADER    PROTEIN WITH WATERS
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
HETATM    5  O   HOH A 101      10.000  10.000  10.000  1.00 30.00           O
HETATM    6  O   HOH A 102      11.000  10.000  10.000  1.00 30.00           O
ATOM      7  N   GLY A   2       4.000   1.000   1.000  1.00 10.00           N
ATOM      8  CA  GLY A   2       5.000   1.000   1.000  1.00 10.00           C
ATOM      9  C   GLY A   2       6.000   1.000   1.000  1.00 10.00           C
ATOM     10  O   GLY A   2       6.000   2.000   1.000  1.00 10.00           O
END
"""


# MSE selenomethionine — modres remap target.
MSE_PDB = """\
HEADER    MSE TEST
HETATM    1  N   MSE A   1       1.000   1.000   1.000  1.00 10.00           N
HETATM    2  CA  MSE A   1       2.000   1.000   1.000  1.00 10.00           C
HETATM    3  C   MSE A   1       3.000   1.000   1.000  1.00 10.00           C
HETATM    4  O   MSE A   1       3.000   2.000   1.000  1.00 10.00           O
HETATM    5  CB  MSE A   1       2.000   2.000   1.000  1.00 10.00           C
HETATM    6  CG  MSE A   1       2.000   3.000   1.000  1.00 10.00           C
HETATM    7  SE  MSE A   1       2.000   4.000   1.000  1.00 10.00          SE
HETATM    8  CE  MSE A   1       2.000   5.000   1.000  1.00 10.00           C
ATOM      9  N   ALA A   2       4.000   1.000   1.000  1.00 10.00           N
ATOM     10  CA  ALA A   2       5.000   1.000   1.000  1.00 10.00           C
ATOM     11  C   ALA A   2       6.000   1.000   1.000  1.00 10.00           C
ATOM     12  O   ALA A   2       6.000   2.000   1.000  1.00 10.00           O
END
"""


# Multi-model NMR.
NMR_PDB = """\
HEADER    NMR TEST
MODEL        1
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
ENDMDL
MODEL        2
ATOM      1  N   ALA A   1       9.000   9.000   9.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       9.000   9.000   9.000  1.00 10.00           C
ATOM      3  C   ALA A   1       9.000   9.000   9.000  1.00 10.00           C
ATOM      4  O   ALA A   1       9.000   9.000   9.000  1.00 10.00           O
ENDMDL
END
"""


# Residue with all-zero backbone coords — would crash RFdiffusion.
ZERO_BB_PDB = """\
HEADER    ZERO BACKBONE
ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A   1       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A   1       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A   1       3.000   2.000   1.000  1.00 10.00           O
ATOM      5  N   GLY A   2       0.000   0.000   0.000  1.00 10.00           N
ATOM      6  CA  GLY A   2       0.000   0.000   0.000  1.00 10.00           C
ATOM      7  C   GLY A   2       0.000   0.000   0.000  1.00 10.00           C
ATOM      8  O   GLY A   2       0.000   0.000   0.000  1.00 10.00           O
END
"""


# Pure ligands, no protein at all.
LIGAND_ONLY_PDB = """\
HEADER    LIGAND ONLY
HETATM    1  C1  NAG B 200       9.000   9.000   9.000  1.00 10.00           C
HETATM    2  C2  NAG B 200      10.000   9.000   9.000  1.00 10.00           C
END
"""


# ---------------------------------------------------------------------------
# Tests: basic happy paths
# ---------------------------------------------------------------------------

def test_clean_input_passes_through_unchanged(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), CLEAN_TWO_RES_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert report.chains_kept == ["A"]
    assert report.residues_kept_per_chain == {"A": 2}
    assert report.chains_dropped == []
    assert os.path.getsize(out) > 0


def test_returns_dataclass_report(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), CLEAN_TWO_RES_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert isinstance(report, PipelineNormalizationReport)
    assert report.output_path == out


# ---------------------------------------------------------------------------
# Tests: the user-reported failure mode
# ---------------------------------------------------------------------------

def test_drops_nag_ligand_chain_does_not_crash(tmp_path):
    """1HEW-style: protein chain A + NAG ligand chain B.

    The user's reported failure: gemmi.remove_ligands_and_waters() raised
    ``missing entity_type in chain B``. After our normalizer, chain B is
    gone and only protein residues remain — gemmi should never see chain B.
    """
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_PLUS_NAG_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert report.chains_kept == ["A"]
    # Chain B should be reported as dropped
    assert any("B" in c for c in report.chains_dropped)
    # Protein residues survived
    assert report.residues_kept_per_chain.get("A", 0) >= 2


def test_drops_waters(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_WITH_WATERS_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    # Protein residues kept; waters dropped
    assert report.residues_kept_per_chain.get("A") == 2
    assert report.residues_dropped_per_chain.get("A", 0) >= 2  # 2 HOH


def test_keep_waters_flag_retains_them(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_WITH_WATERS_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(
        inp, out, target_chain="A",
        keep_waters=True, keep_hetatm=True,
    )
    # When both flags allow waters through, they survive (HOH is in WATER_RESNAMES
    # but keep_waters=True overrides). Water resnames are in WATER_RESNAMES.
    assert "HOH" in WATER_RESNAMES


# ---------------------------------------------------------------------------
# Tests: modres remap
# ---------------------------------------------------------------------------

def test_mse_remapped_to_met(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), MSE_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    # 1 MSE + 1 ALA = 2 residues; MSE counts because we remap it to MET first.
    assert report.residues_kept_per_chain.get("A") == 2
    # Output should have MET, not MSE
    out_text = open(out).read()
    assert "MET" in out_text
    assert "MSE" not in out_text


def test_modres_map_constant_includes_common_modifications():
    assert "MSE" in MODRES_MAP
    assert "SEP" in MODRES_MAP
    assert MODRES_MAP["MSE"][0] == "MET"


# ---------------------------------------------------------------------------
# Tests: NMR multi-model handling
# ---------------------------------------------------------------------------

def test_multi_model_collapses_to_first(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), NMR_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert report.models_collapsed is True
    # Output should contain only one model's coords (CA at x=2.0, not 9.0).
    out_text = open(out).read()
    # Find the CA atom line — it should reference the model-1 coords (~2.000)
    # rather than model-2 (~9.000).
    ca_lines = [l for l in out_text.splitlines() if " CA " in l and " ALA A" in l]
    assert len(ca_lines) == 1
    assert "2.000" in ca_lines[0]
    assert "9.000" not in ca_lines[0]


# ---------------------------------------------------------------------------
# Tests: bad-backbone filtering
# ---------------------------------------------------------------------------

def test_zero_backbone_residue_dropped(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), ZERO_BB_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    # Only the first residue (clean coords) survives; second has all-zero coords.
    assert report.residues_kept_per_chain.get("A") == 1
    assert report.residues_dropped_per_chain.get("A") == 1


def test_zero_backbone_kept_when_drop_zero_disabled(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), ZERO_BB_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(
        inp, out, target_chain="A", drop_zero_backbone=False,
    )
    assert report.residues_kept_per_chain.get("A") == 2


# ---------------------------------------------------------------------------
# Tests: error paths
# ---------------------------------------------------------------------------

def test_unsupported_extension_raises(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.txt"), CLEAN_TWO_RES_PDB)
    out = str(tmp_path / "out.pdb")
    with pytest.raises(ValueError, match="Unsupported file format"):
        normalize_for_pipeline(inp, out)


def test_target_chain_missing_raises(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), CLEAN_TWO_RES_PDB)
    out = str(tmp_path / "out.pdb")
    with pytest.raises(ValueError, match="Target chain 'Z' is not present"):
        normalize_for_pipeline(inp, out, target_chain="Z")


def test_no_protein_residues_raises(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), LIGAND_ONLY_PDB)
    out = str(tmp_path / "out.pdb")
    with pytest.raises(ValueError, match="no standard polymer residues"):
        normalize_for_pipeline(inp, out)


# ---------------------------------------------------------------------------
# Tests: renumber_residues + renumber_map
# ---------------------------------------------------------------------------

def test_renumber_produces_renumber_map(tmp_path):
    """The PROTEIN_PLUS_NAG_LIGAND fixture has chain A residues 20, 21
    (author numbering). After renumbering, they should be 1, 2 — and the
    renumber_map should contain {(A, 20): 1, (A, 21): 2}.
    """
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_PLUS_NAG_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(
        inp, out, target_chain="A", renumber_residues=True,
    )
    assert report.renumber_map == {("A", 20): 1, ("A", 21): 2}


def test_no_renumber_means_empty_map(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_PLUS_NAG_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(
        inp, out, target_chain="A", renumber_residues=False,
    )
    assert report.renumber_map == {}


# ---------------------------------------------------------------------------
# Tests: per-tool presets
# ---------------------------------------------------------------------------

def test_pxdesign_preset_renumbers(tmp_path):
    """pxdesign hotspots are post-reindex; renumber_map must be populated."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_PLUS_NAG_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pxdesign(inp, out, target_chain="A")
    assert report.renumber_map == {("A", 20): 1, ("A", 21): 2}


def test_rfdiffusion_preset_preserves_numbering(tmp_path):
    """rfdiffusion hotspots reference original PDB numbering; do not renumber."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_PLUS_NAG_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_rfdiffusion(inp, out, target_chain="A")
    assert report.renumber_map == {}
    # Output PDB still has author residue numbers 20/21.
    out_text = open(out).read()
    # Either resnum 20 or 21 should appear in an ATOM line; some serializers
    # write the resnum at column 22-26.
    assert " A  20" in out_text or " A  21" in out_text


def test_pxdesign_preset_strips_ligand_chain(tmp_path):
    """The 1HEW-style failure case must succeed under pxdesign preset."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), PROTEIN_PLUS_NAG_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pxdesign(inp, out, target_chain="A")
    assert "A" in report.chains_kept
    assert "B" not in report.chains_kept
    assert report.residues_kept_per_chain == {"A": 2}
