"""BioPython-based PDB structural feature extraction for post-run analysis.

Computes three structural features from a two-chain PDB complex:

- Buried Surface Area (BSA): SASA(chain_A) + SASA(chain_B) - SASA(complex)
  Positive BSA indicates interface surface area that becomes buried upon
  complex formation. Typical range for designed minibinders: 800–2000 A^2.

- Inter-chain clash count: atom pairs closer than sum of VdW radii minus a
  tolerance. Zero clashes expected for a well-minimized designed complex.

- Interface contact count: number of residues on chain_a that have at least
  one heavy atom within the cutoff distance of chain_b heavy atoms.

All public functions return None for individual metrics on failure (malformed
PDB file, missing chain) rather than raising. The calling agent tool handles
None values gracefully by omitting that metric from its response.

Security note: pdb_path is constructed from pdb_key via os.path.basename in
the calling tool — never accepts raw user input (T-08-04).
"""

import logging

from Bio.PDB import NeighborSearch, PDBParser
from Bio.PDB.Polypeptide import is_aa
from Bio.PDB.SASA import ShrakeRupley

logger = logging.getLogger(__name__)

# Van der Waals radii (Angstroms) for heavy atoms in standard amino acids.
# Source: CHARMM36 force field, rounded to 2 decimal places.
VDW_RADII: dict[str, float] = {
    "C": 1.70,
    "N": 1.55,
    "O": 1.52,
    "S": 1.80,
    "H": 1.20,
    "P": 1.80,
    "SE": 1.90,
    "FE": 1.50,
    "ZN": 1.39,
    "MG": 1.73,
}
_DEFAULT_VDW = 1.50  # Fallback radius for uncommon elements
_CLASH_TOLERANCE = 0.4  # Angstroms of overlap allowed before counting as clash


def compute_bsa(pdb_path: str, chain_a: str, chain_b: str) -> float | None:
    """Compute buried surface area (BSA) between two chains.

    BSA is the difference between the sum of individual chain solvent-accessible
    surface areas and the complex SASA:
        BSA = SASA(chain_A alone) + SASA(chain_B alone) - SASA(complex)

    To avoid mutating a shared structure object, this function re-parses the
    PDB file three times (complex, chain_a only, chain_b only).

    Args:
        pdb_path: Path to PDB file containing at least two chains.
        chain_a: First chain ID (e.g. "A" for target).
        chain_b: Second chain ID (e.g. "B" for binder).

    Returns:
        BSA in Angstrom^2, rounded to 1 decimal place. Returns 0.0 rather
        than a negative value if rounding/probe discretization produces a
        tiny negative result. Returns None if parsing fails or either chain
        is absent.
    """
    try:
        parser = PDBParser(QUIET=True)

        # --- SASA of the complex ----------------------------------------
        structure = parser.get_structure("complex", pdb_path)
        model = structure[0]

        chain_ids = [c.id for c in model]
        if chain_a not in chain_ids or chain_b not in chain_ids:
            logger.warning(
                "compute_bsa: chain %s or %s not found in %s (chains: %s)",
                chain_a, chain_b, pdb_path, chain_ids,
            )
            return None

        sr_complex = ShrakeRupley(probe_radius=1.4, n_points=100)
        sr_complex.compute(model, level="A")
        complex_sasa = sum(atom.sasa for atom in model.get_atoms())

        # --- SASA of chain A alone (re-parse, detach chain B and others) ---
        struct_a = parser.get_structure("chain_a_only", pdb_path)
        model_a = struct_a[0]
        for cid in [c.id for c in model_a if c.id != chain_a]:
            model_a.detach_child(cid)
        sr_a = ShrakeRupley(probe_radius=1.4, n_points=100)
        sr_a.compute(model_a, level="A")
        sasa_a = sum(atom.sasa for atom in model_a.get_atoms())

        # --- SASA of chain B alone (re-parse, detach chain A and others) ---
        struct_b = parser.get_structure("chain_b_only", pdb_path)
        model_b = struct_b[0]
        for cid in [c.id for c in model_b if c.id != chain_b]:
            model_b.detach_child(cid)
        sr_b = ShrakeRupley(probe_radius=1.4, n_points=100)
        sr_b.compute(model_b, level="A")
        sasa_b = sum(atom.sasa for atom in model_b.get_atoms())

        bsa = sasa_a + sasa_b - complex_sasa
        return round(max(0.0, bsa), 1)

    except Exception:
        logger.exception("compute_bsa failed for %s chains %s/%s", pdb_path, chain_a, chain_b)
        return None


