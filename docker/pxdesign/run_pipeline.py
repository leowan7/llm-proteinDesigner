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
  1. Download target structure from presigned URL
  2. Convert to CIF and re-index residues (PXDesign recommends CIF)
  3. Write PXDesign YAML spec to disk (crop as residue range list)
  4. Validate input via pxdesign check-input
  5. Run pxdesign pipeline --preset basic
  6. Parse summary.csv for ranked candidates (af2_* prefixed columns)
  7. Upload passing PDBs/CIFs + metrics CSV
  8. POST results to webhook
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

def startup_check() -> dict:
    """Log environment and dependency status at startup.

    Crashes if CUDA is not available or required env vars are missing.
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
    except ImportError:
        logger.error("PyTorch is not installed.")
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

    # Check PXDesign directory and weights
    checks["pxdesign_dir_exists"] = os.path.isdir(PXDESIGN_DIR)
    weights_dir = os.path.join(PXDESIGN_DIR, "tool_weights")
    checks["weights_dir_exists"] = os.path.isdir(weights_dir)

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
    logger.info("Downloading input structure -> %s", dest_path)
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
    """Run a subprocess command with timeout and logging."""
    logger.info("Running: %s", " ".join(cmd[:8]) + ("..." if len(cmd) > 8 else ""))
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


# ===========================================================================
# CIF conversion and re-indexing
# ===========================================================================

def ensure_cif(input_path: str, work_dir: str) -> str:
    """Ensure input is in CIF format. Convert from PDB if needed.

    PXDesign strongly recommends CIF to avoid chain/residue ID remapping
    during internal PDB-to-CIF conversion. Re-indexes chains so residues
    start at 1 (required for correct crop range interpretation).

    Returns:
        Path to the validated/converted CIF file.
    """
    import gemmi

    structure = gemmi.read_structure(input_path)

    # Re-index: ensure each chain starts at residue 1 using label_seq_id
    for model in structure:
        for chain in model:
            for idx, residue in enumerate(chain):
                residue.label_seq = idx + 1
                residue.seqid = gemmi.SeqId(str(idx + 1))

    cif_path = os.path.join(work_dir, "target.cif")
    structure.make_mmcif_document().write_file(cif_path)
    logger.info("CIF written to %s (%d bytes)", cif_path, os.path.getsize(cif_path))
    return cif_path


def get_chain_length(cif_path: str, chain_id: str) -> int:
    """Count residues in a specific chain of a CIF file.

    Used to generate the crop range for PXDesign YAML spec.
    """
    import gemmi

    structure = gemmi.read_structure(cif_path)
    for model in structure:
        for chain in model:
            if chain.name == chain_id:
                return sum(1 for _ in chain)

    raise ValueError(f"Chain {chain_id} not found in {cif_path}")


# ===========================================================================
# YAML spec generation
# ===========================================================================

def build_yaml_spec(
    job_spec: dict, target_cif_path: str,
) -> dict:
    """Build PXDesign YAML task spec from job parameters.

    Reads the target CIF to determine chain length for the crop range.
    PXDesign requires crop as a list of string ranges, e.g. ["1-116"].

    Args:
        job_spec: Deserialized JobSpec dict from JOB_PAYLOAD.
        target_cif_path: Path to the re-indexed target CIF inside the container.

    Returns:
        Dict representing the PXDesign YAML spec.
    """
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    hotspots = job_spec.get("hotspot_residues", [])

    binder_length = params.get("binder_length", 80)
    # PXDesign accepts integer (e.g. 80) or dict (e.g. {"min": 50, "max": 100})
    if isinstance(binder_length, dict):
        binder_length = binder_length
    num_designs = params.get("num_designs", 100)

    # Determine chain length from the CIF for crop range
    chain_length = get_chain_length(target_cif_path, chain)
    logger.info("Target chain %s has %d residues", chain, chain_length)

    chain_spec = {
        "crop": [f"1-{chain_length}"],
        "hotspots": hotspots if hotspots else [],
    }

    yaml_spec = {
        "target": {
            "file": target_cif_path,
            "chains": {
                chain: chain_spec,
            },
        },
        "binder_length": binder_length,
        "preset": "basic",
        "N_sample": num_designs,
    }

    logger.info(
        "YAML spec: chain=%s, crop=[1-%d], hotspots=%s, binder_length=%s, N_sample=%d",
        chain, chain_length, hotspots, binder_length, num_designs,
    )
    return yaml_spec


# ===========================================================================
# Result parsing
# ===========================================================================

def _safe_float(value: str, default: float) -> float:
    """Parse a float from a CSV value, returning default on failure."""
    if not value or not value.strip():
        return default
    try:
        parsed = float(value)
        if parsed != parsed:  # NaN check
            return default
        return parsed
    except (ValueError, TypeError):
        return default


def parse_summary_csv(csv_path: str) -> list[dict]:
    """Parse PXDesign summary.csv into a list of candidate dicts.

    PXDesign summary.csv uses af2_* prefixed column names for AF2-IG metrics
    and AF2-IG-success / AF2-IG-easy-success boolean columns for filter status.

    Returns:
        List of dicts with design_name, scores, and filter status.
    """
    if not os.path.exists(csv_path):
        logger.warning("summary.csv not found at %s", csv_path)
        return []

    results = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        logger.info("summary.csv columns: %s", columns)

        # Normalize column names for lookup
        for row in reader:
            row_lower = {k.lower().strip(): v for k, v in row.items()}

            design_name = (
                row_lower.get("design_name")
                or row_lower.get("name")
                or row_lower.get("sample")
                or f"design_{len(results)}"
            )

            scores = {}

            # PXDesign uses af2_* prefixed columns
            for metric, keys in [
                ("ipTM", ["af2_iptm", "af2_ip_tm", "iptm", "iptm_score", "ip_tm"]),
                ("pLDDT", ["af2_plddt", "af2_mean_plddt", "plddt", "mean_plddt"]),
                ("pAE", ["af2_ipae", "af2_pae", "ipae", "pae", "i_pae", "mean_pae"]),
            ]:
                for key in keys:
                    if key in row_lower and row_lower[key]:
                        scores[metric] = _safe_float(row_lower[key], 0.0 if metric != "pAE" else 99.0)
                        break

            # Filter status from AF2-IG-success column
            filter_status = "unknown"
            for key in ["af2-ig-success", "af2-ig-easy-success", "filter_status", "status", "pass"]:
                if key in row_lower and row_lower[key]:
                    val = row_lower[key].strip().lower()
                    if val in ("true", "1", "yes", "pass", "passed"):
                        filter_status = "pass"
                    elif val in ("false", "0", "no", "fail", "failed"):
                        filter_status = "fail"
                    else:
                        filter_status = val
                    break
            scores["filter_status"] = filter_status

            results.append({
                "design_name": design_name,
                "scores": scores,
            })

    logger.info("Parsed %d entries from summary.csv", len(results))
    return results


def find_design_files(output_dir: str) -> dict[str, str]:
    """Map design names to their PDB or CIF file paths in the output directory.

    Searches passing-AF2-IG/ and passing-AF2-IG-easy/ directories first,
    then falls back to recursive search.

    Returns:
        Dict mapping design name (stem) to file path.
    """
    design_files = {}

    # Search in priority order: strict filter first, then easy filter
    for subdir in ["passing-AF2-IG", "passing-AF2-IG-easy", "orig_designed"]:
        candidate_dir = os.path.join(output_dir, subdir)
        if os.path.isdir(candidate_dir):
            for ext in ("*.pdb", "*.cif"):
                for path in Path(candidate_dir).rglob(ext):
                    if path.stem not in design_files:
                        design_files[path.stem] = str(path)

    # Fallback: search entire output directory
    if not design_files:
        for ext in ("*.pdb", "*.cif"):
            for path in Path(output_dir).rglob(ext):
                if path.stem not in design_files:
                    design_files[path.stem] = str(path)

    logger.info("Found %d design structure files in %s", len(design_files), output_dir)
    return design_files


# ===========================================================================
# Pipeline execution
# ===========================================================================

def validate_input(spec_path: str) -> None:
    """Run pxdesign check-input to validate the YAML spec before design.

    Raises RuntimeError if validation fails. This catches config errors
    before consuming GPU time.
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

    Tries the pxdesign CLI first; falls back to python -m pxdesign.
    """
    cmd = [
        "pxdesign", "pipeline",
        "--preset", "basic",
        "-i", spec_path,
        "-o", output_dir,
        "--N_sample", str(num_designs),
        "--dtype", "bf16",
    ]

    # Reserve 30 min for pre/post processing
    pxdesign_timeout = max(5400, 7200 - 1800)
    try:
        run_command(cmd, timeout=pxdesign_timeout, cwd=PXDESIGN_DIR)
    except FileNotFoundError:
        logger.warning("pxdesign CLI not found, trying python -m fallback")
        fallback_cmd = ["python3", "-m", "pxdesign", "pipeline"] + cmd[2:]
        run_command(fallback_cmd, timeout=pxdesign_timeout, cwd=PXDESIGN_DIR)


def write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a normalized metrics CSV summarizing all passing candidates."""
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
    """Run the full PXDesign pipeline."""
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

    num_designs = job_spec.get("parameters", {}).get("num_designs", 100)
    pipeline_start = time.time()

    work_dir = tempfile.mkdtemp(prefix="pxdesign_job_")
    output_dir = os.path.join(work_dir, "pxdesign_output")
    os.makedirs(output_dir, exist_ok=True)

    # Determine input file extension from URL
    input_ext = ".pdb"
    input_url_path = urlparse(input_url).path
    if input_url_path.endswith(".cif"):
        input_ext = ".cif"
    target_input = os.path.join(work_dir, f"target_input{input_ext}")

    try:
        # ----- Stage 1: Download input -----
        download_input(input_url, target_input)
        send_heartbeat(webhook_url, job_id, "Input downloaded", 0, num_designs)

        # ----- Stage 2: Convert to CIF and re-index -----
        send_heartbeat(webhook_url, job_id, "Preparing CIF", 0, num_designs)
        try:
            target_cif = ensure_cif(target_input, work_dir)
        except Exception as exc:
            logger.error("CIF conversion failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"CIF conversion failed: {exc}",
            })
            return

        # ----- Stage 3: Build YAML spec -----
        try:
            yaml_spec = build_yaml_spec(job_spec, target_cif)
        except ValueError as exc:
            logger.error("YAML spec generation failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"YAML spec generation failed: {exc}",
            })
            return

        spec_path = os.path.join(work_dir, "spec.yaml")
        with open(spec_path, "w") as fh:
            yaml.dump(yaml_spec, fh, default_flow_style=False)
        logger.info("YAML spec written to %s", spec_path)
        with open(spec_path) as fh:
            logger.info("Spec contents:\n%s", fh.read())

        # ----- Stage 4: Validate input -----
        try:
            validate_input(spec_path)
        except RuntimeError as exc:
            logger.error("Input validation failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"PXDesign input validation failed: {exc}",
            })
            return

        # ----- Stage 5: Run PXDesign -----
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

        # ----- Log output tree for debugging -----
        for root, dirs, files in os.walk(output_dir):
            rel_root = os.path.relpath(root, output_dir)
            for fname in files:
                logger.info("Output file: %s/%s", rel_root, fname)

        # ----- Stage 6: Parse results -----
        summary_csv = None
        for candidate_path in [
            os.path.join(output_dir, "summary.csv"),
            os.path.join(output_dir, "results", "summary.csv"),
        ]:
            if os.path.exists(candidate_path):
                summary_csv = candidate_path
                break

        # Fallback: recursive search
        if summary_csv is None:
            csv_matches = list(Path(output_dir).rglob("summary.csv"))
            if csv_matches:
                summary_csv = str(csv_matches[0])

        if summary_csv is None:
            logger.error("No summary.csv found in PXDesign output")
            all_files = list(Path(output_dir).rglob("*"))
            logger.info(
                "PXDesign output contents (%d files): %s",
                len(all_files),
                [str(f.name) for f in all_files[:50]],
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

            # Accept designs that pass PXDesign's AF2-IG filter OR meet our thresholds
            pxdesign_passed = scores.get("filter_status", "") == "pass"
            threshold_passed = (
                iptm >= IPTM_THRESHOLD
                and plddt >= PLDDT_THRESHOLD
                and pae <= PAE_THRESHOLD
            )

            if pxdesign_passed or threshold_passed:
                passing.append(result)

        passing.sort(
            key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True,
        )

        logger.info(
            "Filtering: %d / %d pass (ipTM>=%.2f, pLDDT>=%.0f, pAE<=%.0f or AF2-IG=pass)",
            len(passing), len(parsed_results),
            IPTM_THRESHOLD, PLDDT_THRESHOLD, PAE_THRESHOLD,
        )

        # ----- Prepare upload list -----
        candidates = []
        filenames_to_upload = []

        for rank_idx, result in enumerate(passing):
            rank = rank_idx + 1
            design_name = result["design_name"]

            # Try exact match, then fuzzy match
            local_path = design_files.get(design_name)
            if not local_path:
                for key, fpath in design_files.items():
                    if design_name in key or key in design_name:
                        local_path = fpath
                        break

            if local_path:
                ext = Path(local_path).suffix
            else:
                ext = ".pdb"
                logger.warning(
                    "No structure file found for design %s (available: %s)",
                    design_name, list(design_files.keys())[:10],
                )

            upload_filename = f"design_{rank:03d}{ext}"
            filenames_to_upload.append(upload_filename)

            candidates.append({
                "rank": rank,
                "pdb_key": f"designs/{design_name}{ext}",
                "scores": result["scores"],
                "local_file": local_path,
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
            local_file = candidate.get("local_file")
            if upload_filename in upload_urls and local_file and os.path.exists(local_file):
                try:
                    upload_output(upload_urls[upload_filename], local_file)
                except RuntimeError as exc:
                    logger.warning(
                        "Failed to upload %s: %s", upload_filename, exc,
                    )
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
            "parsed_results": len(parsed_results),
            "runtime_minutes": round(elapsed_minutes, 1),
            "next_steps": (
                "PXDesign uses multi-predictor confidence filtering (AF2-IG) "
                "which achieves 20-73% experimental hit rates in published benchmarks. "
                "Recommend SPR or BLI binding assay for top candidates, followed by "
                "counter-screen for specificity. Consider extended mode (with MSA) "
                "for Protenix-level confidence filtering on the best hits."
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
