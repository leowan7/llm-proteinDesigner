"""Shared Pydantic models for the PDB pipeline.

These types are produced by the PDB fetch and normalization pipeline (Plan 02-02)
and consumed by the agent (Plan 02-03) and frontend (Plan 02-04).
"""

from pydantic import BaseModel


class StructureSummary(BaseModel):
    """Summary card data for a resolved structure (displayed in chat)."""

    pdb_id: str                          # e.g. "1N26"
    protein_name: str                    # e.g. "Interleukin-6 receptor subunit alpha"
    resolution: float | None             # Angstroms; None for NMR
    method: str                          # "X-ray", "NMR", "EM"
    chain_count: int
    selected_chain: str                  # e.g. "A"
    residue_count: int
    normalization_changes: list[str]     # e.g. ["Converted 3 MSE residues to MET"]


class NormalizationResult(BaseModel):
    """Output of the PDB normalization pipeline."""

    output_path: str                     # Path to normalized file
    changes: list[str]                   # Human-readable list of changes made


class HotspotCheck(BaseModel):
    """Result for a single hotspot residue accessibility check."""

    residue_number: int
    residue_name: str                    # 3-letter code, e.g. "ARG"
    sasa: float                          # Solvent-accessible surface area in Angstrom^2
    accessible: bool                     # True if sasa >= threshold
    warning: str | None                  # None if accessible; explanation if buried
