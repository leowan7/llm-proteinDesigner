"""Standalone pipeline script for PXDesign on RunPod GPU Pods.

Reads job configuration from the JOB_PAYLOAD environment variable,
runs PXDesign binder generation in basic preset mode, uploads results
via presigned URLs, and POSTs results to the Kendrew webhook.

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, upload endpoint, and webhook config
    WEBHOOK_URL     URL to POST results to (Kendrew backend)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    RUNPOD_POD_ID   RunPod pod ID (so backend can terminate after completion)

Pipeline stages:
  1. Download target PDB from presigned URL
  2. Write PXDesign YAML spec to disk
  3. Validate input via pxdesign check-input
  4. Run pxdesign pipeline --preset basic
  5. Parse summary.csv for ranked candidates
  6. Upload passing PDBs/CIFs + metrics CSV
  7. POST results to webhook
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
logger = logging.getLogger("pxdesign_pipeline")

# ---------------------------------------------------------------------------
# Paths inside the container
# ---------------------------------------------------------------------------
PXDESIGN_DIR = os.environ.get("PXDESIGN_DIR", "/opt/pxdesign")

# Filtering thresholds for PXDesign output
IPTM_THRESHOLD = 0.70
PLDDT_THRESHOLD = 80.0
PAE_THRESHOLD = 10.0


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def startup_check():
    """Log environment and dependency status at startup.

    Validates that CUDA is available and all required environment variables
    are set. Crashes if CUDA is not available, since PXDesign requires GPU.

    Returns:
        Dict of diagnostic key-value pairs.

    Raises:
        SystemExit: If CUDA is not available or required env vars are missing.
    """
    checks = {}

    # Validate required environment variables
    required_vars = ["WEBHOOK_URL", "JOB_ID", "JOB_PAYLOAD"]
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
            logger.error("CUDA is not available — PXDesign requires GPU")
            sys.exit(1)
    except Exception as exc:
        logger.error("PyTorch import failed: %s", exc)
        sys.exit(1)

    # Check PXDesign CLI availability
    try:
        result = subprocess.run(
            ["pxdesign", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        checks["pxdesign_cli"] = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks["pxdesign_cli"] = False
        logger.warning("pxdesign CLI not found in PATH; will try python -m fallback")

    # Check PXDesign directory
    checks["pxdesign_dir_exists"] = os.path.isdir(PXDESIGN_DIR)

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
        url: Presigned GET URL for the target PDB.
        dest_path: Local file path to save the downloaded PDB.

    Raises:
        RuntimeError: If the download fails (non-200 status).
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
        RuntimeError: If the upload fails (non-2xx status).
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
    """Run a subprocess command with timeout and logging.

    Always logs the last 2000 characters of combined stdout+stderr,
    even on success, for debugging pipeline issues.

    Args:
        cmd: Command and arguments to run.
        timeout: Maximum execution time in seconds.
        cwd: Working directory for the subprocess.

    Returns:
        Combined stdout + stderr output.

    Raises:
        RuntimeError: If the command exits with non-zero status.
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
# YAML spec generation
# ===========================================================================

def build_yaml_spec(
    job_spec: dict, target_pdb_path: str,
) -> dict:
    """Build PXDesign YAML task spec from job parameters.

    Args:
        job_spec: Deserialized JobSpec dict from JOB_PAYLOAD.
        target_pdb_path: Path to the target PDB inside the container.

    Returns:
        Dict representing the PXDesign YAML spec.
    """
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    hotspots = job_spec.get("hotspot_residues", [])

    binder_length = params.get("binder_length", {"min": 50, "max": 100})
    if isinstance(binder_length, dict):
        length_spec = {
            "min": binder_length.get("min", 50),
            "max": binder_length.get("max", 100),
        }
    else:
        length_spec = {"min": 50, "max": 100}

    num_designs = params.get("num_designs", 10)

    chain_spec = {
        "crop": True,
        "hotspots": hotspots if hotspots else [],
    }

    yaml_spec = {
        "target": {
            "file": target_pdb_path,
            "chains": {
                chain: chain_spec,
            },
        },
        "binder_length": length_spec,
        "preset": "basic",
        "N_sample": num_designs,
    }

    logger.info(
        "YAML spec: chain=%s, hotspots=%s, binder_length=%s, N_sample=%d",
        chain, hotspots, length_spec, num_designs,
    )
    return yaml_spec


# ===========================================================================
# Result parsing
# ===========================================================================

