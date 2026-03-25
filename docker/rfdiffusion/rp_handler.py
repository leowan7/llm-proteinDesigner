"""RunPod serverless handler for the RFdiffusion 3-stage pipeline.

Pipeline stages:
  1. RFdiffusion  — generate poly-Gly backbone PDBs
  2. ProteinMPNN  — assign sequences to designed backbones (fix target chain)
  3. AF2 multimer — validate binder-target complex, extract ipTM/pLDDT/i_pAE

Input payload (job["input"]):
    job_spec              dict   Full JobSpec from Kendrew backend
    input_presigned_url   str    GET URL for target PDB in R2
    output_presigned_urls list   PUT URLs for uploading designed PDBs
    report_presigned_url  str    PUT URL for metrics CSV

Output:
    candidates      list[dict]  Ranked passing designs with scores
    candidate_count int         Number of passing candidates
    next_steps      str         Recommended experimental follow-up
"""

import csv
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from glob import glob
from pathlib import Path

import requests
import runpod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("rfdiffusion_handler")

# ---------------------------------------------------------------------------
# Paths inside the container (set during Docker build)
# ---------------------------------------------------------------------------
RFDIFFUSION_SCRIPT = "/opt/rfdiffusion/scripts/run_inference.py"
RFDIFFUSION_MODELS = "/opt/rfdiffusion/models"
PROTEINMPNN_SCRIPT = "/opt/ProteinMPNN/protein_mpnn_run.py"
PROTEINMPNN_WEIGHTS = "/opt/ProteinMPNN/vanilla_model_weights"

# ---------------------------------------------------------------------------
# Filtering thresholds (mirrors backend/agent/reference/02_technical_setup_guide.md)
# ---------------------------------------------------------------------------
IPTM_THRESHOLD = 0.70
PLDDT_THRESHOLD = 80.0
IPAE_THRESHOLD = 10.0


# ===========================================================================
# Helper functions
# ===========================================================================


def download_input(url: str, dest_path: str) -> None:
    """Download a file from a presigned GET URL.

    Args:
        url: Presigned GET URL (R2/S3).
        dest_path: Local path to write the downloaded file.

    Raises:
        RuntimeError: If the download fails.
    """
    logger.info("Downloading input PDB from presigned URL -> %s", dest_path)
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to download input PDB: HTTP {resp.status_code}"
        )
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(resp.content)
    logger.info("Downloaded %d bytes", len(resp.content))


def upload_output(url: str, file_path: str) -> None:
    """Upload a file to R2/S3 via a presigned PUT URL.

    Args:
        url: Presigned PUT URL.
        file_path: Local file to upload.

    Raises:
        RuntimeError: If the upload fails with a non-2xx status.
    """
    data = Path(file_path).read_bytes()
    content_type = (
        "text/csv" if file_path.endswith(".csv") else "chemical/x-pdb"
    )
    resp = requests.put(
        url,
        data=data,
        headers={"Content-Type": content_type},
        timeout=120,
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"Upload failed for {file_path}: HTTP {resp.status_code}"
        )
    logger.info("Uploaded %s (%d bytes)", file_path, len(data))


def run_command(cmd: list[str], timeout: int = 3600, cwd: str | None = None) -> str:
    """Run a subprocess command with timeout and logging.

    Args:
        cmd: Command and arguments as a list of strings.
        timeout: Maximum seconds before killing the process.
        cwd: Working directory for the subprocess.

    Returns:
        Combined stdout + stderr output.

    Raises:
        RuntimeError: If the command exits with a non-zero return code.
    """
    logger.info("Running: %s", " ".join(cmd[:6]) + ("..." if len(cmd) > 6 else ""))
    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    elapsed = time.time() - start
    logger.info("Command finished in %.1fs (exit code %d)", elapsed, result.returncode)

    if result.returncode != 0:
        error_tail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {error_tail}"
        )
    return result.stdout + result.stderr


