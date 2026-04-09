"""Standalone pipeline script for RunPod GPU Pods — RFantibody antibody design.

Reads job configuration from the JOB_PAYLOAD environment variable,
runs the 3-stage RFantibody pipeline, uploads results via presigned URLs,
POSTs results to the Kendrew webhook, then exits.

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, upload endpoint, and webhook config
    WEBHOOK_URL     URL to POST results to (Kendrew backend)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    RUNPOD_POD_ID   RunPod pod ID (so backend can terminate after completion)

Pipeline stages:
  1. RFantibody CDR loop generation — generates antibody backbones with designed CDR loops
  2. AbMPNN sequence design — assigns amino acid sequences to CDR loops
  3. RF2 antibody validation — predicts structure of designed antibody-antigen complex
"""

import csv
import datetime
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("rfantibody_pipeline")

# ---------------------------------------------------------------------------
# Paths inside the container (all weights baked into the Docker image)
# ---------------------------------------------------------------------------
RFANTIBODY_DIR = os.environ.get("RFANTIBODY_DIR", "/opt/rfantibody")
WEIGHTS_DIR = os.environ.get("WEIGHTS_DIR", "/opt/rfantibody/weights")

# RF2 confidence threshold for passing designs
RF2_CONFIDENCE_THRESHOLD = 0.70


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def startup_check():
    """Log environment and dependency status at startup.

    Crashes if CUDA is not available — running without GPU is not supported.
    Validates all required environment variables and weight files exist.

    Returns:
        Dict of diagnostic check results.

    Raises:
        SystemExit: If CUDA is unavailable or required env vars are missing.
    """
    checks = {}

    # --- Torch + CUDA ---
    try:
        import torch
        checks["torch"] = torch.__version__
        checks["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            checks["gpu"] = torch.cuda.get_device_name(0)
        else:
            logger.error("CUDA is NOT available. Cannot run RFantibody without GPU.")
            sys.exit(1)
    except Exception as exc:
        logger.error("PyTorch import failed: %s", exc)
        sys.exit(1)

    # --- Required env vars ---
    required_env = ["JOB_PAYLOAD", "WEBHOOK_URL", "JOB_ID"]
    for var in required_env:
        val = os.environ.get(var)
        if not val:
            logger.error("Required environment variable %s is not set", var)
            sys.exit(1)
        checks[f"env_{var}"] = "set"

    # --- Biopython ---
    try:
        from Bio.PDB import PDBParser
        checks["biopython"] = "ok"
    except Exception as exc:
        checks["biopython_error"] = str(exc)

    # --- RFantibody directory ---
    checks["rfantibody_dir"] = os.path.isdir(RFANTIBODY_DIR)

    # --- Weight files ---
    expected_weights = [
        "RF2_ab.pt",
    ]
    for weight_file in expected_weights:
        weight_path = os.path.join(WEIGHTS_DIR, weight_file)
        checks[f"weight_{weight_file}"] = os.path.exists(weight_path)

    # Check for any .pt files in weights dir
    if os.path.isdir(WEIGHTS_DIR):
        pt_files = glob(os.path.join(WEIGHTS_DIR, "*.pt"))
        checks["weight_files_found"] = len(pt_files)
        checks["weight_files"] = [os.path.basename(f) for f in pt_files]
    else:
        checks["weights_dir_exists"] = False

    logger.info("Startup diagnostics: %s", json.dumps(checks, indent=2))
    return checks


# ===========================================================================
# Helper functions
# ===========================================================================

def send_heartbeat(
    webhook_url: str,
    job_id: str,
    stage: str,
    designs_completed: int = 0,
    designs_total: int = 0,
) -> None:
    """Send a heartbeat to the Kendrew backend.

    Derives the heartbeat URL from the main webhook URL by replacing
    the /webhooks/runpod path with /webhooks/heartbeat.

    Args:
        webhook_url: The main RunPod webhook URL.
        job_id: Kendrew job UUID.
        stage: Current pipeline stage description.
        designs_completed: Number of designs finished so far.
        designs_total: Total designs requested.
    """
    heartbeat_url = webhook_url.replace("/webhooks/runpod", "/webhooks/heartbeat")
    body = {
        "job_id": job_id,
        "stage": stage,
        "designs_completed": designs_completed,
        "designs_total": designs_total,
    }
    try:
        resp = requests.post(heartbeat_url, json=body, timeout=10)
        logger.debug("Heartbeat sent: %s (HTTP %d)", stage, resp.status_code)
    except Exception as exc:
        logger.warning("Heartbeat failed: %s", exc)


def download_input(url: str, dest_path: str) -> None:
    """Download a file from a presigned GET URL.

    Args:
        url: Presigned S3/R2 GET URL.
        dest_path: Local path to write the downloaded file.

    Raises:
        RuntimeError: If download fails (non-200 response).
    """
    logger.info("Downloading input PDB -> %s", dest_path)
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download input PDB: HTTP {resp.status_code}")
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(resp.content)
    logger.info("Downloaded %d bytes", len(resp.content))


def request_upload_urls(
    upload_endpoint: str, job_token: str, filenames: list[str]
) -> dict[str, str]:
    """Request fresh presigned PUT URLs from the Kendrew backend.

    Args:
        upload_endpoint: URL of the /jobs/{job_id}/upload-urls endpoint.
        job_token: Job-specific Bearer token for authentication.
        filenames: List of filenames to upload.

    Returns:
        Dict mapping filename to presigned PUT URL.

    Raises:
        RuntimeError: If the request fails.
    """
    resp = requests.post(
        upload_endpoint,
        json={"filenames": filenames},
        headers={"Authorization": f"Bearer {job_token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to get upload URLs: HTTP {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()["urls"]


def upload_output(url: str, file_path: str) -> None:
    """Upload a file to R2/S3 via a presigned PUT URL.

    Args:
        url: Presigned PUT URL.
        file_path: Local file path to upload.

    Raises:
        RuntimeError: If upload fails.
    """
    data = Path(file_path).read_bytes()
    content_type = "text/csv" if file_path.endswith(".csv") else "chemical/x-pdb"
    resp = requests.put(
        url, data=data, headers={"Content-Type": content_type}, timeout=120
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Upload failed for {file_path}: HTTP {resp.status_code}")
    logger.info("Uploaded %s (%d bytes)", file_path, len(data))


def run_command(
    cmd: list[str], timeout: int = 3600, cwd: str | None = None
) -> str:
    """Run a subprocess command with timeout and logging.

    Always logs the last 2000 chars of combined stdout+stderr, even on
    success, to aid debugging in production.

    Args:
        cmd: Command and arguments as a list.
        timeout: Maximum seconds before killing the process.
        cwd: Working directory for the subprocess.

    Returns:
        Combined stdout + stderr output.

    Raises:
        RuntimeError: If the command exits with non-zero status.
    """
    logger.info("Running: %s", " ".join(cmd[:8]) + ("..." if len(cmd) > 8 else ""))
    start = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )
    elapsed = time.time() - start
    combined_output = (result.stdout or "") + (result.stderr or "")

    # Log last 2000 chars even on success for debugging
    logger.info(
        "Command finished in %.1fs (exit code %d). Output tail:\n%s",
        elapsed,
        result.returncode,
        combined_output[-2000:],
    )

    if result.returncode != 0:
        error_tail = combined_output[-2000:]
        raise RuntimeError(f"Command failed (exit {result.returncode}): {error_tail}")
    return combined_output


def post_webhook(webhook_url: str, job_id: str, pod_id: str, payload: dict) -> None:
    """POST results to the Kendrew backend webhook.

    Args:
        webhook_url: Backend webhook endpoint URL.
        job_id: Kendrew job UUID.
        pod_id: RunPod pod ID (for backend to terminate).
        payload: Results dict (candidates, counts, etc.).
    """
    body = {
        "id": job_id,
        "pod_id": pod_id,
        "status": "COMPLETED" if "error" not in payload else "FAILED",
        "output": payload,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if "error" in payload:
        body["error"] = {"category": "Pipeline error", "message": payload["error"]}

    logger.info("Posting webhook to %s", webhook_url)
    try:
        resp = requests.post(webhook_url, json=body, timeout=30)
        logger.info("Webhook response: %d", resp.status_code)
    except Exception as exc:
        logger.error("Webhook POST failed: %s", exc)


# ===========================================================================
# Pipeline stage functions
# ===========================================================================

def stage_rfantibody_design(
    target_pdb: str,
    config: dict,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
) -> list[str]:
    """Stage 1: Run RFantibody CDR loop generation.

    Generates antibody backbones with designed CDR loops targeting the
    specified epitope residues on the antigen.

    Args:
        target_pdb: Path to the input target PDB file.
        config: Pipeline config dict with epitope_residues, cdr_design,
                framework, num_designs.
        output_dir: Directory to write generated backbone PDBs.
        webhook_url: Heartbeat webhook URL (optional).
        job_id: Kendrew job ID for heartbeats (optional).

    Returns:
        List of paths to generated backbone PDB files.

    Raises:
        RuntimeError: If RFantibody produces no output.
    """
    logger.info("=== Stage 1: RFantibody CDR loop generation ===")
    os.makedirs(output_dir, exist_ok=True)

    num_designs = config.get("num_designs", 10)
    epitope_residues = config.get("epitope_residues", [])
    cdr_design = config.get("cdr_design", ["H1", "H2", "H3"])
    framework = config.get("framework", "VHH")

    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running RFantibody design", 0, num_designs)

    # Build the RFantibody design command.
    # RFantibody uses Hydra-style config overrides similar to RFdiffusion.
    cmd = [
        "python", "-m", "rfantibody.design",
        f"input.target_pdb={target_pdb}",
        f"input.epitope_residues=[{','.join(epitope_residues)}]",
        f"input.cdr_design=[{','.join(cdr_design)}]",
        f"input.framework={framework}",
        f"output.num_designs={num_designs}",
        f"output.prefix={output_dir}/design",
    ]

    run_command(cmd, timeout=1800, cwd=RFANTIBODY_DIR)

    generated = sorted(glob(os.path.join(output_dir, "design_*.pdb")))
    logger.info("RFantibody generated %d backbone PDBs", len(generated))
    if not generated:
        raise RuntimeError("RFantibody produced no output PDB files")

    if webhook_url and job_id:
        send_heartbeat(
            webhook_url, job_id, "RFantibody design complete",
            len(generated), num_designs,
        )
    return generated


def stage_abmpnn(
    backbone_pdbs: list[str],
    config: dict,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
) -> list[str]:
    """Stage 2: Run AbMPNN sequence design on generated backbones.

    AbMPNN assigns amino acid sequences to the CDR loops designed by
    RFantibody, keeping the framework and antigen regions fixed.

    Args:
        backbone_pdbs: List of backbone PDB paths from stage 1.
        config: Pipeline config dict.
        output_dir: Directory for AbMPNN output files.
        webhook_url: Heartbeat webhook URL (optional).
        job_id: Kendrew job ID for heartbeats (optional).

    Returns:
        List of paths to designed PDB/FASTA output files.

    Raises:
        RuntimeError: If AbMPNN produces no output.
    """
    logger.info("=== Stage 2: AbMPNN sequence design ===")
    os.makedirs(output_dir, exist_ok=True)

    if webhook_url and job_id:
        send_heartbeat(
            webhook_url, job_id, "Running AbMPNN sequence design",
            0, len(backbone_pdbs),
        )

    designed_files = []
    for idx, backbone_pdb in enumerate(backbone_pdbs):
        design_name = Path(backbone_pdb).stem
        per_design_out = os.path.join(output_dir, design_name)
        os.makedirs(per_design_out, exist_ok=True)

        cmd = [
            "python", "-m", "rfantibody.abmpnn",
            f"input.pdb={backbone_pdb}",
            f"output.prefix={per_design_out}/{design_name}",
            f"output.num_seqs=2",
        ]

        try:
            run_command(cmd, timeout=600, cwd=RFANTIBODY_DIR)

            # Collect output PDB or FASTA files
            output_files = sorted(
                glob(os.path.join(per_design_out, "*.pdb"))
                + glob(os.path.join(per_design_out, "*.fa"))
                + glob(os.path.join(per_design_out, "*.fasta"))
            )
            if output_files:
                designed_files.extend(output_files)
                logger.info(
                    "AbMPNN design %d/%d (%s): %d output files",
                    idx + 1, len(backbone_pdbs), design_name, len(output_files),
                )
            else:
                logger.warning("AbMPNN produced no output for %s", design_name)

        except RuntimeError as exc:
            logger.warning("AbMPNN failed for %s: %s", design_name, exc)
            continue

        if webhook_url and job_id:
            send_heartbeat(
                webhook_url, job_id, "Running AbMPNN sequence design",
                idx + 1, len(backbone_pdbs),
            )

    if not designed_files:
        raise RuntimeError("AbMPNN produced no output files for any design")

    logger.info("AbMPNN produced %d designed files total", len(designed_files))
    return designed_files


def stage_rf2_validation(
    designed_files: list[str],
    target_pdb: str,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
) -> list[dict]:
    """Stage 3: RF2 antibody structure validation.

    Predicts the structure of each designed antibody-antigen complex using
    RF2 (RoseTTAFold2) antibody model and extracts confidence scores.

    Args:
        designed_files: List of designed PDB/FASTA paths from stage 2.
        target_pdb: Path to the original target antigen PDB.
        output_dir: Directory for RF2 prediction output.
        webhook_url: Heartbeat webhook URL (optional).
        job_id: Kendrew job ID for heartbeats (optional).

    Returns:
        List of dicts with design_name, scores, pdb_path, and sequence keys.
    """
    logger.info("=== Stage 3: RF2 antibody validation ===")
    os.makedirs(output_dir, exist_ok=True)

    if webhook_url and job_id:
        send_heartbeat(
            webhook_url, job_id, "Running RF2 validation",
            0, len(designed_files),
        )

    results = []
    for idx, designed_file in enumerate(designed_files):
        design_name = Path(designed_file).stem
        per_design_out = os.path.join(output_dir, design_name)
        os.makedirs(per_design_out, exist_ok=True)

        cmd = [
            "python", "-m", "rfantibody.rf2_predict",
            f"input.pdb={designed_file}",
            f"input.target_pdb={target_pdb}",
            f"output.prefix={per_design_out}/{design_name}",
        ]

        try:
            output_text = run_command(cmd, timeout=600, cwd=RFANTIBODY_DIR)

            # Parse RF2 confidence scores from output
            scores = _parse_rf2_scores(per_design_out, design_name, output_text)
            if scores:
                # Find the predicted PDB
                predicted_pdbs = glob(
                    os.path.join(per_design_out, f"{design_name}*.pdb")
                )
                pdb_path = predicted_pdbs[0] if predicted_pdbs else designed_file

                # Extract sequence from designed file if it is a PDB
                sequence = _extract_sequence_from_pdb(designed_file)

                results.append({
                    "design_name": design_name,
                    "scores": scores,
                    "pdb_path": pdb_path,
                    "designed_pdb": designed_file,
                    "sequence": sequence or "",
                })
                logger.info(
                    "RF2 scores for %s: rf2_confidence=%.3f",
                    design_name, scores.get("rf2_confidence", 0.0),
                )

        except RuntimeError as exc:
            logger.warning("RF2 validation failed for %s: %s", design_name, exc)
            continue

        if webhook_url and job_id:
            send_heartbeat(
                webhook_url, job_id, "Running RF2 validation",
                idx + 1, len(designed_files),
            )

    logger.info(
        "RF2 validated %d / %d designs", len(results), len(designed_files)
    )
    return results


def _parse_rf2_scores(
    result_dir: str, design_name: str, output_text: str
) -> dict | None:
    """Extract RF2 confidence scores from validation output.

    Checks for a JSON scores file first, then falls back to parsing
    stdout for confidence metrics.

    Args:
        result_dir: Directory containing RF2 output files.
        design_name: Name of the design being scored.
        output_text: Combined stdout+stderr from the RF2 command.

    Returns:
        Dict with rf2_confidence and cdr_geometry scores, or None on failure.
    """
    # Try JSON score file first
    score_files = glob(os.path.join(result_dir, f"{design_name}*scores*.json"))
    if not score_files:
        score_files = glob(os.path.join(result_dir, "*scores*.json"))

    if score_files:
        try:
            with open(score_files[0]) as fh:
                data = json.load(fh)
            return {
                "rf2_confidence": float(data.get("confidence", data.get("plddt", 0.0))),
                "cdr_rmsd": float(data.get("cdr_rmsd", 0.0)),
                "cdr_geometry": data.get("cdr_geometry", {}),
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "Failed to parse RF2 JSON scores for %s: %s", design_name, exc
            )

    # Fallback: parse confidence from stdout
    # RF2 typically prints lines like "confidence: 0.85" or "pLDDT: 82.3"
    confidence = _extract_float_from_output(output_text, "confidence")
    plddt = _extract_float_from_output(output_text, "plddt")

    if confidence is not None or plddt is not None:
        # Normalize pLDDT (0-100) to 0-1 scale for rf2_confidence if needed
        rf2_conf = confidence if confidence is not None else (plddt / 100.0 if plddt else 0.0)
        return {
            "rf2_confidence": round(rf2_conf, 4),
            "cdr_rmsd": 0.0,
            "cdr_geometry": {},
        }

    logger.warning("No RF2 scores found for %s", design_name)
    return None


def _extract_float_from_output(text: str, keyword: str) -> float | None:
    """Extract a float value following a keyword in command output.

    Searches for lines containing the keyword (case-insensitive) and
    extracts the first float-like value after it.

    Args:
        text: Command output text to search.
        keyword: Keyword to look for (e.g., 'confidence', 'plddt').

    Returns:
        Extracted float value, or None if not found.
    """
    import re
    pattern = rf"(?i){keyword}\s*[:=]\s*([\d.]+)"
    match = re.search(pattern, text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _extract_sequence_from_pdb(pdb_path: str) -> str | None:
    """Extract the antibody heavy chain sequence from a PDB file.

    Reads chain H (heavy chain) residues from the PDB. Falls back to
    chain A if H is not found.

    Args:
        pdb_path: Path to the PDB file.

    Returns:
        Amino acid sequence string, or None on failure.
    """
    if not pdb_path.endswith(".pdb"):
        return None
    try:
        from Bio.PDB import PDBParser
        from Bio.PDB.Polypeptide import protein_letters_3to1

        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("design", pdb_path)

        # Try heavy chain first, then fall back to first chain
        for target_chain_id in ["H", "A"]:
            for model in structure:
                for chain in model:
                    if chain.id == target_chain_id:
                        residues = []
                        for residue in chain:
                            resname = residue.get_resname().strip()
                            if resname in protein_letters_3to1:
                                residues.append(protein_letters_3to1[resname])
                        if residues:
                            return "".join(residues)

        # Last resort: first chain with residues
        for model in structure:
            for chain in model:
                residues = []
                for residue in chain:
                    resname = residue.get_resname().strip()
                    if resname in protein_letters_3to1:
                        residues.append(protein_letters_3to1[resname])
                if residues:
                    return "".join(residues)

        return None
    except Exception as exc:
        logger.warning("Failed to extract sequence from %s: %s", pdb_path, exc)
        return None


def write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a metrics CSV summarizing all passing candidates.

    Args:
        csv_path: Path to write the CSV file.
        candidates: List of candidate dicts with rank, pdb_key, scores, sequence.
    """
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "design_name", "rf2_confidence", "cdr_rmsd", "sequence",
        ])
        for candidate in candidates:
            design_name = Path(candidate["pdb_key"]).stem
            scores = candidate["scores"]
            writer.writerow([
                candidate["rank"],
                design_name,
                scores.get("rf2_confidence", ""),
                scores.get("cdr_rmsd", ""),
                candidate.get("sequence", ""),
            ])


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full pipeline: download -> RFantibody -> AbMPNN -> RF2 -> upload -> webhook."""
    startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))
    job_token = os.environ.get("JOB_TOKEN", "")

    # JOB_PAYLOAD already validated in startup_check
    job_payload = json.loads(job_payload_str)
    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    # Build pipeline config from job_spec
    params = job_spec.get("parameters", {})
    target_chain = job_spec.get("target_chain", "A")
    hotspots = job_spec.get("hotspot_residues", [])

    config = {
        "epitope_residues": [f"{target_chain}{res}" for res in hotspots],
        "cdr_design": params.get("cdr_design", ["H1", "H2", "H3"]),
        "framework": params.get("framework", "VHH"),
        "num_designs": params.get("num_designs", 10),
    }

    pipeline_start = time.time()
    work_dir = tempfile.mkdtemp(prefix="rfantibody_job_")
    target_pdb = os.path.join(work_dir, "target.pdb")

    try:
        # ----- Download input PDB -----
        download_input(input_url, target_pdb)

        # ----- Stage 1: RFantibody CDR generation -----
        design_output = os.path.join(work_dir, "rfantibody_output")
        os.makedirs(design_output, exist_ok=True)

        try:
            backbone_pdbs = stage_rfantibody_design(
                target_pdb, config, design_output,
                webhook_url=webhook_url, job_id=job_id,
            )
        except RuntimeError as exc:
            logger.error("RFantibody design failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"RFantibody design failed: {exc}",
            })
            return

        # ----- Stage 2: AbMPNN sequence design -----
        abmpnn_output = os.path.join(work_dir, "abmpnn_output")
        try:
            designed_files = stage_abmpnn(
                backbone_pdbs, config, abmpnn_output,
                webhook_url=webhook_url, job_id=job_id,
            )
        except RuntimeError as exc:
            logger.error("AbMPNN failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"AbMPNN failed: {exc}",
                "partial": True,
                "backbone_count": len(backbone_pdbs),
            })
            return

        # ----- Stage 3: RF2 validation -----
        rf2_output = os.path.join(work_dir, "rf2_output")
        try:
            rf2_results = stage_rf2_validation(
                designed_files, target_pdb, rf2_output,
                webhook_url=webhook_url, job_id=job_id,
            )
        except RuntimeError as exc:
            logger.error("RF2 validation failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"RF2 validation failed: {exc}",
                "partial": True,
                "backbone_count": len(backbone_pdbs),
                "designed_count": len(designed_files),
            })
            return

        # ----- Filter and rank by rf2_confidence -----
        passing = [
            r for r in rf2_results
            if r["scores"].get("rf2_confidence", 0.0) >= RF2_CONFIDENCE_THRESHOLD
        ]
        passing.sort(
            key=lambda x: x["scores"].get("rf2_confidence", 0.0), reverse=True
        )

        logger.info(
            "Filtering: %d / %d pass (rf2_confidence >= %.2f)",
            len(passing), len(rf2_results), RF2_CONFIDENCE_THRESHOLD,
        )

        # ----- Upload outputs (on-demand URLs) -----
        candidates = []
        filenames_to_upload = []
        for rank_idx in range(len(passing)):
            filenames_to_upload.append(f"design_{rank_idx + 1:03d}.pdb")
        if filenames_to_upload:
            filenames_to_upload.append("metrics.csv")

        upload_urls = {}
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(
                    upload_endpoint, job_token, filenames_to_upload
                )
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)

        for rank_idx, result in enumerate(passing):
            rank = rank_idx + 1
            design_name = result["design_name"]
            pdb_path = result.get("pdb_path", result.get("designed_pdb", ""))

            pdb_key = f"designs/{design_name}.pdb"
            candidate = {
                "rank": rank,
                "pdb_key": pdb_key,
                "scores": result["scores"],
                "sequence": result.get("sequence", ""),
            }
            candidates.append(candidate)

            upload_filename = f"design_{rank:03d}.pdb"
            if upload_filename in upload_urls and pdb_path and os.path.exists(pdb_path):
                try:
                    upload_output(upload_urls[upload_filename], pdb_path)
                except RuntimeError as exc:
                    logger.warning("Failed to upload PDB for rank %d: %s", rank, exc)

        # ----- Upload metrics CSV -----
        if candidates:
            csv_path = os.path.join(work_dir, "metrics.csv")
            write_metrics_csv(csv_path, candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], csv_path)
                except RuntimeError as exc:
                    logger.warning("Failed to upload metrics CSV: %s", exc)

        elapsed_minutes = (time.time() - pipeline_start) / 60.0
        logger.info(
            "Pipeline complete: %d candidates in %.1f minutes",
            len(candidates), elapsed_minutes,
        )

        # ----- POST results to webhook -----
        result_payload = {
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
            "rf2_validated": len(rf2_results),
            "runtime_minutes": round(elapsed_minutes, 1),
            "next_steps": (
                "Recommend experimental validation: SPR or BLI binding assay "
                "for top antibody candidates, followed by thermal stability "
                "assessment (nanoDSF). Consider yeast display for affinity "
                "maturation of the best CDR loop designs."
            ),
        }
        post_webhook(webhook_url, job_id, pod_id, result_payload)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
