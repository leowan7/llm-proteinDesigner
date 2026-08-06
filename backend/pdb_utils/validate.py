"""Pre-flight validation checks for PDB structures.

Two public functions:
  - check_hotspot_accessibility: SASA-based surface accessibility check per residue.
  - run_preflight_checks: battery of quality checks that returns a list of
    ValidationResult objects with pass/warn/fail status.

Both are synchronous (CPU-bound BioPython work). The async wrapper
run_preflight_checks_async is provided for use inside FastAPI endpoints.
"""
import asyncio

from agent.jobspec import ValidationResult
from Bio.PDB import PDBParser
from Bio.PDB.Polypeptide import is_aa
from Bio.PDB.SASA import ShrakeRupley

from pdb_utils.models import HotspotCheck, StructureSummary


def _clean_structure_for_sasa(structure):
    """Remove non-standard residues, water, and heteroatoms before SASA computation.

    Biopython's ShrakeRupley can choke on non-standard residues (metal ions,
    ligands, modified residues like europium). Stripping them first avoids
    parser errors while preserving the protein backbone geometry.
    """
    residues_to_remove = []
    for model in structure:
        for chain in model:
            for residue in chain:
                hetflag = residue.get_id()[0]
                # Keep standard amino acids (hetflag=' ') only
                # Remove water (W/HOH), heteroatoms (H_xxx), and non-standard residues
                if hetflag != " " or not is_aa(residue, standard=True):
                    residues_to_remove.append((chain.get_id(), residue.get_id()))

    for chain_id, res_id in residues_to_remove:
        try:
            structure[0][chain_id].detach_child(res_id)
        except Exception:
            pass

    return structure


def check_hotspot_accessibility(
    pdb_path: str,
    chain_id: str,
    residue_numbers: list[int],
    sasa_threshold: float = 1.0,
) -> list[HotspotCheck]:
    """Check whether hotspot residues are surface-accessible via SASA.

    Computes per-residue solvent-accessible surface area (SASA) using the
    Shrake-Rupley rolling probe algorithm. Non-standard residues, water, and
    heteroatoms are stripped before computation to avoid parser errors.

    Args:
        pdb_path: Path to a PDB file.
        chain_id: Single-letter chain identifier containing the hotspot residues.
        residue_numbers: List of residue sequence numbers to check.
        sasa_threshold: Minimum per-residue SASA (Angstrom^2) to be considered
                        surface-accessible. Default 1.0 Angstrom^2.

    Returns:
        List of HotspotCheck results, one per residue number in the input list.
        Residues not found in the chain are reported with accessible=False and
        a 'not found' warning.
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("target", pdb_path)

    # Strip non-protein atoms before SASA computation
    structure = _clean_structure_for_sasa(structure)

    sr = ShrakeRupley()
    sr.compute(structure, level="R")

    if chain_id not in [c.id for c in structure[0]]:
        return [
            HotspotCheck(
                residue_number=r,
                residue_name="UNKNOWN",
                sasa=0.0,
                accessible=False,
                warning=f"Chain {chain_id} not found in structure",
            )
            for r in residue_numbers
        ]

    chain = structure[0][chain_id]

    # Build residue lookup: resseq (int) -> residue object
    residue_map: dict[int, object] = {}
    for residue in chain:
        if is_aa(residue, standard=True):
            resseq = residue.get_id()[1]
            if resseq not in residue_map:
                residue_map[resseq] = residue

    results = []
    for resnum in residue_numbers:
        residue = residue_map.get(resnum)
        if residue is None:
            results.append(
                HotspotCheck(
                    residue_number=resnum,
                    residue_name="UNKNOWN",
                    sasa=0.0,
                    accessible=False,
                    warning=f"Residue {resnum} not found in chain {chain_id}",
                )
            )
            continue

        sasa = getattr(residue, "sasa", 0.0)
        accessible = sasa >= sasa_threshold
        results.append(
            HotspotCheck(
                residue_number=resnum,
                residue_name=residue.get_resname().strip(),
                sasa=round(sasa, 2),
                accessible=accessible,
                warning=(
                    None
                    if accessible
                    else (
                        f"Residue {resnum} ({residue.get_resname().strip()}) appears buried "
                        f"(SASA={sasa:.1f} \u00c5\u00b2); may not be a productive binder "
                        f"contact point"
                    )
                ),
            )
        )
    return results


def scan_chain_gaps(pdb_path: str, chain_id: str) -> list[tuple[int, int]]:
    """Find numbering gaps in a chain's standard-AA residues.

    Detects disorder regions (residues missing from the deposited model)
    that would break RFdiffusion's strict contig builder, which asserts
    every residue in the contig range exists. Used by validate_preflight
    to surface a fail/warn BEFORE the user launches a job that would
    crash inside the container.

    Args:
        pdb_path: Path to a PDB-format file.
        chain_id: Chain to scan. May name SEVERAL chains, comma- or
            whitespace-separated ("A,B" / "A B"); every named chain is
            scanned and the gaps are unioned.

    Returns:
        List of (gap_start, gap_end) tuples (inclusive endpoints of the
        MISSING residue range). Empty list if the chain has no gaps.
        Example: chain numbered 19, 20, ..., 51, 61, 62, ... returns
        ``[(52, 60)]``. Chains with monotonic +1 numbering return ``[]``.
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("target", pdb_path)
    present = {c.id: c for c in structure[0]}

    # A multi-chain selector used to be compared whole against a single
    # chain id: "A,B" matched nothing, returned [], and was reported as
    # "contiguous numbering" — a silent pass that dropped this guard from
    # precisely the oligomeric targets most likely to have disordered loops.
    chain_ids = (chain_id or "").replace(",", " ").split()

    gaps: list[tuple[int, int]] = []
    for cid in chain_ids:
        chain = present.get(cid)
        if chain is None:
            # Absent chains are reported by the chain-presence check; a
            # missing chain is not a gap.
            continue
        resnums = sorted(
            residue.get_id()[1]
            for residue in chain
            if is_aa(residue, standard=True)
        )
        for prev, curr in zip(resnums, resnums[1:]):
            if curr > prev + 1:
                gaps.append((prev + 1, curr - 1))
    return gaps