def parse_af2_scores(result_dir: str, design_name: str) -> dict | None:
    """Extract ipTM, pLDDT, and i_pAE from ColabFold AF2 prediction output.

    ColabFold writes a *_scores_rank_001*.json file per prediction containing
    the relevant metrics.

    Args:
        result_dir: Directory containing ColabFold output files.
        design_name: Base name of the design (used to locate score files).

    Returns:
        Dict with ipTM, pLDDT, i_pAE floats, or None if parsing fails.
    """
    score_files = glob(os.path.join(result_dir, f"{design_name}*scores*.json"))
    if not score_files:
        # Fall back to any scores file in the directory.
        score_files = glob(os.path.join(result_dir, "*scores*.json"))
    if not score_files:
        logger.warning("No AF2 score files found for %s in %s", design_name, result_dir)
        return None

    try:
        with open(score_files[0]) as fh:
            data = json.load(fh)

        # ColabFold score file structure: plddt (list), ptm (float),
        # iptm (float), and pae (2D list).
        iptm = float(data.get("iptm", 0.0))
        plddt_values = data.get("plddt", [])
        mean_plddt = (
            sum(plddt_values) / len(plddt_values) if plddt_values else 0.0
        )

        # i_pAE: mean of the interface PAE block. The PAE matrix has shape
        # [total_residues, total_residues]. We approximate interface PAE as
        # the mean of the off-diagonal blocks (target-binder interactions).
        pae_matrix = data.get("pae", [])
        ipae = _compute_interface_pae(pae_matrix, data) if pae_matrix else 99.0

        return {
            "ipTM": round(iptm, 4),
            "pLDDT": round(mean_plddt, 2),
            "i_pAE": round(ipae, 2),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse AF2 scores for %s: %s", design_name, exc)
        return None


def _compute_interface_pae(pae_matrix: list, score_data: dict) -> float:
    """Compute mean interface PAE from the PAE matrix.

    Interface PAE is the mean of PAE values between residues belonging to
    different chains (target vs binder).

    Args:
        pae_matrix: 2D PAE matrix from ColabFold scores JSON.
        score_data: Full score data dict (may contain chain length info).

    Returns:
        Mean interface PAE in Angstroms.
    """
    total_res = len(pae_matrix)
    if total_res == 0:
        return 99.0

    # Estimate chain boundary. ColabFold multimer predictions concatenate
    # chains in order. We split at the midpoint as a reasonable heuristic
    # when exact chain lengths are unavailable. For more accurate splitting,
    # the handler passes the target chain length via the sequence file.
    chain_lengths = score_data.get("chain_lengths", None)
    if chain_lengths and len(chain_lengths) >= 2:
        boundary = chain_lengths[0]
    else:
        boundary = total_res // 2

    ipae_values = []
    for row_idx in range(total_res):
        for col_idx in range(total_res):
            # Cross-chain interactions only.
            row_is_target = row_idx < boundary
            col_is_target = col_idx < boundary
            if row_is_target != col_is_target:
                ipae_values.append(pae_matrix[row_idx][col_idx])

    return sum(ipae_values) / len(ipae_values) if ipae_values else 99.0


# ===========================================================================
# Pipeline stage functions
# ===========================================================================


def build_hydra_args(job_spec: dict, target_pdb_path: str) -> list[str]:
    """Build RFdiffusion Hydra CLI override args from JobSpec parameters.

    Mirrors the logic in backend/pipelines/rfdiffusion.py
    RFdiffusionPipeline.generate_config() so the handler produces identical
    configurations to what the backend would generate.

    Args:
        job_spec: Deserialized JobSpec dict.
        target_pdb_path: Path to target PDB inside the container.

    Returns:
        List of Hydra override strings.
    """
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    hotspots = job_spec.get("hotspot_residues", [])

    binder_length = params.get("binder_length", {"min": 50, "max": 100})
    if isinstance(binder_length, dict):
        binder_min = binder_length.get("min", 50)
        binder_max = binder_length.get("max", 100)
    else:
        binder_min = 50
        binder_max = 100

    num_designs = params.get("num_designs", 10)

    # Contig string: [ChainResRange/0 BinderLenRange]
    # The /0 gap means "break chain here" — target on left, binder on right.
    contig_str = f"[{chain}1-150/0 {binder_min}-{binder_max}]"

    checkpoint = params.get("checkpoint", "Complex_base_ckpt.pt")
    ckpt_path = os.path.join(RFDIFFUSION_MODELS, checkpoint)

    hydra_args = [
        f"inference.input_pdb={target_pdb_path}",
        f"contigmap.contigs={contig_str}",
        f"inference.num_designs={num_designs}",
        f"inference.ckpt_override_path={ckpt_path}",
    ]

    if hotspots:
        hotspot_str = "[" + ",".join(f"{chain}{res}" for res in hotspots) + "]"
        hydra_args.append(f"ppi.hotspot_res={hotspot_str}")

    return hydra_args


def stage_rfdiffusion(
    target_pdb: str, job_spec: dict, output_dir: str
) -> list[str]:
    """Stage 1: Run RFdiffusion backbone generation.

    Args:
        target_pdb: Path to the target PDB file.
        job_spec: Full JobSpec dict.
        output_dir: Directory for RFdiffusion output PDBs.

    Returns:
        List of paths to generated backbone PDB files.

    Raises:
        RuntimeError: If RFdiffusion fails.
    """
    logger.info("=== Stage 1: RFdiffusion backbone generation ===")
    hydra_args = build_hydra_args(job_spec, target_pdb)

    cmd = [
        "python", RFDIFFUSION_SCRIPT,
        f"inference.output_prefix={output_dir}/design",
        *hydra_args,
    ]

    run_command(cmd, timeout=1800)

    generated = sorted(glob(os.path.join(output_dir, "design_*.pdb")))
    logger.info("RFdiffusion generated %d backbone PDBs", len(generated))

    if not generated:
        raise RuntimeError("RFdiffusion produced no output PDB files")

    return generated


def stage_proteinmpnn(
    backbone_pdbs: list[str], target_chain: str, output_dir: str
) -> list[str]:
    """Stage 2: Run ProteinMPNN sequence design on each backbone.

    Fixes target chain residues and designs the binder chain sequence.

    Args:
        backbone_pdbs: List of paths to poly-Gly backbone PDBs.
        target_chain: Chain ID of the target (residues are fixed).
        output_dir: Directory for ProteinMPNN output.

    Returns:
        List of paths to sequence-designed PDB/FASTA files.

    Raises:
        RuntimeError: If ProteinMPNN fails on all inputs.
    """
    logger.info("=== Stage 2: ProteinMPNN sequence design ===")
    os.makedirs(output_dir, exist_ok=True)

    designed_fastas = []

    for pdb_path in backbone_pdbs:
        design_name = Path(pdb_path).stem
        per_design_out = os.path.join(output_dir, design_name)
        os.makedirs(per_design_out, exist_ok=True)

        # Create a chains-to-design file: fix the target chain, design everything else.
        chains_to_design_path = os.path.join(per_design_out, "chains_to_design.txt")
        # ProteinMPNN --design_only_positions approach: we specify which chains to design.
        # The binder chain is typically B (RFdiffusion outputs target=A, binder=B).
        binder_chain = "B" if target_chain == "A" else "A"

        cmd = [
            "python", PROTEINMPNN_SCRIPT,
            "--pdb_path", pdb_path,
            "--out_folder", per_design_out,
            "--num_seq_per_target", "1",
            "--sampling_temp", "0.1",
            "--model_name", "v_48_020",
            "--batch_size", "1",
            "--chain_id_jsonl", "",
            "--fixed_positions_jsonl", "",
            "--design_chains", binder_chain,
        ]

        try:
            run_command(cmd, timeout=300, cwd="/opt/ProteinMPNN")
            # ProteinMPNN outputs FASTA files in the seqs/ subdirectory.
            fasta_files = glob(os.path.join(per_design_out, "seqs", "*.fa"))
            if fasta_files:
                designed_fastas.extend(fasta_files)
                logger.info("ProteinMPNN designed sequence for %s", design_name)
            else:
                logger.warning("No FASTA output for %s", design_name)
        except RuntimeError as exc:
            logger.warning("ProteinMPNN failed for %s: %s", design_name, exc)
            continue

    if not designed_fastas:
        raise RuntimeError("ProteinMPNN failed on all backbone inputs")

    logger.info("ProteinMPNN produced %d designed sequences", len(designed_fastas))
    return designed_fastas


def _extract_sequences_from_fasta(fasta_path: str) -> dict[str, str]:
    """Parse a FASTA file and return {header: sequence} dict.

    Args:
        fasta_path: Path to the FASTA file.

    Returns:
        Dictionary mapping sequence headers to amino acid sequences.
    """
    sequences = {}
    current_header = None
    current_seq = []

    with open(fasta_path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(">"):
                if current_header is not None:
                    sequences[current_header] = "".join(current_seq)
                current_header = line[1:]
                current_seq = []
            else:
                current_seq.append(line)

    if current_header is not None:
        sequences[current_header] = "".join(current_seq)

    return sequences


def stage_af2_validation(
    designed_fastas: list[str],
    target_pdb: str,
    target_chain: str,
    output_dir: str,
) -> list[dict]:
    """Stage 3: AF2 multimer validation of designed binder-target complexes.

    Uses ColabFold to predict the binder-target complex and extract
    confidence metrics (ipTM, pLDDT, i_pAE).

    Args:
        designed_fastas: List of FASTA files from ProteinMPNN.
        target_pdb: Path to the target PDB (for extracting target sequence).
        target_chain: Chain ID of the target.
        output_dir: Directory for AF2 output.

    Returns:
        List of dicts with design_name, scores, and sequence for each design.
    """
    logger.info("=== Stage 3: AF2 multimer validation ===")
    os.makedirs(output_dir, exist_ok=True)

    # Extract target sequence from PDB using Biopython.
    target_sequence = _extract_target_sequence(target_pdb, target_chain)
    if not target_sequence:
        raise RuntimeError(
            f"Could not extract target sequence from {target_pdb} chain {target_chain}"
        )

    results = []

    for fasta_path in designed_fastas:
        design_name = Path(fasta_path).stem
        sequences = _extract_sequences_from_fasta(fasta_path)

        if not sequences:
            logger.warning("No sequences in %s, skipping", fasta_path)
            continue

        # Take the first designed sequence (ProteinMPNN outputs 1 seq per target).
        binder_sequence = list(sequences.values())[0]

        # Write combined FASTA for ColabFold multimer prediction.
        # Format: target:binder separated by colon in the sequence.
        combined_fasta = os.path.join(output_dir, f"{design_name}.fasta")
        with open(combined_fasta, "w") as fh:
            fh.write(f">{design_name}\n")
            fh.write(f"{target_sequence}:{binder_sequence}\n")

        per_design_out = os.path.join(output_dir, design_name)
        os.makedirs(per_design_out, exist_ok=True)

        try:
            cmd = [
                "colabfold_batch",
                combined_fasta,
                per_design_out,
                "--model-type", "alphafold2_multimer_v3",
                "--num-recycle", "3",
                "--num-models", "1",
                "--rank", "iptm",
            ]
            run_command(cmd, timeout=600)

            scores = parse_af2_scores(per_design_out, design_name)
            if scores:
                results.append({
                    "design_name": design_name,
                    "scores": scores,
                    "sequence": binder_sequence,
                    "fasta_path": fasta_path,
                })
                logger.info(
                    "AF2 scores for %s: ipTM=%.3f pLDDT=%.1f i_pAE=%.1f",
                    design_name,
                    scores["ipTM"],
                    scores["pLDDT"],
                    scores["i_pAE"],
                )
        except RuntimeError as exc:
            logger.warning("AF2 validation failed for %s: %s", design_name, exc)
            continue

    logger.info("AF2 validated %d / %d designs", len(results), len(designed_fastas))
    return results


def _extract_target_sequence(pdb_path: str, chain_id: str) -> str | None:
    """Extract the amino acid sequence for a specific chain from a PDB file.

    Uses Biopython PDBParser to read the structure and extract one-letter
    amino acid codes for the specified chain.

    Args:
        pdb_path: Path to the PDB file.
        chain_id: Chain identifier (e.g., "A").

    Returns:
        One-letter amino acid sequence string, or None if extraction fails.
    """
    try:
        from Bio.PDB import PDBParser
        from Bio.PDB.Polypeptide import protein_letters_3to1

        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("target", pdb_path)

        for model in structure:
            for chain in model:
                if chain.id == chain_id:
                    residues = []
                    for residue in chain:
                        resname = residue.get_resname().strip()
                        if resname in protein_letters_3to1:
                            residues.append(protein_letters_3to1[resname])
                    if residues:
                        return "".join(residues)

        logger.warning("Chain %s not found in %s", chain_id, pdb_path)
        return None
    except Exception as exc:
        logger.warning("Failed to extract target sequence: %s", exc)
        return None


# ===========================================================================
# Main handler
# ===========================================================================


def handler(job: dict) -> dict:
    """RunPod serverless handler for the RFdiffusion 3-stage pipeline.

    Receives a job payload with JobSpec, presigned URLs for input/output,
    and runs the complete pipeline: RFdiffusion -> ProteinMPNN -> AF2.

    Args:
        job: RunPod job dict with "input" key containing the payload.

    Returns:
        Dict with candidates list, candidate_count, and next_steps.
    """
    job_input = job["input"]
    job_spec = job_input["job_spec"]
    input_url = job_input["input_presigned_url"]
    output_urls = job_input.get("output_presigned_urls", [])
    report_url = job_input.get("report_presigned_url")

    target_chain = job_spec.get("target_chain", "A")
    pipeline_start = time.time()

    # Create a clean working directory for this job.
    work_dir = tempfile.mkdtemp(prefix="rfdiffusion_job_")
    target_pdb = os.path.join(work_dir, "target.pdb")

    try:
        # ----- Download input PDB -----
        download_input(input_url, target_pdb)

        # ----- Stage 1: RFdiffusion -----
        rfdiff_output = os.path.join(work_dir, "rfdiffusion_output")
        os.makedirs(rfdiff_output, exist_ok=True)

        try:
            backbone_pdbs = stage_rfdiffusion(target_pdb, job_spec, rfdiff_output)
        except RuntimeError as exc:
            logger.error("RFdiffusion failed: %s", exc)
            return {"error": f"RFdiffusion failed: {exc}"}

        # ----- Stage 2: ProteinMPNN -----
        mpnn_output = os.path.join(work_dir, "mpnn_output")

        try:
            designed_fastas = stage_proteinmpnn(
                backbone_pdbs, target_chain, mpnn_output
            )
        except RuntimeError as exc:
            logger.error("ProteinMPNN failed: %s", exc)
            return {
                "error": f"ProteinMPNN failed: {exc}",
                "partial": True,
                "backbone_count": len(backbone_pdbs),
            }

        # ----- Stage 3: AF2 validation -----
        af2_output = os.path.join(work_dir, "af2_output")

        try:
            af2_results = stage_af2_validation(
                designed_fastas, target_pdb, target_chain, af2_output
            )
        except RuntimeError as exc:
            logger.error("AF2 validation failed: %s", exc)
            return {
                "error": f"AF2 validation failed: {exc}",
                "partial": True,
                "backbone_count": len(backbone_pdbs),
                "designed_count": len(designed_fastas),
            }

        # ----- Filter and rank -----
        passing = [
            result for result in af2_results
            if (
                result["scores"]["ipTM"] >= IPTM_THRESHOLD
                and result["scores"]["pLDDT"] >= PLDDT_THRESHOLD
                and result["scores"]["i_pAE"] <= IPAE_THRESHOLD
            )
        ]
        passing.sort(key=lambda x: x["scores"]["ipTM"], reverse=True)

        logger.info(
            "Filtering: %d / %d designs pass thresholds "
            "(ipTM>=%.2f, pLDDT>=%.0f, i_pAE<=%.0f)",
            len(passing),
            len(af2_results),
            IPTM_THRESHOLD,
            PLDDT_THRESHOLD,
            IPAE_THRESHOLD,
        )

        # ----- Upload outputs -----
        candidates = []
        for rank_idx, result in enumerate(passing):
            rank = rank_idx + 1
            design_name = result["design_name"]

            # Find the designed PDB (backbone from RFdiffusion).
            backbone_pdb = os.path.join(rfdiff_output, f"{design_name}.pdb")
            if not os.path.exists(backbone_pdb):
                # Try matching by design index.
                backbone_pdb = os.path.join(
                    rfdiff_output, f"design_{design_name.split('_')[-1]}.pdb"
                )

            pdb_key = f"designs/{design_name}.pdb"
            candidate = {
                "rank": rank,
                "pdb_key": pdb_key,
                "scores": result["scores"],
                "sequence": result["sequence"],
            }
            candidates.append(candidate)

            # Upload PDB if we have a presigned URL for this rank.
            if rank_idx < len(output_urls) and os.path.exists(backbone_pdb):
                try:
                    upload_output(output_urls[rank_idx], backbone_pdb)
                except RuntimeError as exc:
                    logger.warning("Failed to upload PDB for rank %d: %s", rank, exc)

        # ----- Upload metrics CSV -----
        if report_url and candidates:
            csv_path = os.path.join(work_dir, "metrics.csv")
            _write_metrics_csv(csv_path, candidates)
            try:
                upload_output(report_url, csv_path)
            except RuntimeError as exc:
                logger.warning("Failed to upload metrics CSV: %s", exc)

        elapsed_minutes = (time.time() - pipeline_start) / 60.0
        logger.info(
            "Pipeline complete: %d candidates in %.1f minutes",
            len(candidates),
            elapsed_minutes,
        )

        return {
            "candidates": [
                {
                    "rank": c["rank"],
                    "pdb_key": c["pdb_key"],
                    "scores": c["scores"],
                }
                for c in candidates
            ],
            "candidate_count": len(candidates),
            "total_designs": len(backbone_pdbs),
            "af2_validated": len(af2_results),
            "runtime_minutes": round(elapsed_minutes, 1),
            "next_steps": (
                "Recommend experimental validation: SPR or BLI binding assay "
                "for top candidates, followed by counter-screen for specificity. "
                "Consider yeast display library construction for affinity maturation "
                "of the best hits."
            ),
        }

    finally:
        # Clean up temporary files.
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a metrics CSV summarizing all passing candidates.

    Columns: rank, design_name, ipTM, pLDDT, i_pAE, sequence

    Args:
        csv_path: Output path for the CSV file.
        candidates: List of candidate dicts with rank, pdb_key, scores, sequence.
    """
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "design_name", "ipTM", "pLDDT", "i_pAE", "sequence"])
        for candidate in candidates:
            design_name = Path(candidate["pdb_key"]).stem
            scores = candidate["scores"]
            writer.writerow([
                candidate["rank"],
                design_name,
                scores.get("ipTM", ""),
                scores.get("pLDDT", ""),
                scores.get("i_pAE", ""),
                candidate.get("sequence", ""),
            ])


# ---------------------------------------------------------------------------
# RunPod serverless entry point
# ---------------------------------------------------------------------------
runpod.serverless.start({"handler": handler})
