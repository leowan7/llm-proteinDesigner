"""Extract interface residues from co-crystal structures.

Given a PDB with multiple chains, identifies residues on a target chain
that are within a distance cutoff of a partner chain. These are candidate
hotspot residues for binder design.

Uses Biopython's NeighborSearch for efficient spatial queries.
"""

from dataclasses import dataclass

from Bio.PDB import PDBParser, NeighborSearch
from Bio.PDB.Polypeptide import is_aa


@dataclass
class InterfaceResidue:
    """A single interface residue on the target chain."""

    chain_id: str
    residue_number: int
    residue_name: str
    min_distance: float  # Angstroms to nearest partner atom


def extract_interface_residues(
    pdb_path: str,
    target_chain: str,
    partner_chain: str,
    distance_cutoff: float = 5.0,
) -> list[InterfaceResidue]:
    """Find residues on target_chain within distance_cutoff of partner_chain.

    Args:
        pdb_path: Path to the PDB file.
        target_chain: Chain ID of the target (e.g., "A").
        partner_chain: Chain ID of the binding partner (e.g., "B").
        distance_cutoff: Distance threshold in Angstroms. Residues on the
            target chain with any heavy atom within this distance of any
            heavy atom on the partner chain are returned. Default 5.0 A.

    Returns:
        List of InterfaceResidue objects sorted by residue number.

    Raises:
        ValueError: If either chain is not found in the PDB.
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("complex", pdb_path)
    model = structure[0]

    if target_chain not in [c.id for c in model]:
        raise ValueError(f"Chain {target_chain} not found in {pdb_path}")
    if partner_chain not in [c.id for c in model]:
        raise ValueError(f"Chain {partner_chain} not found in {pdb_path}")

    # Collect all heavy atoms from the partner chain
    partner_atoms = [
        atom
        for residue in model[partner_chain]
        if is_aa(residue, standard=True)
        for atom in residue
        if atom.element != "H"
    ]

    if not partner_atoms:
        return []

    # Build spatial index from partner atoms
    ns = NeighborSearch(partner_atoms)

    # Find target residues with atoms near partner
    interface_residues: dict[int, InterfaceResidue] = {}

    for residue in model[target_chain]:
        if not is_aa(residue, standard=True):
            continue

        res_num = residue.get_id()[1]

        for atom in residue:
            if atom.element == "H":
                continue

            nearby = ns.search(atom.get_vector().get_array(), distance_cutoff)
            if nearby:
                # Calculate minimum distance to partner
                min_dist = min(
                    atom - partner_atom for partner_atom in nearby
                )
                if res_num not in interface_residues or min_dist < interface_residues[res_num].min_distance:
                    interface_residues[res_num] = InterfaceResidue(
                        chain_id=target_chain,
                        residue_number=res_num,
                        residue_name=residue.get_resname(),
                        min_distance=round(min_dist, 2),
                    )

    return sorted(interface_residues.values(), key=lambda r: r.residue_number)
