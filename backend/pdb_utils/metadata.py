"""Extract structure metadata from a parsed PDB/mmCIF file.

Used by the upload endpoint to populate the StructurePreviewCard when
the user uploads a file directly (no RCSB metadata available).
"""

from Bio.PDB import PDBParser
from Bio.PDB.Polypeptide import is_aa


def extract_structure_metadata(pdb_path: str) -> dict:
    """Parse a normalized PDB file and extract chain/residue metadata.

    Args:
        pdb_path: Path to the normalized PDB file on disk.

    Returns:
        Dict with chain_count, chain_ids, selected_chain, residue_count,
        and per-chain residue counts.
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("target", pdb_path)

    chain_ids = []
    residue_counts = {}

    for model in structure:
        for chain in model:
            cid = chain.get_id()
            if cid.strip():
                chain_ids.append(cid)
                aa_count = sum(1 for r in chain if is_aa(r, standard=True))
                residue_counts[cid] = aa_count
        break  # Only process first model

    total_residues = sum(residue_counts.values())
    selected_chain = chain_ids[0] if chain_ids else "A"

    return {
        "chain_count": len(chain_ids),
        "chain_ids": chain_ids,
        "selected_chain": selected_chain,
        "residue_count": total_residues,
        "residue_counts_by_chain": residue_counts,
    }
