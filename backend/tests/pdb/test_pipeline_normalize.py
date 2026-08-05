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
    normalize_for_rfantibody,
    normalize_for_rfdiffusion,
    parse_target_chains,
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


# Two protein chains + a ligand chain. Stands in for the driving use case:
# an IgG1 Fc homodimer where binders grip BOTH protomers, so a design run
# filtered down to one chain is aimed at half the epitope. Chain A is
# author-numbered 20-21, chain B 30-31 — deliberately different offsets so
# a per-chain renumber map can be told apart from a global one.
TWO_CHAIN_PLUS_LIGAND_PDB = """\
HEADER    HOMODIMER PLUS LIGAND
ATOM      1  N   ALA A  20       1.000   1.000   1.000  1.00 10.00           N
ATOM      2  CA  ALA A  20       2.000   1.000   1.000  1.00 10.00           C
ATOM      3  C   ALA A  20       3.000   1.000   1.000  1.00 10.00           C
ATOM      4  O   ALA A  20       3.000   2.000   1.000  1.00 10.00           O
ATOM      5  N   GLY A  21       4.000   1.000   1.000  1.00 10.00           N
ATOM      6  CA  GLY A  21       5.000   1.000   1.000  1.00 10.00           C
ATOM      7  C   GLY A  21       6.000   1.000   1.000  1.00 10.00           C
ATOM      8  O   GLY A  21       6.000   2.000   1.000  1.00 10.00           O
ATOM      9  N   SER B  30      21.000   1.000   1.000  1.00 10.00           N
ATOM     10  CA  SER B  30      22.000   1.000   1.000  1.00 10.00           C
ATOM     11  C   SER B  30      23.000   1.000   1.000  1.00 10.00           C
ATOM     12  O   SER B  30      23.000   2.000   1.000  1.00 10.00           O
ATOM     13  N   THR B  31      24.000   1.000   1.000  1.00 10.00           N
ATOM     14  CA  THR B  31      25.000   1.000   1.000  1.00 10.00           C
ATOM     15  C   THR B  31      26.000   1.000   1.000  1.00 10.00           C
ATOM     16  O   THR B  31      26.000   2.000   1.000  1.00 10.00           O
HETATM   17  C1  NAG C 200       9.000   9.000   9.000  1.00 10.00           C
HETATM   18  C2  NAG C 200      10.000   9.000   9.000  1.00 10.00           C
END
"""


# Pure ligands, no protein at all.
LIGAND_ONLY_PDB = """\
HEADER    LIGAND ONLY
HETATM    1  C1  NAG B 200       9.000   9.000   9.000  1.00 10.00           C
HETATM    2  C2  NAG B 200      10.000   9.000   9.000  1.00 10.00           C
END
"""


def _atom_line(
    serial: int, name: str, altloc: str, resname: str, chain: str, resnum: int,
    x: float, y: float, z: float, occ: float = 1.0, bfac: float = 10.0,
    element: str = "",
) -> str:
    """Emit a PDB ATOM record with column-perfect alignment.

    PDB v3 fixed-width format. Position reference:
        cols  1-6   "ATOM  "
        cols  7-11  atom serial (right)
        cols 13-16  atom name (left-pad: e.g. " CA ")
        col  17     altloc (or ' ')
        cols 18-20  residue name (right)
        col  22     chain
        cols 23-26  resnum (right)
        cols 31-38  x (%8.3f)
        cols 39-46  y (%8.3f)
        cols 47-54  z (%8.3f)
        cols 55-60  occupancy (%6.2f)
        cols 61-66  temp factor (%6.2f)
        cols 77-78  element symbol (right)
    """
    # Atom-name field is 4 chars; conventional placement is to put the
    # element symbol at column 13 if it's a metal or 2-letter element, else
    # leave column 13 blank and right-justify within 13-16.
    if len(name) >= 4:
        name_field = name[:4]
    else:
        name_field = " " + name.ljust(3)
    altloc_field = altloc if altloc else " "
    resname_field = resname[:3].rjust(3)
    serial_field = str(serial)[:5].rjust(5)
    resnum_field = str(resnum)[:4].rjust(4)
    element_field = (element or name.strip()[:1]).rjust(2)
    return (
        f"ATOM  {serial_field} {name_field}{altloc_field}{resname_field} "
        f"{chain}{resnum_field}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{occ:6.2f}{bfac:6.2f}          {element_field}\n"
    )


