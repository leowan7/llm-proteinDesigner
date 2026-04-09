"""Standalone pipeline script for BoltzGen on RunPod GPU Pods.

Reads job configuration from the JOB_PAYLOAD environment variable,
runs BoltzGen protein binder design, uploads results via presigned URLs,
POSTs results to the Kendrew webhook, then exits.

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, upload endpoint, and webhook config
    WEBHOOK_URL     URL to POST results to (Kendrew backend)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    RUNPOD_POD_ID   RunPod pod ID (so backend can terminate after completion)

Pipeline stages:
  1. Download target PDB from presigned URL
  2. Write BoltzGen YAML spec to disk
  3. Run `boltzgen run spec.yaml` via subprocess
  4. Parse output CSV for ranked candidates
  5. Upload passing PDBs + metrics CSV
  6. POST results to webhook
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

import requests
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("boltzgen_pipeline")

# ---------------------------------------------------------------------------
# Filtering thresholds for BoltzGen output
# ---------------------------------------------------------------------------
IPTM_THRESHOLD = 0.70
PLDDT_THRESHOLD = 80.0
RMSD_THRESHOLD = 2.0  # refolding RMSD in angstroms


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def validate_env_vars() -> None:
    """Validate that all required environment variables are set.

    Crashes immediately if any required variable is missing, so the pod
    fails fast rather than burning GPU time on an incomplete configuration.
    """
    required = ["JOB_PAYLOAD", "WEBHOOK_URL", "JOB_ID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)


def startup_check() -> dict:
    """Log environment and dependency status at startup.

    Crashes if CUDA is not available (no point running a GPU pipeline on CPU).

    Returns:
        Dict of diagnostic checks for logging.
    """
    checks = {}

    # PyTorch and CUDA
    try:
        import torch
        checks["torch"] = torch.__version__
        checks["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            checks["gpu"] = torch.cuda.get_device_name(0)
            checks["cuda_version"] = torch.version.cuda
        else:
            logger.error("CUDA is not available. BoltzGen requires a GPU.")
            sys.exit(1)
    except ImportError:
        logger.error("PyTorch is not installed.")
        sys.exit(1)

    # BoltzGen
    try:
        result = subprocess.run(
            ["boltzgen", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        checks["boltzgen"] = result.stdout.strip() or "installed (no version string)"
    except FileNotFoundError:
        logger.error("boltzgen CLI not found on PATH.")
        sys.exit(1)
    except Exception as exc:
        checks["boltzgen_error"] = str(exc)

    # HuggingFace cache
    hf_home = os.environ.get("HF_HOME", "~/.cache/huggingface")
    checks["hf_home"] = hf_home
    checks["hf_home_exists"] = os.path.isdir(os.path.expanduser(hf_home))

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
        dest_path: Local filesystem destination path.
    """
    logger.info("Downloading input PDB -> %s", dest_path)
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download input PDB: HTTP {resp.status_code}")
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(resp.content)
    logger.info("Downloaded %d bytes", len(resp.content))


def request_upload_urls(
    upload_endpoint: str, job_token: str, filenames: list[str],
) -> dict[str, str]:
    """Request fresh presigned PUT URLs from the Kendrew backend.

    Args:
        upload_endpoint: URL of the /jobs/{job_id}/upload-urls endpoint.
        job_token: Job-specific Bearer token for authentication.
        filenames: List of filenames to upload.

    Returns:
        Dict mapping filename to presigned PUT URL.
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
    """
    data = Path(file_path).read_bytes()
    if file_path.endswith(".csv"):
        content_type = "text/csv"
    elif file_path.endswith(".cif"):
        content_type = "chemical/x-cif"
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
    """Run a subprocess command with timeout, logging output even on success.

    Args:
        cmd: Command and arguments to run.
        timeout: Maximum seconds before killing the process.
        cwd: Working directory for the subprocess.

    Returns:
        Combined stdout + stderr from the command.

    Raises:
        RuntimeError: If the command exits with a non-zero return code.
    """
    logger.info("Running: %s", " ".join(cmd[:8]) + ("..." if len(cmd) > 8 else ""))
    start = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
    )
    elapsed = time.time() - start
    combined_output = (result.stdout or "") + (result.stderr or "")

    # Always log last 2000 chars of output, even on success
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


# ===========================================================================
# BoltzGen YAML spec generation
# ===========================================================================

