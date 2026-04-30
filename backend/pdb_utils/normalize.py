"""PDB normalization pipeline.

Handles: NMR multi-model (keep model 0), altloc (keep highest occupancy via
BioPython's default DisorderedAtom behaviour), MSE->MET residue mutation,
and unsupported format rejection.

This module backs the chat / fetch flow (POST /pdb/upload, /pdb/fetch).

For aggressive cleanup needed by GPU pipelines (drop waters, HETATM,
hydrogens, bad-backbone residues; per-tool flag presets), see
``pipeline_normalize.py`` — that module is mounted standalone into each
Kendrew docker image and must not depend on this one.
"""
import os

from Bio.PDB import PDBParser, MMCIFParser, PDBIO, Select
from Bio.PDB.Polypeptide import is_aa

from pdb_utils.models import NormalizationResult


class FirstModelSelect(Select):
    """PDBIO selector that keeps only model 0 (index 0 in the structure)."""

    def accept_model(self, model):
        """Accept only the first model (id == 0 for PDB, may be 1-indexed for NMR).

        Args:
            model: BioPython Model object.

        Returns:
            True if this is the first model encountered.
        """
        return model.get_id() == 0


def normalize_structure(pdb_path: str, output_dir: str) -> NormalizationResult:
    """Normalize a PDB or mmCIF file and write a cleaned output file.

    Normalization steps (in order):
      1. Parse file; reject unsupported extensions with ValueError.
      2. Mutate MSE (selenomethionine) residues to MET in-place.
      3. For NMR multi-model structures, write only model 0 to output.
      4. Verify the structure contains at least one standard amino acid residue.
      5. Write normalized output as PDB format.

    Altloc disambiguation is handled implicitly by BioPython's DisorderedAtom:
    the default atom selected is the one with the highest occupancy. No
    explicit step is required.

    Args:
        pdb_path: Absolute or relative path to the input PDB or mmCIF file.
        output_dir: Directory where the normalized output file will be written.

    Returns:
        NormalizationResult containing the output path and a human-readable
        list of changes applied.

    Raises:
        ValueError: If the file extension is not supported (.pdb, .ent,
                    .cif, .mmcif), or if the structure contains no standard
                    amino acid residues after normalization.
    """
    ext = os.path.splitext(pdb_path)[1].lower()
    if ext in (".cif", ".mmcif"):
        parser = MMCIFParser(QUIET=True)
    elif ext in (".pdb", ".ent"):
        parser = PDBParser(QUIET=True)
    else:
        raise ValueError(
            f"Unsupported file format: {ext}. Expected .pdb, .cif, or .mmcif"
        )

    structure = parser.get_structure("target", pdb_path)
    changes: list[str] = []

    # --- NMR: detect multiple models -------------------------------------
    models = list(structure.get_models())
    if len(models) > 1:
        changes.append(
            f"NMR structure: selected model 1 of {len(models)} (first model only)"
        )

    # --- MSE -> MET mutation ---------------------------------------------
    mse_count = 0
    for residue in structure.get_residues():
        if residue.get_resname().strip() == "MSE":
            residue.resname = "MET"
            for atom in residue:
                if atom.get_name().strip() == "SE":
                    atom.name = " SD "
                    atom.element = "S"
            mse_count += 1
    if mse_count:
        changes.append(
            f"Converted {mse_count} MSE (selenomethionine) residue(s) to MET"
        )

    # --- Verify at least one standard amino acid residue is present ------
    has_aa = any(is_aa(r, standard=True) for r in structure.get_residues())
    if not has_aa:
        raise ValueError(
            "Structure contains no standard amino acid residues after normalization"
        )

    # --- Write output (PDB format, model 0 only for NMR) -----------------
    output_path = os.path.join(output_dir, "normalized.pdb")
    io = PDBIO()
    io.set_structure(structure)
    if len(models) > 1:
        io.save(output_path, FirstModelSelect())
    else:
        io.save(output_path)

    return NormalizationResult(output_path=output_path, changes=changes)