def count_clashes(pdb_path: str, chain_a: str, chain_b: str) -> int | None:
    """Count inter-chain steric clashes between two chains.

    A clash is defined as two heavy atoms (one from each chain) whose
    center-to-center distance is less than the sum of their VdW radii
    minus a tolerance factor (_CLASH_TOLERANCE = 0.4 A).

    Only heavy atoms (element != "H") are considered. Hydrogen positions
    in PDB files are often imprecise and would inflate clash counts.

    Uses NeighborSearch for O(N log N) spatial queries rather than O(N^2)
    all-pairs distance calculation.

    Args:
        pdb_path: Path to PDB file.
        chain_a: First chain ID.
        chain_b: Second chain ID.

    Returns:
        Count of clashing heavy atom pairs (each pair counted once).
        Returns None if parsing fails or either chain is absent.
        Returns 0 if either chain has no heavy atoms.
    """
    try:
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("complex", pdb_path)
        model = structure[0]

        chain_ids = [c.id for c in model]
        if chain_a not in chain_ids or chain_b not in chain_ids:
            logger.warning(
                "count_clashes: chain %s or %s not found in %s (chains: %s)",
                chain_a, chain_b, pdb_path, chain_ids,
            )
            return None

        # Collect heavy atoms from each chain
        atoms_a = [atom for atom in model[chain_a].get_atoms() if atom.element != "H"]
        atoms_b = [atom for atom in model[chain_b].get_atoms() if atom.element != "H"]

        if not atoms_a or not atoms_b:
            return 0

        # Build spatial index from chain B atoms; query from chain A
        # Search radius = max possible clash distance (largest VdW pair sum)
        max_vdw = max(VDW_RADII.values())
        search_radius = max_vdw * 2  # Conservative upper bound for neighbor search

        ns = NeighborSearch(atoms_b)
        clash_pairs: set[tuple[int, int]] = set()

        for atom_a in atoms_a:
            radius_a = VDW_RADII.get(atom_a.element.upper() if atom_a.element else "", _DEFAULT_VDW)
            nearby = ns.search(atom_a.coord, search_radius)

            for atom_b in nearby:
                radius_b = VDW_RADII.get(atom_b.element.upper() if atom_b.element else "", _DEFAULT_VDW)
                clash_threshold = radius_a + radius_b - _CLASH_TOLERANCE
                dist = atom_a - atom_b  # BioPython __sub__ returns float distance

                if dist < clash_threshold:
                    # Deduplicate by sorting serial numbers to avoid double-counting
                    pair = (
                        min(atom_a.serial_number, atom_b.serial_number),
                        max(atom_a.serial_number, atom_b.serial_number),
                    )
                    clash_pairs.add(pair)

        return len(clash_pairs)

    except Exception:
        logger.exception("count_clashes failed for %s chains %s/%s", pdb_path, chain_a, chain_b)
        return None


def count_interface_contacts(
    pdb_path: str,
    chain_a: str,
    chain_b: str,
    cutoff: float = 5.0,
) -> int | None:
    """Count residues on chain_a within cutoff distance of chain_b.

    A residue on chain_a is counted as an interface contact if any of its
    heavy atoms is within `cutoff` Angstroms of any heavy atom on chain_b.

    This mirrors the logic in pdb_utils/interface.py and reuses the same
    NeighborSearch approach for consistency.

    Args:
        pdb_path: Path to PDB file.
        chain_a: Chain whose interface residues are counted.
        chain_b: Partner chain used as the search target.
        cutoff: Distance cutoff in Angstroms. Default 5.0 A.

    Returns:
        Number of interface residues on chain_a, or None if parsing fails
        or either chain is absent. Returns 0 if chain_b has no standard
        amino acid heavy atoms.
    """
    try:
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("complex", pdb_path)
        model = structure[0]

        chain_ids = [c.id for c in model]
        if chain_a not in chain_ids or chain_b not in chain_ids:
            logger.warning(
                "count_interface_contacts: chain %s or %s not found in %s (chains: %s)",
                chain_a, chain_b, pdb_path, chain_ids,
            )
            return None

        # Collect heavy atoms from chain_b standard amino acid residues
        atoms_b = [
            atom
            for residue in model[chain_b]
            if is_aa(residue, standard=True)
            for atom in residue
            if atom.element != "H"
        ]

        if not atoms_b:
            return 0

        ns = NeighborSearch(atoms_b)
        contact_residues: set[int] = set()

        for residue in model[chain_a]:
            if not is_aa(residue, standard=True):
                continue

            residue_seq_num = residue.get_id()[1]

            for atom in residue:
                if atom.element == "H":
                    continue
                if ns.search(atom.coord, cutoff):
                    contact_residues.add(residue_seq_num)
                    break  # One atom contact is sufficient to count the residue

        return len(contact_residues)

    except Exception:
        logger.exception(
            "count_interface_contacts failed for %s chains %s/%s",
            pdb_path, chain_a, chain_b,
        )
        return None


def extract_structural_features(
    pdb_path: str,
    chain_a: str,
    chain_b: str,
) -> dict:
    """Extract all structural features from a two-chain PDB complex.

    Convenience wrapper that runs all three structural analyses. Individual
    failures return None for that metric without blocking the others. The
    calling agent tool handles None values by omitting those metrics from
    its natural-language response.

    Args:
        pdb_path: Path to PDB file.
        chain_a: Target chain ID (e.g. "A").
        chain_b: Binder chain ID (e.g. "B").

    Returns:
        Dict with keys:
            bsa (float | None): Buried surface area in A^2.
            clash_count (int | None): Number of inter-chain clash pairs.
            interface_contacts (int | None): Residues on chain_a within 5A of chain_b.
    """
    return {
        "bsa": compute_bsa(pdb_path, chain_a, chain_b),
        "clash_count": count_clashes(pdb_path, chain_a, chain_b),
        "interface_contacts": count_interface_contacts(pdb_path, chain_a, chain_b),
    }