def write_yaml_spec(
    yaml_spec: dict, target_pdb_path: str, work_dir: str,
) -> str:
    """Write the BoltzGen YAML design specification to disk.

    The YAML spec defines the target entity, binder constraints, and
    hotspot residues for BoltzGen.

    Args:
        yaml_spec: Dict from job payload with entities, binder config.
        target_pdb_path: Path to the downloaded target PDB inside the container.
        work_dir: Working directory for writing the spec file.

    Returns:
        Path to the written YAML spec file.
    """
    # Rewrite entity file paths to point to the local target PDB
    for entity in yaml_spec.get("entities", []):
        entity["file"] = target_pdb_path

    spec_path = os.path.join(work_dir, "spec.yaml")
    with open(spec_path, "w") as fh:
        yaml.dump(yaml_spec, fh, default_flow_style=False)

    logger.info("Wrote BoltzGen YAML spec to %s", spec_path)
    with open(spec_path) as fh:
        logger.info("Spec contents:\n%s", fh.read())

    return spec_path


# ===========================================================================
# Output parsing
# ===========================================================================

def find_metrics_csv(output_dir: str) -> str | None:
    """Locate the BoltzGen metrics CSV in the output directory.

    BoltzGen writes files named final_designs_metrics_N.csv. Returns the
    first match found, or None if no metrics file exists.

    Args:
        output_dir: BoltzGen output directory.

    Returns:
        Path to the metrics CSV, or None.
    """
    for root, _dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if fname.startswith("final_designs_metrics") and fname.endswith(".csv"):
                return os.path.join(root, fname)
    return None


def find_design_files(output_dir: str) -> list[str]:
    """Locate designed PDB/CIF files in the BoltzGen output directory.

    Looks inside final_ranked_designs/ for structure files.

    Args:
        output_dir: BoltzGen output directory.

    Returns:
        Sorted list of paths to design structure files.
    """
    ranked_dir = os.path.join(output_dir, "final_ranked_designs")
    if not os.path.isdir(ranked_dir):
        # Fall back to searching entire output directory
        ranked_dir = output_dir

    design_files = []
    for fname in sorted(os.listdir(ranked_dir)):
        if fname.endswith((".pdb", ".cif")):
            design_files.append(os.path.join(ranked_dir, fname))

    return design_files


def parse_metrics_csv(csv_path: str) -> list[dict]:
    """Parse the BoltzGen metrics CSV into a list of scored designs.

    Expected columns include: design name/file, refolding_rmsd, ipTM, pLDDT.

    Args:
        csv_path: Path to the BoltzGen metrics CSV.

    Returns:
        List of dicts with design_name, scores, and file reference.
    """
    results = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        logger.info("Metrics CSV columns: %s", columns)

        for row in reader:
            # BoltzGen CSV column names may vary; handle common patterns
            design_name = (
                row.get("design_name")
                or row.get("design")
                or row.get("name")
                or row.get("file", "unknown")
            )
            # Strip path prefix and extension if present
            design_name = Path(design_name).stem

            scores = {}
            for key in ["refolding_rmsd", "rmsd", "RMSD"]:
                if key in row and row[key]:
                    scores["refolding_rmsd"] = float(row[key])
                    break
            for key in ["ipTM", "iptm", "iPTM"]:
                if key in row and row[key]:
                    scores["ipTM"] = float(row[key])
                    break
            for key in ["pLDDT", "plddt", "mean_plddt"]:
                if key in row and row[key]:
                    scores["pLDDT"] = float(row[key])
                    break

            results.append({
                "design_name": design_name,
                "scores": scores,
            })

    logger.info("Parsed %d designs from metrics CSV", len(results))
    return results


def filter_and_rank(designs: list[dict]) -> list[dict]:
    """Filter designs by quality thresholds and rank by ipTM.

    Args:
        designs: List of design dicts with scores.

    Returns:
        Filtered and ranked list of designs.
    """
    passing = []
    for design in designs:
        scores = design["scores"]
        iptm = scores.get("ipTM", 0.0)
        plddt = scores.get("pLDDT", 0.0)
        rmsd = scores.get("refolding_rmsd", 99.0)

        if iptm >= IPTM_THRESHOLD and plddt >= PLDDT_THRESHOLD and rmsd <= RMSD_THRESHOLD:
            passing.append(design)

    # Rank by ipTM descending
    passing.sort(key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True)

    logger.info(
        "Filtering: %d / %d pass (ipTM>=%.2f, pLDDT>=%.0f, RMSD<=%.1f)",
        len(passing), len(designs),
        IPTM_THRESHOLD, PLDDT_THRESHOLD, RMSD_THRESHOLD,
    )
    return passing


