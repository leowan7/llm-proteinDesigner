"""Standalone pipeline script for RFantibody on RunPod GPU Pods.

Reads job configuration from the JOB_PAYLOAD environment variable,
runs the 3-stage RFantibody Quiver pipeline, uploads results via
presigned URLs, POSTs results to the Kendrew webhook, then exits.

RFantibody uses Quiver (.qv) files as the primary I/O format across
all three stages. PDB files are extracted at the end for upload.

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, upload endpoint, and webhook config
    WEBHOOK_URL     URL to POST results to (Kendrew backend)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    RUNPOD_POD_ID   RunPod pod ID (so backend can terminate after completion)

Pipeline stages:
  1. rfdiffusion  — generate antibody backbones with designed CDR loops
  2. proteinmpnn  — assign amino acid sequences to CDR loops
  3. rf2          — predict structure of designed antibody-antigen complex
  4. qvscorefile  — extract confidence scores to TSV
  5. qvextract    — extract passing PDB files for upload
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
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("rfantibody_pipeline")

# ---------------------------------------------------------------------------
# Paths inside the container
# ---------------------------------------------------------------------------
RFANTIBODY_DIR = os.environ.get("RFANTIBODY_DIR", "/opt/rfantibody")

# Bundled framework PDBs (HLT-marked, from RFantibody repo examples)
FRAMEWORKS = {
    "VHH": os.path.join(RFANTIBODY_DIR, "scripts/examples/example_inputs/h-NbBCII10.pdb"),
    "scFv": os.path.join(RFANTIBODY_DIR, "scripts/examples/example_inputs/hu-4D5-8_Fv.pdb"),
}

# Filtering thresholds
PAE_THRESHOLD = 10.0
PLDDT_THRESHOLD = 80.0
IPTM_THRESHOLD = 0.70


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def startup_check() -> dict:
    """Log environment and dependency status at startup.

    Crashes if CUDA is not available or required CLI tools are missing.
    """
    checks = {}

    # Validate required environment variables
    required_vars = ["JOB_PAYLOAD", "WEBHOOK_URL", "JOB_ID"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    # Check PyTorch and CUDA
    try:
        import torch
        checks["torch"] = torch.__version__
        checks["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            checks["gpu"] = torch.cuda.get_device_name(0)
            checks["cuda_version"] = torch.version.cuda
        else:
            logger.error("CUDA is not available — RFantibody requires GPU")
            sys.exit(1)
    except ImportError:
        logger.error("PyTorch is not installed.")
        sys.exit(1)

    # Check CLI tools
    for tool in ["rfdiffusion", "proteinmpnn", "rf2", "qvextract", "qvscorefile"]:
        try:
            subprocess.run(
                [tool, "--help"], capture_output=True, text=True, timeout=10,
            )
            checks[f"{tool}_cli"] = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            checks[f"{tool}_cli"] = False
            logger.error("%s CLI not found in PATH", tool)
            sys.exit(1)

    # Check framework PDBs
    for name, path in FRAMEWORKS.items():
        checks[f"framework_{name}"] = os.path.exists(path)

    # Check weights
    weights_dir = os.path.join(RFANTIBODY_DIR, "weights")
    checks["weights_dir"] = os.path.isdir(weights_dir)

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
    """Send a heartbeat to the Kendrew backend."""
    parsed = urlparse(webhook_url)
    heartbeat_url = urlunparse(parsed._replace(path="/webhooks/heartbeat"))
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
    """Download a file from a presigned GET URL."""
    logger.info("Downloading input -> %s", dest_path)
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download input: HTTP {resp.status_code}")
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(resp.content)
    logger.info("Downloaded %d bytes", len(resp.content))


def request_upload_urls(
    upload_endpoint: str, job_token: str, filenames: list[str],
) -> dict[str, str]:
    """Request fresh presigned PUT URLs from the Kendrew backend."""
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
    """Upload a file to R2/S3 via a presigned PUT URL."""
    data = Path(file_path).read_bytes()
    if file_path.endswith(".csv") or file_path.endswith(".tsv"):
        content_type = "text/csv"
    else:
        content_type = "chemical/x-pdb"
    resp = requests.put(
        url, data=data, headers={"Content-Type": content_type}, timeout=120,
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Upload failed for {file_path}: HTTP {resp.status_code}")
    logger.info("Uploaded %s (%d bytes)", file_path, len(data))


def run_command(
    cmd: list[str], timeout: int = 3600, cwd: str | None = None,
) -> str:
    """Run a subprocess command with timeout and logging."""
    logger.info("Running: %s", " ".join(cmd[:10]) + ("..." if len(cmd) > 10 else ""))
    start = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
    )
    elapsed = time.time() - start
    combined_output = (result.stdout or "") + (result.stderr or "")

    output_tail = combined_output[-2000:]
    logger.info(
        "Command finished in %.1fs (exit code %d). Output tail:\n%s",
        elapsed, result.returncode, output_tail,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {output_tail}"
        )
    return combined_output


def post_webhook(
    webhook_url: str, job_id: str, pod_id: str, payload: dict,
) -> None:
    """POST results to the Kendrew backend webhook."""
    body = {
        "id": job_id,
        "pod_id": pod_id,
        "status": "COMPLETED" if "error" not in payload else "FAILED",
        "output": payload,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if "error" in payload:
        body["error"] = {
            "category": "Pipeline error",
            "message": payload["error"],
        }

    logger.info("Posting webhook to %s", webhook_url)
    try:
        resp = requests.post(webhook_url, json=body, timeout=30)
        logger.info("Webhook response: %d", resp.status_code)
    except Exception as exc:
        logger.error("Webhook POST failed: %s", exc)


def _safe_float(value: str, default: float) -> float:
    """Parse a float from a string, returning default on failure."""
    if not value or not value.strip():
        return default
    try:
        parsed = float(value)
        if parsed != parsed:  # NaN check
            return default
        return parsed
    except (ValueError, TypeError):
        return default


# ===========================================================================
# Pipeline stages (Quiver-based)
# ===========================================================================

def stage_rfdiffusion(
    target_pdb: str,
    framework_pdb: str,
    backbones_qv: str,
    num_designs: int,
    cdr_lengths: str,
    hotspots: str,
    webhook_url: str = "",
    job_id: str = "",
) -> None:
    """Stage 1: Generate antibody backbones with RFdiffusion.

    Uses the antibody-finetuned RFdiffusion model to generate CDR loop
    backbones on the provided framework scaffold.

    Args:
        target_pdb: Path to target antigen PDB.
        framework_pdb: Path to HLT-marked antibody framework PDB.
        backbones_qv: Output path for backbones Quiver file.
        num_designs: Number of backbone designs to generate.
        cdr_lengths: CDR loop length spec, e.g. "H1:8,H2:7,H3:10-16".
        hotspots: Comma-separated epitope residues, e.g. "A50,A51,A80".
        webhook_url: Heartbeat webhook URL.
        job_id: Kendrew job ID for heartbeats.
    """
    logger.info("=== Stage 1: RFdiffusion backbone generation ===")

    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running RFdiffusion", 0, num_designs)

    cmd = [
        "rfdiffusion",
        "-t", target_pdb,
        "-f", framework_pdb,
        "-q", backbones_qv,
        "-n", str(num_designs),
        "-l", cdr_lengths,
    ]
    if hotspots:
        cmd.extend(["-h", hotspots])

    run_command(cmd, timeout=1800, cwd=RFANTIBODY_DIR)

    if not os.path.exists(backbones_qv):
        raise RuntimeError(f"RFdiffusion did not produce {backbones_qv}")

    logger.info("RFdiffusion complete: %s", backbones_qv)


def stage_proteinmpnn(
    backbones_qv: str,
    sequences_qv: str,
    seqs_per_backbone: int = 5,
    temperature: float = 0.2,
    webhook_url: str = "",
    job_id: str = "",
    num_designs: int = 0,
) -> None:
    """Stage 2: Design CDR loop sequences with ProteinMPNN.

    Assigns amino acid sequences to the CDR loops designed by RFdiffusion,
    keeping the framework and antigen regions fixed.

    Args:
        backbones_qv: Path to backbones Quiver file from stage 1.
        sequences_qv: Output path for sequences Quiver file.
        seqs_per_backbone: Number of sequences per backbone (default 5).
        temperature: Sampling temperature (0.2 = conservative).
        webhook_url: Heartbeat webhook URL.
        job_id: Kendrew job ID for heartbeats.
        num_designs: Total design count for heartbeat display.
    """
    logger.info("=== Stage 2: ProteinMPNN sequence design ===")

    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running ProteinMPNN", 0, num_designs)

    cmd = [
        "proteinmpnn",
        "-q", backbones_qv,
        "--output-quiver", sequences_qv,
        "-n", str(seqs_per_backbone),
        "-t", str(temperature),
    ]

    run_command(cmd, timeout=1800, cwd=RFANTIBODY_DIR)

    if not os.path.exists(sequences_qv):
        raise RuntimeError(f"ProteinMPNN did not produce {sequences_qv}")

    logger.info("ProteinMPNN complete: %s", sequences_qv)


def stage_rf2(
    sequences_qv: str,
    predictions_qv: str,
    recycles: int = 10,
    webhook_url: str = "",
    job_id: str = "",
    num_designs: int = 0,
) -> None:
    """Stage 3: Predict structures with RoseTTAFold2-Antibody.

    Predicts the structure of each designed antibody-antigen complex
    and generates confidence scores (pAE, pLDDT, ipTM).

    Args:
        sequences_qv: Path to sequences Quiver file from stage 2.
        predictions_qv: Output path for predictions Quiver file.
        recycles: Number of RF2 refinement cycles (default 10).
        webhook_url: Heartbeat webhook URL.
        job_id: Kendrew job ID for heartbeats.
        num_designs: Total design count for heartbeat display.
    """
    logger.info("=== Stage 3: RF2 structure prediction ===")

    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running RF2 validation", 0, num_designs)

    cmd = [
        "rf2",
        "-q", sequences_qv,
        "--output-quiver", predictions_qv,
        "-r", str(recycles),
    ]

    run_command(cmd, timeout=3600, cwd=RFANTIBODY_DIR)

    if not os.path.exists(predictions_qv):
        raise RuntimeError(f"RF2 did not produce {predictions_qv}")

    logger.info("RF2 complete: %s", predictions_qv)


# ===========================================================================
# Score extraction and parsing
# ===========================================================================

def extract_scores(predictions_qv: str, scores_tsv: str) -> None:
    """Extract confidence scores from predictions Quiver to TSV.

    Uses qvscorefile to extract pAE, pLDDT, ipTM, pTM for all designs.
    """
    logger.info("Extracting scores from %s", predictions_qv)
    output = run_command(
        ["qvscorefile", predictions_qv],
        timeout=120,
        cwd=RFANTIBODY_DIR,
    )
    with open(scores_tsv, "w") as fh:
        fh.write(output)
    logger.info("Scores written to %s", scores_tsv)


def extract_pdbs(predictions_qv: str, out_dir: str) -> list[str]:
    """Extract all PDB files from predictions Quiver.

    Uses qvextract to write individual PDB files for upload.

    Returns:
        List of extracted PDB file paths.
    """
    logger.info("Extracting PDBs from %s to %s", predictions_qv, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    run_command(
        ["qvextract", predictions_qv, "--out-dir", out_dir],
        timeout=120,
        cwd=RFANTIBODY_DIR,
    )
    pdbs = sorted(str(p) for p in Path(out_dir).glob("*.pdb"))
    logger.info("Extracted %d PDB files", len(pdbs))
    return pdbs


def parse_scores_tsv(tsv_path: str) -> list[dict]:
    """Parse the scores TSV produced by qvscorefile.

    Expected columns include: design_name, pAE, pLDDT, ipTM, pTM.
    Column names may vary; we search case-insensitively.

    Returns:
        List of dicts with design_name and scores.
    """
    results = []
    with open(tsv_path, newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        columns = reader.fieldnames or []
        logger.info("Scores TSV columns: %s", columns)

        for row in reader:
            row_lower = {k.lower().strip(): v for k, v in row.items()}

            design_name = (
                row_lower.get("design_name")
                or row_lower.get("name")
                or row_lower.get("design")
                or f"design_{len(results)}"
            )
            design_name = Path(design_name).stem

            scores = {}
            for metric, keys, default in [
                ("pAE", ["pae", "ipae", "i_pae", "mean_pae"], 99.0),
                ("pLDDT", ["plddt", "mean_plddt", "avg_plddt"], 0.0),
                ("ipTM", ["iptm", "ip_tm", "iptm_score"], 0.0),
                ("pTM", ["ptm", "p_tm"], 0.0),
            ]:
                for key in keys:
                    if key in row_lower and row_lower[key]:
                        scores[metric] = _safe_float(row_lower[key], default)
                        break

            results.append({
                "design_name": design_name,
                "scores": scores,
            })

    logger.info("Parsed %d designs from scores TSV", len(results))
    return results


def filter_and_rank(designs: list[dict]) -> list[dict]:
    """Filter designs by quality thresholds and rank by ipTM."""
    passing = []
    for design in designs:
        scores = design["scores"]
        pae = scores.get("pAE", 99.0)
        plddt = scores.get("pLDDT", 0.0)
        iptm = scores.get("ipTM", 0.0)

        if pae <= PAE_THRESHOLD and plddt >= PLDDT_THRESHOLD and iptm >= IPTM_THRESHOLD:
            passing.append(design)

    passing.sort(key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True)

    logger.info(
        "Filtering: %d / %d pass (pAE<=%.1f, pLDDT>=%.0f, ipTM>=%.2f)",
        len(passing), len(designs),
        PAE_THRESHOLD, PLDDT_THRESHOLD, IPTM_THRESHOLD,
    )
    return passing


def write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a normalized metrics CSV for upload to Kendrew."""
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "design_name", "ipTM", "pLDDT", "pAE", "pTM",
        ])
        for candidate in candidates:
            scores = candidate["scores"]
            writer.writerow([
                candidate["rank"],
                candidate["design_name"],
                scores.get("ipTM", ""),
                scores.get("pLDDT", ""),
                scores.get("pAE", ""),
                scores.get("pTM", ""),
            ])


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full RFantibody pipeline."""
    startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))
    job_token = os.environ.get("JOB_TOKEN", "")

    if not job_payload_str:
        logger.error("JOB_PAYLOAD environment variable not set")
        sys.exit(1)

    job_payload = json.loads(job_payload_str)
    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    params = job_spec.get("parameters", {})
    num_designs = params.get("num_designs", 100)
    framework = params.get("framework", "VHH")
    cdr_lengths = params.get("cdr_lengths", "H1:8,H2:7,H3:10-16")
    hotspots_str = params.get("hotspots", "")
    mpnn_seqs = params.get("mpnn_seqs_per_backbone", 5)
    mpnn_temp = params.get("mpnn_temperature", 0.2)
    rf2_recycles = params.get("rf2_recycles", 10)

    # Resolve framework PDB
    framework_pdb = FRAMEWORKS.get(framework)
    if not framework_pdb or not os.path.exists(framework_pdb):
        logger.error("Framework '%s' not found. Available: %s", framework, list(FRAMEWORKS.keys()))
        post_webhook(webhook_url, job_id, pod_id, {
            "error": f"Unknown framework '{framework}'. Available: {list(FRAMEWORKS.keys())}",
        })
        sys.exit(1)

    # Build hotspots string from job_spec
    if not hotspots_str:
        chain = job_spec.get("target_chain", "A")
        hotspot_residues = job_spec.get("hotspot_residues", [])
        if hotspot_residues:
            hotspots_str = ",".join(f"{chain}{res}" for res in hotspot_residues)

    pipeline_start = time.time()
    work_dir = tempfile.mkdtemp(prefix="rfantibody_job_")
    target_pdb = os.path.join(work_dir, "target.pdb")

    # Quiver file paths
    backbones_qv = os.path.join(work_dir, "backbones.qv")
    sequences_qv = os.path.join(work_dir, "sequences.qv")
    predictions_qv = os.path.join(work_dir, "predictions.qv")
    scores_tsv = os.path.join(work_dir, "scores.tsv")
    top_hits_dir = os.path.join(work_dir, "top_hits")

    try:
        # ----- Download target PDB -----
        download_input(input_url, target_pdb)
        send_heartbeat(webhook_url, job_id, "Input downloaded", 0, num_designs)

        # ----- Stage 1: RFdiffusion -----
        try:
            stage_rfdiffusion(
                target_pdb, framework_pdb, backbones_qv,
                num_designs, cdr_lengths, hotspots_str,
                webhook_url=webhook_url, job_id=job_id,
            )
        except RuntimeError as exc:
            logger.error("RFdiffusion failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"RFdiffusion failed: {exc}",
            })
            return

        # ----- Stage 2: ProteinMPNN -----
        try:
            stage_proteinmpnn(
                backbones_qv, sequences_qv,
                seqs_per_backbone=mpnn_seqs,
                temperature=mpnn_temp,
                webhook_url=webhook_url, job_id=job_id,
                num_designs=num_designs,
            )
        except RuntimeError as exc:
            logger.error("ProteinMPNN failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"ProteinMPNN failed: {exc}",
                "partial": True,
            })
            return

        # ----- Stage 3: RF2 -----
        try:
            stage_rf2(
                sequences_qv, predictions_qv,
                recycles=rf2_recycles,
                webhook_url=webhook_url, job_id=job_id,
                num_designs=num_designs,
            )
        except RuntimeError as exc:
            logger.error("RF2 failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"RF2 validation failed: {exc}",
                "partial": True,
            })
            return

        send_heartbeat(webhook_url, job_id, "RF2 complete", num_designs, num_designs)

        # ----- Extract scores and PDBs -----
        try:
            extract_scores(predictions_qv, scores_tsv)
        except RuntimeError as exc:
            logger.error("Score extraction failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"Score extraction failed: {exc}",
            })
            return

        all_designs = parse_scores_tsv(scores_tsv)
        if not all_designs:
            logger.error("No designs found in scores TSV")
            post_webhook(webhook_url, job_id, pod_id, {
                "error": "RF2 produced no scored designs",
            })
            return

        # Extract PDB files from predictions quiver
        extracted_pdbs = extract_pdbs(predictions_qv, top_hits_dir)
        pdb_map = {Path(p).stem: p for p in extracted_pdbs}

        # ----- Filter and rank -----
        passing = filter_and_rank(all_designs)

        # ----- Prepare upload list -----
        candidates = []
        filenames_to_upload = []

        for rank_idx, design in enumerate(passing):
            rank = rank_idx + 1
            design_name = design["design_name"]

            # Match design to extracted PDB
            local_file = pdb_map.get(design_name)
            if not local_file:
                for key, path in pdb_map.items():
                    if design_name in key or key in design_name:
                        local_file = path
                        break

            if not local_file:
                logger.warning(
                    "No PDB found for design %s (available: %s)",
                    design_name, list(pdb_map.keys())[:10],
                )
                continue

            upload_filename = f"design_{rank:03d}.pdb"
            filenames_to_upload.append(upload_filename)

            candidates.append({
                "rank": rank,
                "design_name": design_name,
                "pdb_key": f"designs/{design_name}.pdb",
                "scores": design["scores"],
                "local_file": local_file,
                "upload_filename": upload_filename,
            })

        if filenames_to_upload:
            filenames_to_upload.append("metrics.csv")

        # ----- Upload outputs -----
        send_heartbeat(
            webhook_url, job_id, "Uploading results",
            len(candidates), len(candidates),
        )

        upload_urls = {}
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(
                    upload_endpoint, job_token, filenames_to_upload,
                )
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)

        failed_uploads = []
        for candidate in candidates:
            upload_filename = candidate["upload_filename"]
            local_file = candidate["local_file"]
            if upload_filename in upload_urls and os.path.exists(local_file):
                try:
                    upload_output(upload_urls[upload_filename], local_file)
                except RuntimeError as exc:
                    logger.warning("Failed to upload %s: %s", upload_filename, exc)
                    failed_uploads.append(upload_filename)

        # ----- Upload metrics CSV -----
        if candidates:
            metrics_csv_path = os.path.join(work_dir, "metrics.csv")
            write_metrics_csv(metrics_csv_path, candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], metrics_csv_path)
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
            "total_designs": num_designs,
            "rf2_scored": len(all_designs),
            "passing_filters": len(passing),
            "framework": framework,
            "cdr_lengths": cdr_lengths,
            "runtime_minutes": round(elapsed_minutes, 1),
            "next_steps": (
                "Recommend experimental validation: yeast display screening "
                "for top candidates, followed by SPR/BLI for binding kinetics. "
                "RFantibody designs have shown 78 nM affinity with 1.45 A RMSD "
                "to cryo-EM structures in published benchmarks."
            ),
        }
        if failed_uploads:
            result_payload["failed_uploads"] = failed_uploads

        post_webhook(webhook_url, job_id, pod_id, result_payload)

    except KeyError as exc:
        logger.error("Missing required key in job payload: %s", exc)
        post_webhook(webhook_url, job_id, pod_id, {
            "error": f"Missing required key in job payload: {exc}",
        })
    except Exception as exc:
        logger.exception("Unhandled pipeline error: %s", exc)
        try:
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"Pipeline crashed: {exc}",
            })
        except Exception:
            logger.error("Failed to send error webhook")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