def _pdb(*lines: str) -> str:
    return "HEADER    SYNTH\n" + "".join(lines) + "END\n"


# Multi-altloc: residue 1 has CA at altloc A (occ 0.5) AND altloc B (occ 0.5).
# Plus CB split across A/B. Backbone N/C/O at altloc ' '. This is the
# 3IUT / 3KKU pattern that produced the RFdiffusion "Non-positive
# determinant" crash for hcruz@indicasat.org.pa on 2026-06-03.
ALTLOC_MIXED_BACKBONE_PDB = _pdb(
    _atom_line(1, "N",  "", "ASN", "A", 1,  1.0,  0.0,  1.0, 1.0, 8.0),
    _atom_line(2, "CA", "A", "ASN", "A", 1, 2.0, -1.0,  1.0, 0.5, 8.5, "C"),
    _atom_line(3, "CA", "B", "ASN", "A", 1, 2.1, -1.1,  1.1, 0.5, 9.0, "C"),
    _atom_line(4, "C",  "", "ASN", "A", 1,  3.0, -1.0,  1.0, 1.0, 9.0, "C"),
    _atom_line(5, "O",  "", "ASN", "A", 1,  3.0, -2.0,  1.0, 1.0, 11.0, "O"),
    _atom_line(6, "CB", "A", "ASN", "A", 1, 2.5, -2.0,  2.0, 0.5, 9.0, "C"),
    _atom_line(7, "CB", "B", "ASN", "A", 1, 2.6, -1.9,  2.1, 0.5, 11.0, "C"),
    _atom_line(8, "N",  "", "GLY", "A", 2,  4.0,  0.0,  1.0, 1.0, 8.0),
    _atom_line(9, "CA", "", "GLY", "A", 2,  5.0, -1.0,  1.0, 1.0, 8.0, "C"),
    _atom_line(10, "C", "", "GLY", "A", 2,  6.0, -1.0,  1.0, 1.0, 8.0, "C"),
    _atom_line(11, "O", "", "GLY", "A", 2,  6.0, -2.0,  1.0, 1.0, 9.0, "O"),
)