def parse_summary_csv(csv_path: str) -> list[dict]:
    """Parse PXDesign summary.csv into a list of candidate dicts.

    PXDesign writes a summary.csv with design metrics including ipTM,
    pLDDT, pAE, and filter_status. This function reads and normalizes
    the output.

    Args:
        csv_path: Path to the summary.csv file.

    Returns:
        List of dicts with keys: design_name, scores (ipTM, pLDDT, pAE,
        filter_status), and file_path (path to design PDB/CIF).
    """
    if not os.path.exists(csv_path):
        logger.warning("summary.csv not found at %s", csv_path)
        return []

    results = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Normalize column names (PXDesign may use different casing)
            row_lower = {k.lower().strip(): v for k, v in row.items()}

            design_name = (
                row_lower.get("design_name")
                or row_lower.get("name")
                or row_lower.get("sample")
                or f"design_{len(results)}"
            )

            scores = {}
            for metric, keys in [
                ("ipTM", ["iptm", "ip_tm", "iptm_score"]),
                ("pLDDT", ["plddt", "mean_plddt", "plddt_score"]),
                ("pAE", ["pae", "mean_pae", "ipae", "i_pae"]),
            ]:
                for key in keys:
                    if key in row_lower and row_lower[key]:
                        try:
                            scores[metric] = float(row_lower[key])
                            break
                        except ValueError:
                            continue

            filter_status = (
                row_lower.get("filter_status")
                or row_lower.get("status")
                or row_lower.get("pass")
                or "unknown"
            )
            scores["filter_status"] = filter_status

            results.append({
                "design_name": design_name,
                "scores": scores,
            })

    logger.info("Parsed %d entries from summary.csv", len(results))
    return results


def find_design_files(output_dir: str) -> dict[str, str]:
    """Map design names to their PDB or CIF file paths in the output directory.

    Searches for .pdb and .cif files recursively in the output directory.

    Args:
        output_dir: PXDesign output directory.

    Returns:
        Dict mapping design name (stem) to file path.
    """
    design_files = {}
    for ext in ("*.pdb", "*.cif"):
        for path in Path(output_dir).rglob(ext):
            design_files[path.stem] = str(path)
    logger.info("Found %d design structure files in %s", len(design_files), output_dir)
    return design_files


# ===========================================================================
# Pipeline execution
# ===========================================================================

def validate_input(spec_path: str) -> None:
    """Run pxdesign check-input to validate the YAML spec before design.

    Args:
        spec_path: Path to the YAML spec file.

    Raises:
        RuntimeError: If validation fails.
    """
    logger.info("Validating PXDesign input spec: %s", spec_path)
    try:
        run_command(
            ["pxdesign", "check-input", "--yaml", spec_path],
            timeout=120,
        )
        logger.info("Input validation passed")
    except FileNotFoundError:
        # CLI not in PATH — try python -m fallback
        run_command(
            ["python3", "-m", "pxdesign", "check-input", "--yaml", spec_path],
            timeout=120,
        )
        logger.info("Input validation passed (python -m fallback)")


def run_pxdesign(
    spec_path: str,
    output_dir: str,
    num_designs: int,
) -> None:
    """Run the PXDesign pipeline in basic preset mode.

    Tries the pxdesign CLI first; falls back to python -m pxdesign if the
    CLI is not in PATH.

    Args:
        spec_path: Path to the YAML spec file.
        output_dir: Directory for PXDesign output.
        num_designs: Number of samples to generate.

    Raises:
        RuntimeError: If PXDesign fails.
    """
    cmd = [
        "pxdesign", "pipeline",
        "--preset", "basic",
        "-i", spec_path,
        "-o", output_dir,
        "--N_sample", str(num_designs),
        "--dtype", "bf16",
    ]

    try:
        run_command(cmd, timeout=7200, cwd=PXDESIGN_DIR)
    except (RuntimeError, FileNotFoundError) as exc:
        if isinstance(exc, FileNotFoundError) or "No such file" in str(exc):
            logger.warning("pxdesign CLI not found, trying python -m fallback")
            fallback_cmd = ["python3", "-m", "pxdesign", "pipeline"] + cmd[2:]
            run_command(fallback_cmd, timeout=7200, cwd=PXDESIGN_DIR)
        else:
            raise