def write_output_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a standardized metrics CSV for upload to Kendrew.

    Args:
        csv_path: Destination path for the CSV.
        candidates: Ranked list of candidate dicts.
    """
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "design_name", "ipTM", "pLDDT", "refolding_rmsd",
        ])
        for candidate in candidates:
            scores = candidate["scores"]
            writer.writerow([
                candidate["rank"],
                candidate["design_name"],
                scores.get("ipTM", ""),
                scores.get("pLDDT", ""),
                scores.get("refolding_rmsd", ""),
            ])


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the BoltzGen pipeline: download -> design -> parse -> upload -> webhook."""
    validate_env_vars()
    startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    job_token = os.environ.get("JOB_TOKEN", "")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))

    job_payload = json.loads(job_payload_str)
    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    # Extract BoltzGen-specific config from job_spec parameters
    params = job_spec.get("parameters", {})
    yaml_spec = params.get("yaml_spec", {})
    protocol = params.get("protocol", "protein-anything")
    num_designs = params.get("num_designs", 10)
    budget = params.get("budget", 1000)

    pipeline_start = time.time()
    work_dir = tempfile.mkdtemp(prefix="boltzgen_job_")
    output_dir = os.path.join(work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    target_pdb = os.path.join(work_dir, "target.pdb")

    try:
        # ----- Download input PDB -----
        send_heartbeat(webhook_url, job_id, "Downloading input", 0, num_designs)
        download_input(input_url, target_pdb)

        # ----- Write YAML spec -----
        spec_path = write_yaml_spec(yaml_spec, target_pdb, work_dir)

        # ----- Run BoltzGen -----
        send_heartbeat(webhook_url, job_id, "Running BoltzGen", 0, num_designs)
        logger.info("=== Running BoltzGen design ===")

        cmd = [
            "boltzgen", "run", spec_path,
            "--output", output_dir,
            "--protocol", protocol,
            "--num_designs", str(num_designs),
            "--budget", str(budget),
        ]

        try:
            run_command(cmd, timeout=7200, cwd=work_dir)
        except RuntimeError as exc:
            logger.error("BoltzGen failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"BoltzGen failed: {exc}",
            })
            return

        send_heartbeat(webhook_url, job_id, "BoltzGen complete", num_designs, num_designs)

        # ----- List output contents for debugging -----
        for root, dirs, files in os.walk(output_dir):
            rel_root = os.path.relpath(root, output_dir)
            for fname in files:
                logger.info("Output file: %s/%s", rel_root, fname)

        # ----- Parse metrics CSV -----
        logger.info("=== Parsing BoltzGen output ===")
        metrics_csv_path = find_metrics_csv(output_dir)
        if not metrics_csv_path:
            logger.error("No metrics CSV found in BoltzGen output")
            post_webhook(webhook_url, job_id, pod_id, {
                "error": "BoltzGen produced no metrics CSV",
            })
            return

        logger.info("Found metrics CSV: %s", metrics_csv_path)
        all_designs = parse_metrics_csv(metrics_csv_path)

        if not all_designs:
            logger.error("Metrics CSV contained no designs")
            post_webhook(webhook_url, job_id, pod_id, {
                "error": "BoltzGen metrics CSV was empty",
            })
            return

        # ----- Filter and rank -----
        passing = filter_and_rank(all_designs)
        design_files = find_design_files(output_dir)

        # Build a lookup from design name to file path
        design_file_map = {}
        for fpath in design_files:
            stem = Path(fpath).stem
            design_file_map[stem] = fpath

        # ----- Prepare upload list -----
        candidates = []
        filenames_to_upload = []

        for rank_idx, design in enumerate(passing):
            rank = rank_idx + 1
            design_name = design["design_name"]
            design_file = design_file_map.get(design_name)

            if not design_file:
                logger.warning(
                    "No structure file found for design %s, skipping upload",
                    design_name,
                )
                continue

            ext = Path(design_file).suffix  # .pdb or .cif
            upload_filename = f"design_{rank:03d}{ext}"
            filenames_to_upload.append(upload_filename)

            candidates.append({
                "rank": rank,
                "design_name": design_name,
                "pdb_key": f"designs/{design_name}{ext}",
                "scores": design["scores"],
                "local_file": design_file,
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

        for candidate in candidates:
            upload_filename = candidate["upload_filename"]
            local_file = candidate["local_file"]
            if upload_filename in upload_urls and os.path.exists(local_file):
                try:
                    upload_output(upload_urls[upload_filename], local_file)
                except RuntimeError as exc:
                    logger.warning(
                        "Failed to upload %s: %s", upload_filename, exc,
                    )

        # ----- Upload metrics CSV -----
        if candidates:
            output_csv_path = os.path.join(work_dir, "metrics.csv")
            write_output_metrics_csv(output_csv_path, candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], output_csv_path)
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
            "boltzgen_scored": len(all_designs),
            "passing_filters": len(passing),
            "runtime_minutes": round(elapsed_minutes, 1),
            "next_steps": (
                "Recommend experimental validation: SPR or BLI binding assay "
                "for top candidates, followed by counter-screen for specificity. "
                "Consider yeast display library construction for affinity maturation "
                "of the best hits."
            ),
        }
        post_webhook(webhook_url, job_id, pod_id, result_payload)

    except Exception as exc:
        logger.exception("Unhandled pipeline error: %s", exc)
        post_webhook(webhook_url, job_id, pod_id, {
            "error": f"Unhandled error: {exc}",
        })
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