# Altloc with mixed occupancy: A=0.7, B=0.3. A coords must win.
ALTLOC_OCCUPANCY_PDB = _pdb(
    _atom_line(1, "N",  "", "ALA", "A", 1,  1.0, 1.0, 1.0),
    _atom_line(2, "CA", "A", "ALA", "A", 1, 2.0, 1.0, 1.0, 0.7, 10.0, "C"),
    _atom_line(3, "CA", "B", "ALA", "A", 1, 2.5, 1.5, 1.5, 0.3, 10.0, "C"),
    _atom_line(4, "C",  "", "ALA", "A", 1,  3.0, 1.0, 1.0, 1.0, 10.0, "C"),
    _atom_line(5, "O",  "", "ALA", "A", 1,  3.0, 2.0, 1.0, 1.0, 10.0, "O"),
    _atom_line(6, "N",  "", "GLY", "A", 2,  4.0, 1.0, 1.0),
    _atom_line(7, "CA", "", "GLY", "A", 2,  5.0, 1.0, 1.0, 1.0, 10.0, "C"),
    _atom_line(8, "C",  "", "GLY", "A", 2,  6.0, 1.0, 1.0, 1.0, 10.0, "C"),
    _atom_line(9, "O",  "", "GLY", "A", 2,  6.0, 2.0, 1.0, 1.0, 10.0, "O"),
)


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
    normalize_for_pipeline(
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
    ca_lines = [line for line in out_text.splitlines() if " CA " in line and " ALA A" in line]
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
# Tests: altloc collapse (rfantibody hcruz@indicasat fix, 2026-06-03)
# ---------------------------------------------------------------------------

def _count_altloc_letters(path: str) -> int:
    """Count ATOM/HETATM records whose altloc column (index 16) is a non-blank letter."""
    n = 0
    with open(path) as fh:
        for line in fh:
            if line.startswith(("ATOM", "HETATM")) and len(line) > 16:
                if line[16] != " ":
                    n += 1
    return n


def test_altloc_collapsed_in_output(tmp_path):
    """Multi-altloc input must collapse to one record per atom name with
    altloc column blanked. RFdiffusion produced a degenerate rotation
    frame mid-denoise on multi-altloc 3IUT/3KKU inputs (hcruz incident
    2026-06-03) because the upstream normalizer let both altloc copies
    through."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), ALTLOC_MIXED_BACKBONE_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    # Both residues survive (residue 1 has complete backbone after altloc choice).
    assert report.residues_kept_per_chain.get("A") == 2
    # Residue 1 had CA in altloc A AND altloc B, plus CB in altloc A AND altloc B
    # — 4 altloc records total. After collapse we keep one of each (2), so 2 dropped.
    assert report.altloc_records_collapsed == 2
    # Output file has no altloc letters left.
    assert _count_altloc_letters(out) == 0
    # And exactly one CA line per residue.
    out_text = open(out).read()
    ca_lines = [line for line in out_text.splitlines() if " CA " in line]
    assert len(ca_lines) == 2  # one per residue, no duplicates


def test_altloc_winner_is_highest_occupancy(tmp_path):
    """When altloc A has occupancy 0.7 and altloc B has 0.3, the A coords
    must survive."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), ALTLOC_OCCUPANCY_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert report.altloc_records_collapsed == 1
    out_text = open(out).read()
    ca_lines = [line for line in out_text.splitlines() if " CA " in line and "ALA" in line]
    assert len(ca_lines) == 1
    # The A coords are (2.000, 1.000, 1.000); the B coords are (2.500, 1.500, 1.500).
    assert "2.000" in ca_lines[0] and "2.500" not in ca_lines[0]


def test_altloc_change_recorded_in_changes(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), ALTLOC_MIXED_BACKBONE_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert any("alternate-conformation" in c for c in report.changes)


def test_clean_input_records_zero_altloc_collapse(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), CLEAN_TWO_RES_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert report.altloc_records_collapsed == 0
    # Changes list should NOT mention altloc when there is nothing to collapse.
    assert not any("alternate-conformation" in c for c in report.changes)


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


# ---------------------------------------------------------------------------
# Tests: multi-chain targets (IgG1 Fc homodimer use case)
#
# The wrappers were written single-chain and silently discarded the
# multi-chain capability the upstream models have. A one-chain filter on a
# two-protomer target designs against half the epitope while still
# returning plausible-looking output, so these tests assert on the surviving
# structure, not just on the report.
# ---------------------------------------------------------------------------

def _chains_in_pdb(path: str) -> list:
    """Chain ids actually present in ATOM records of a written PDB."""
    seen: list = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("ATOM") and len(line) > 21:
                cid = line[21]
                if cid not in seen:
                    seen.append(cid)
    return seen


def _resnums_for_chain(path: str, chain: str) -> list:
    nums: list = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("ATOM") and len(line) > 26 and line[21] == chain:
                n = int(line[22:26])
                if n not in nums:
                    nums.append(n)
    return nums


# --- parse_target_chains ---------------------------------------------------

def test_parse_target_chains_scalar():
    assert parse_target_chains("A") == ["A"]


def test_parse_target_chains_comma_string():
    assert parse_target_chains("A,B") == ["A", "B"]


def test_parse_target_chains_tolerates_whitespace():
    assert parse_target_chains(" A , B ") == ["A", "B"]


def test_parse_target_chains_preserves_caller_order():
    """Order drives contig and FASTA concatenation downstream — it must not
    be silently sorted."""
    assert parse_target_chains("B,A") == ["B", "A"]


def test_parse_target_chains_dedupes():
    assert parse_target_chains("A,B,A") == ["A", "B"]


def test_parse_target_chains_accepts_sequence():
    assert parse_target_chains(["A", "B"]) == ["A", "B"]


def test_parse_target_chains_none_and_empty_mean_no_filter():
    assert parse_target_chains(None) is None
    assert parse_target_chains("") is None
    assert parse_target_chains("  ") is None
    assert parse_target_chains([]) is None


# --- structure survives with every requested chain --------------------------

def test_two_chain_target_keeps_both_chains(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A,B")
    assert report.chains_kept == ["A", "B"]
    assert report.residues_kept_per_chain == {"A": 2, "B": 2}


def test_two_chain_output_file_actually_contains_both_chains(tmp_path):
    """The check every other assertion can pass without: does the file on
    disk that the GPU tool reads still hold both protomers?"""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    normalize_for_pipeline(inp, out, target_chain="A,B")
    assert _chains_in_pdb(out) == ["A", "B"]


def test_two_chain_target_still_drops_ligand_chain(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A,B")
    assert "C" not in report.chains_kept
    assert "C" not in _chains_in_pdb(out)


def test_chains_requested_records_caller_order(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="B,A")
    assert report.chains_requested == ["B", "A"]
    # chains_kept stays sorted (historical contract); order lives in
    # chains_requested.
    assert report.chains_kept == ["A", "B"]


def test_chains_requested_empty_when_no_filter(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out)
    assert report.chains_requested == []


# --- backward compatibility -------------------------------------------------

def test_single_chain_on_multi_chain_input_still_drops_the_others(tmp_path):
    """Regression guard: target_chain="A" must behave exactly as before and
    NOT start letting chain B through."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(inp, out, target_chain="A")
    assert report.chains_kept == ["A"]
    assert _chains_in_pdb(out) == ["A"]


def test_scalar_and_single_element_list_are_byte_identical(tmp_path):
    """"A" and ["A"] must produce the same file, byte for byte."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out_scalar = str(tmp_path / "scalar.pdb")
    out_list = str(tmp_path / "list.pdb")
    normalize_for_pipeline(inp, out_scalar, target_chain="A")
    normalize_for_pipeline(inp, out_list, target_chain=["A"])
    assert open(out_scalar, "rb").read() == open(out_list, "rb").read()


def test_single_chain_missing_keeps_historical_error_message(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    with pytest.raises(ValueError, match="Target chain 'Z' is not present"):
        normalize_for_pipeline(inp, out, target_chain="Z")


# --- partially-missing multi-chain selector is a hard error -----------------

def test_partially_missing_multi_chain_raises(tmp_path):
    """"A,Z" must fail loudly rather than quietly designing against A alone."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    with pytest.raises(ValueError, match=r"Target chain\(s\) \['Z'\]"):
        normalize_for_pipeline(inp, out, target_chain="A,Z")


def test_multi_chain_naming_a_ligand_chain_raises(tmp_path):
    """Chain C is NAG-only — it survives no protein filter, so naming it as
    a target must raise instead of silently yielding an A-only structure."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    with pytest.raises(ValueError, match=r"Target chain\(s\) \['C'\]"):
        normalize_for_pipeline(inp, out, target_chain="A,C")


# --- renumbering is per-chain ----------------------------------------------

def test_multi_chain_renumber_map_is_per_chain(tmp_path):
    """Each chain restarts at 1, and the map keys keep the chain id so a
    hotspot on B is never confused with one on A."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pipeline(
        inp, out, target_chain="A,B", renumber_residues=True,
    )
    assert report.renumber_map == {
        ("A", 20): 1, ("A", 21): 2,
        ("B", 30): 1, ("B", 31): 2,
    }


def test_multi_chain_renumbered_output_restarts_each_chain(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    normalize_for_pipeline(
        inp, out, target_chain="A,B", renumber_residues=True,
    )
    assert _resnums_for_chain(out, "A") == [1, 2]
    assert _resnums_for_chain(out, "B") == [1, 2]


# --- presets ----------------------------------------------------------------

def test_pxdesign_preset_multi_chain(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_pxdesign(inp, out, target_chain="A,B")
    assert report.chains_kept == ["A", "B"]
    assert _chains_in_pdb(out) == ["A", "B"]
    assert report.renumber_map == {
        ("A", 20): 1, ("A", 21): 2,
        ("B", 30): 1, ("B", 31): 2,
    }


def test_rfdiffusion_preset_multi_chain_preserves_author_numbering(tmp_path):
    """RFdiffusion contigs and ppi.hotspot_res reference author numbering —
    a multi-chain target must not start renumbering."""
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_rfdiffusion(inp, out, target_chain="A,B")
    assert report.chains_kept == ["A", "B"]
    assert report.renumber_map == {}
    assert _resnums_for_chain(out, "A") == [20, 21]
    assert _resnums_for_chain(out, "B") == [30, 31]


def test_rfantibody_preset_accepts_multi_chain_selector(tmp_path):
    inp = _write_pdb(str(tmp_path / "input.pdb"), TWO_CHAIN_PLUS_LIGAND_PDB)
    out = str(tmp_path / "out.pdb")
    report = normalize_for_rfantibody(inp, out, target_chain="A,B")
    assert report.chains_kept == ["A", "B"]
    assert report.renumber_map == {}