def write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a normalized metrics CSV summarizing all passing candidates.

    Args:
        csv_path: Output CSV file path.
        candidates: List of candidate dicts with rank, pdb_key, scores.
    """
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "design_name", "ipTM", "pLDDT", "pAE", "filter_status",
        ])
        for candidate in candidates:
            design_name = Path(candidate["pdb_key"]).stem
            scores = candidate["scores"]
            writer.writerow([
                candidate["rank"],
                design_name,
                scores.get("ipTM", ""),
                scores.get("pLDDT", ""),
                scores.get("pAE", ""),
                scores.get("filter_status", ""),
            ])


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full PXDesign pipeline.

    Download target PDB -> write YAML spec -> validate -> run PXDesign ->
    parse results -> filter and rank -> upload -> webhook.
    """
    checks = startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))
    job_token = os.environ.get("JOB_TOKEN", "")

    job_payload = json.loads(job_payload_str)
    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    num_designs = job_spec.get("parameters", {}).get("num_designs", 10)
    pipeline_start = time.time()

    work_dir = tempfile.mkdtemp(prefix="pxdesign_job_")
    target_pdb = os.path.join(work_dir, "target.pdb")
    output_dir = os.path.join(work_dir, "pxdesign_output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        # ----- Download input PDB -----
        download_input(input_url, target_pdb)
        send_heartbeat(webhook_url, job_id, "Input downloaded", 0, num_designs)

        # ----- Build YAML spec -----
        yaml_spec = build_yaml_spec(job_spec, target_pdb)
        spec_path = os.path.join(work_dir, "spec.yaml")
        with open(spec_path, "w") as fh:
            yaml.dump(yaml_spec, fh, default_flow_style=False)
        logger.info("YAML spec written to %s", spec_path)

        # ----- Validate input -----
        try:
            validate_input(spec_path)
        except (RuntimeError, FileNotFoundError) as exc:
            logger.warning(
                "Input validation failed or unavailable: %s. Proceeding anyway.", exc,
            )

        # ----- Run PXDesign -----
        send_heartbeat(webhook_url, job_id, "Running PXDesign", 0, num_designs)
        try:
            run_pxdesign(spec_path, output_dir, num_designs)
        except RuntimeError as exc:
            logger.error("PXDesign pipeline failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"PXDesign pipeline failed: {exc}",
            })
            return

        send_heartbeat(webhook_url, job_id, "PXDesign complete", num_designs, num_designs)

        # ----- Parse results -----
        # Look for summary.csv in output directory (check common locations)
        summary_csv = None
        for candidate_path in [
            os.path.join(output_dir, "summary.csv"),
            os.path.join(output_dir, "results", "summary.csv"),
            os.path.join(output_dir, "metrics", "summary.csv"),
        ]:
            if os.path.exists(candidate_path):
                summary_csv = candidate_path
                break

        # If no summary.csv found, search recursively
        if summary_csv is None:
            csv_matches = list(Path(output_dir).rglob("summary.csv"))
            if csv_matches:
                summary_csv = str(csv_matches[0])

        if summary_csv is None:
            logger.error("No summary.csv found in PXDesign output")
            # List what was actually produced for debugging
            all_files = list(Path(output_dir).rglob("*"))
            logger.info(
                "PXDesign output contents (%d files): %s",
                len(all_files),
                [str(f) for f in all_files[:50]],
            )
            post_webhook(webhook_url, job_id, pod_id, {
                "error": "PXDesign produced no summary.csv",
                "output_files": [str(f.name) for f in all_files[:50]],
            })
            return

        parsed_results = parse_summary_csv(summary_csv)
        design_files = find_design_files(output_dir)

        # ----- Filter and rank -----
        passing = []
        for result in parsed_results:
            scores = result["scores"]
            iptm = scores.get("ipTM", 0.0)
            plddt = scores.get("pLDDT", 0.0)
            pae = scores.get("pAE", 99.0)

            # Accept designs that pass PXDesign's own filter OR meet our thresholds
            pxdesign_passed = scores.get("filter_status", "").lower() in (
                "pass", "passed", "true", "1", "yes",
            )
            threshold_passed = (
                iptm >= IPTM_THRESHOLD
                and plddt >= PLDDT_THRESHOLD
                and pae <= PAE_THRESHOLD
            )

            if pxdesign_passed or threshold_passed:
                passing.append(result)

        # Sort by ipTM descending
        passing.sort(
            key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True,
        )

        logger.info(
            "Filtering: %d / %d pass (ipTM>=%.2f, pLDDT>=%.0f, pAE<=%.0f or PXDesign filter=pass)",
            len(passing), len(parsed_results),
            IPTM_THRESHOLD, PLDDT_THRESHOLD, PAE_THRESHOLD,
        )

        # ----- Upload outputs (on-demand URLs) -----
        candidates = []
        filenames_to_upload = []

        for rank_idx, result in enumerate(passing):
            design_name = result["design_name"]
            if design_name in design_files:
                ext = Path(design_files[design_name]).suffix
                filenames_to_upload.append(f"design_{rank_idx + 1:03d}{ext}")

        if filenames_to_upload:
            filenames_to_upload.append("metrics.csv")

        # Request fresh presigned upload URLs from the backend
        upload_urls = {}
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(
                    upload_endpoint, job_token, filenames_to_upload,
                )
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)

        for rank_idx, result in enumerate(passing):
            rank = rank_idx + 1
            design_name = result["design_name"]

            # Determine the file extension and build the key
            if design_name in design_files:
                local_path = design_files[design_name]
                ext = Path(local_path).suffix
            else:
                local_path = None
                ext = ".pdb"

            pdb_key = f"designs/{design_name}{ext}"
            candidate = {
                "rank": rank,
                "pdb_key": pdb_key,
                "scores": result["scores"],
            }
            candidates.append(candidate)

            upload_filename = f"design_{rank:03d}{ext}"
            if upload_filename in upload_urls and local_path and os.path.exists(local_path):
                try:
                    upload_output(upload_urls[upload_filename], local_path)
                except RuntimeError as exc:
                    logger.warning(
                        "Failed to upload structure for rank %d: %s", rank, exc,
                    )

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
            "parsed_results": len(parsed_results),
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
        logger.error("Pipeline crashed: %s", exc, exc_info=True)
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