def check_hotspots_present(
    pdb_path: str,
    chain_id: str,
    hotspot_residues: list[int],
) -> list[int]:
    """Return the subset of hotspots that are NOT present in the chain.

    A hotspot residue can be "absent" because (a) the chain has a disorder
    region covering that number, or (b) the residue is non-standard and
    excluded from the standard-AA filter. Either way, downstream tools
    that reference the hotspot by number (RFdiffusion's ppi.hotspot_res,
    RFantibody's epitope strings) will fail at runtime.

    Args:
        pdb_path: Path to a PDB-format file.
        chain_id: Chain to look in.
        hotspot_residues: Residue numbers the user wants to target.

    Returns:
        The hotspot residue numbers that are NOT present in the chain's
        standard-AA residues. Empty list if every hotspot is present.
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("target", pdb_path)
    if chain_id not in [c.id for c in structure[0]]:
        return list(hotspot_residues)
    chain = structure[0][chain_id]
    present = {
        residue.get_id()[1]
        for residue in chain
        if is_aa(residue, standard=True)
    }
    return [r for r in hotspot_residues if r not in present]


def run_preflight_checks(
    summary: StructureSummary, pdb_path: str
) -> list[ValidationResult]:
    """Run all pre-flight validation checks on a resolved structure.

    Checks performed:
      1. Resolution: warn if > 3.0 Angstrom; pass otherwise; warn if NMR (no resolution).
      2. Method: warn if NMR; pass otherwise.
      3. Standard residues: fail if no standard amino acids are present.

    Args:
        summary: StructureSummary produced by the resolution/normalization step.
        pdb_path: Path to the normalized PDB file for structural checks.

    Returns:
        List of ValidationResult objects. Any 'fail' status should block job dispatch.
    """
    results: list[ValidationResult] = []

    # Check 1: Resolution
    if summary.resolution is not None:
        if summary.resolution > 3.0:
            results.append(
                ValidationResult(
                    check_name="resolution",
                    status="warn",
                    message=(
                        f"Resolution {summary.resolution:.1f} \u00c5 is above 3.0 \u00c5. "
                        f"Structure quality may affect design outcome."
                    ),
                )
            )
        else:
            results.append(
                ValidationResult(
                    check_name="resolution",
                    status="pass",
                    message=f"Resolution {summary.resolution:.1f} \u00c5 is acceptable.",
                )
            )
    else:
        results.append(
            ValidationResult(
                check_name="resolution",
                status="warn",
                message=(
                    "NMR ensemble detected — using model 1. X-ray structures generally "
                    "give better results for binder design."
                ),
            )
        )

    # Check 2: Experimental method
    if summary.method == "NMR":
        results.append(
            ValidationResult(
                check_name="method",
                status="warn",
                message=(
                    "NMR ensemble detected. X-ray structures generally give better "
                    "results for binder design."
                ),
            )
        )
    else:
        results.append(
            ValidationResult(
                check_name="method",
                status="pass",
                message=f"Experimental method: {summary.method}.",
            )
        )

    # Check 3: Standard amino acids present in structure file
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("check", pdb_path)
    has_aa = any(is_aa(r, standard=True) for r in structure.get_residues())
    if not has_aa:
        results.append(
            ValidationResult(
                check_name="standard_residues",
                status="fail",
                message=(
                    "Structure contains no standard amino acid residues. "
                    "Cannot proceed with design."
                ),
            )
        )
    else:
        results.append(
            ValidationResult(
                check_name="standard_residues",
                status="pass",
                message="Standard amino acid residues present.",
            )
        )

    return results


async def run_preflight_checks_async(
    summary: StructureSummary, pdb_path: str
) -> list[ValidationResult]:
    """Async wrapper for run_preflight_checks.

    Runs the CPU-bound SASA and PDB parsing work in a thread pool executor
    to avoid blocking the FastAPI event loop.

    Args:
        summary: StructureSummary from the resolution step.
        pdb_path: Path to normalized PDB file.

    Returns:
        List of ValidationResult objects.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_preflight_checks, summary, pdb_path)
