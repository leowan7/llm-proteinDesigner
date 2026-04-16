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
  1. Download target structure from presigned URL
  2. Convert to CIF and re-index residues (BoltzGen requires CIF with chains at index 1)
  3. Write BoltzGen YAML design spec to disk
  4. Run `boltzgen run spec.yaml` via subprocess
  5. Parse output metrics CSV for ranked candidates
  6. Upload passing CIFs + metrics CSV
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
from urllib.parse import urlparse, urlunparse

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

# BoltzGen weight cache (baked into Docker image)
BOLTZGEN_CACHE = os.environ.get("HF_HOME", "/opt/boltzgen_cache")


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def startup_check() -> dict:
    """Log environment and dependency status at startup.

    Crashes if CUDA is not available or BoltzGen is not installed.
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

    # BoltzGen CLI
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

    # Weight cache
    checks["cache_dir"] = BOLTZGEN_CACHE
    checks["cache_exists"] = os.path.isdir(BOLTZGEN_CACHE)
    if os.path.isdir(BOLTZGEN_CACHE):
        cache_files = list(Path(BOLTZGEN_CACHE).rglob("*"))
        checks["cache_file_count"] = len(cache_files)

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

    # Always log last 2000 chars of output
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

    BoltzGen requires mmCIF format. If the input is a PDB file, convert it
    using gemmi. Also re-indexes chains so residues start at 1.

    Args:
        input_path: Path to the downloaded target file (.pdb or .cif).
        work_dir: Working directory for writing the CIF file.

    Returns:
        Path to the validated/converted CIF file.
    """
    import gemmi

    if input_path.endswith(".cif") or input_path.endswith(".mmcif"):
        # Already CIF — read, re-index, and write back
        logger.info("Input is CIF, re-indexing residues...")
        structure = gemmi.read_structure(input_path)
    else:
        # PDB -> CIF conversion
        logger.info("Converting PDB to CIF...")
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


# ===========================================================================
# BoltzGen YAML spec generation
# ===========================================================================

def write_yaml_spec(
    yaml_spec: dict, target_cif_path: str, work_dir: str,
) -> str:
    """Write the BoltzGen YAML design specification to disk.

    Rewrites entity file paths to point to the local target CIF,
    ensuring the spec references the container-local file.

    Args:
        yaml_spec: Dict from job payload with entities and constraints.
        target_cif_path: Path to the re-indexed target CIF inside the container.
        work_dir: Working directory for writing the spec file.

    Returns:
        Path to the written YAML spec file.
    """
    # Rewrite file entity paths to point to the local CIF
    for entity in yaml_spec.get("entities", []):
        if "file" in entity and isinstance(entity["file"], dict):
            entity["file"]["path"] = target_cif_path

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

    BoltzGen writes metrics to:
      - intermediate_designs_inverse_folded/aggregate_metrics_analyze.csv
      - intermediate_designs_inverse_folded/per_target_metrics_analyze.csv

    Falls back to searching for any *metrics*.csv in the output tree.

    Returns:
        Path to the best metrics CSV, or None.
    """
    # Primary location
    primary = os.path.join(
        output_dir, "intermediate_designs_inverse_folded",
        "aggregate_metrics_analyze.csv",
    )
    if os.path.isfile(primary):
        return primary

    # Secondary: per-target metrics
    secondary = os.path.join(
        output_dir, "intermediate_designs_inverse_folded",
        "per_target_metrics_analyze.csv",
    )
    if os.path.isfile(secondary):
        return secondary

    # Fallback: search for any metrics CSV
    for root, _dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if "metrics" in fname.lower() and fname.endswith(".csv"):
                return os.path.join(root, fname)

    return None


def find_design_files(output_dir: str, budget: int) -> list[str]:
    """Locate designed CIF files in the BoltzGen output directory.

    BoltzGen writes ranked designs to:
      final_ranked_designs/intermediate_ranked_{budget}_designs/

    Falls back to searching for CIF/PDB files in final_ranked_designs/.

    Args:
        output_dir: BoltzGen output directory.
        budget: The --budget value used (determines subdirectory name).

    Returns:
        Sorted list of paths to design structure files.
    """
    # Primary: budget-specific ranked directory
    ranked_dir = os.path.join(
        output_dir, "final_ranked_designs",
        f"intermediate_ranked_{budget}_designs",
    )
    if not os.path.isdir(ranked_dir):
        # Try other ranked subdirectories
        parent = os.path.join(output_dir, "final_ranked_designs")
        if os.path.isdir(parent):
            subdirs = sorted(os.listdir(parent))
            for d in subdirs:
                candidate = os.path.join(parent, d)
                if os.path.isdir(candidate):
                    ranked_dir = candidate
                    break
            else:
                ranked_dir = parent
        else:
            # Last resort: search entire output
            ranked_dir = output_dir

    design_files = []
    for root, _dirs, files in os.walk(ranked_dir):
        for fname in sorted(files):
            if fname.endswith((".cif", ".pdb")):
                design_files.append(os.path.join(root, fname))

    return sorted(design_files)


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


def parse_metrics_csv(csv_path: str) -> list[dict]:
    """Parse the BoltzGen metrics CSV into a list of scored designs.

    BoltzGen aggregate_metrics_analyze.csv may have various column names.
    We try common patterns for each metric.

    Returns:
        List of dicts with design_name and scores.
    """
    results = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        logger.info("Metrics CSV columns: %s", columns)

        for row in reader:
            # Design name: try several column names
            design_name = (
                row.get("design_name")
                or row.get("design")
                or row.get("name")
                or row.get("file")
                or row.get("sample")
                or "unknown"
            )
            # Strip path prefix and extension if present
            design_name = Path(design_name).stem

            scores = {}

            # Refolding RMSD
            for key in ["refolding_rmsd", "rmsd", "RMSD", "bb_rmsd",
                        "design_rmsd", "ca_rmsd"]:
                if key in row and row[key]:
                    scores["refolding_rmsd"] = _safe_float(row[key], 99.0)
                    break

            # ipTM
            for key in ["ipTM", "iptm", "iPTM", "interface_ptm",
                        "iptm_score"]:
                if key in row and row[key]:
                    scores["ipTM"] = _safe_float(row[key], 0.0)
                    break

            # pLDDT
            for key in ["pLDDT", "plddt", "mean_plddt", "binder_plddt",
                        "avg_plddt"]:
                if key in row and row[key]:
                    scores["pLDDT"] = _safe_float(row[key], 0.0)
                    break

            results.append({
                "design_name": design_name,
                "scores": scores,
            })

    logger.info("Parsed %d designs from metrics CSV", len(results))
    return results


def filter_and_rank(designs: list[dict]) -> list[dict]:
    """Filter designs by quality thresholds and rank by ipTM."""
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


def check_ubiquitin_risk(design_files: list[str]) -> list[str]:
    """Check for designs in the 73-76 amino acid range (ubiquitin-like).

    BoltzGen can produce designs that resemble ubiquitin in this length range.
    Returns a list of warning strings for any suspicious designs.
    """
    warnings = []
    for fpath in design_files:
        try:
            import gemmi
            structure = gemmi.read_structure(fpath)
            for model in structure:
                for chain in model:
                    n_res = sum(1 for _ in chain)
                    if 73 <= n_res <= 76:
                        warnings.append(
                            f"{Path(fpath).name} chain {chain.name}: {n_res} residues "
                            f"(ubiquitin-like length — BLAST-check recommended)"
                        )
        except Exception:
            continue
    return warnings


def write_output_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a standardized metrics CSV for upload to Kendrew."""
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
    """Run the BoltzGen pipeline: download -> CIF convert -> design -> parse -> upload -> webhook."""
    startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    job_token = os.environ.get("JOB_TOKEN", "")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))

    if not job_payload_str:
        logger.error("JOB_PAYLOAD environment variable not set")
        sys.exit(1)

    job_payload = json.loads(job_payload_str)
    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    # Extract BoltzGen-specific config from job_spec parameters
    params = job_spec.get("parameters", {})
    yaml_spec = params.get("yaml_spec", {})
    protocol = params.get("protocol", "protein-anything")
    num_designs = params.get("num_designs", 10000)
    budget = params.get("budget", 60)

    if not yaml_spec.get("entities"):
        logger.error("yaml_spec must contain at least one entity")
        post_webhook(webhook_url, job_id, pod_id, {
            "error": "Invalid yaml_spec: no entities defined",
        })
        sys.exit(1)

    pipeline_start = time.time()
    work_dir = tempfile.mkdtemp(prefix="boltzgen_job_")
    output_dir = os.path.join(work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    # Determine input file extension from URL or default to .pdb
    input_ext = ".pdb"
    input_url_path = urlparse(input_url).path
    if input_url_path.endswith(".cif"):
        input_ext = ".cif"
    target_input = os.path.join(work_dir, f"target_input{input_ext}")

    try:
        # ----- Stage 1: Download input -----
        send_heartbeat(webhook_url, job_id, "Downloading input", 0, budget)
        download_input(input_url, target_input)

        # ----- Stage 2: Convert to CIF and re-index -----
        send_heartbeat(webhook_url, job_id, "Preparing CIF", 0, budget)
        try:
            target_cif = ensure_cif(target_input, work_dir)
        except Exception as exc:
            logger.error("CIF conversion failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"CIF conversion failed: {exc}",
            })
            return

        # ----- Stage 3: Write YAML spec -----
        spec_path = write_yaml_spec(yaml_spec, target_cif, work_dir)

        # ----- Stage 4: Run BoltzGen -----
        send_heartbeat(webhook_url, job_id, "Running BoltzGen", 0, budget)
        logger.info("=== Running BoltzGen design ===")

        cmd = [
            "boltzgen", "run", spec_path,
            "--output", output_dir,
            "--protocol", protocol,
            "--num_designs", str(num_designs),
            "--budget", str(budget),
            "--devices", "1",
        ]

        # Reserve 30 min for pre/post-processing; rest for BoltzGen
        boltzgen_timeout = max(6600, 7200 - 1800)
        try:
            run_command(cmd, timeout=boltzgen_timeout, cwd=work_dir)
        except RuntimeError as exc:
            logger.error("BoltzGen failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"BoltzGen failed: {exc}",
            })
            return

        send_heartbeat(webhook_url, job_id, "BoltzGen complete", budget, budget)

        # ----- Log output tree for debugging -----
        for root, dirs, files in os.walk(output_dir):
            rel_root = os.path.relpath(root, output_dir)
            for fname in files:
                logger.info("Output file: %s/%s", rel_root, fname)

        # ----- Stage 5: Parse metrics CSV -----
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
        design_files = find_design_files(output_dir, budget)

        # Build a lookup from design name stem to file path
        design_file_map = {}
        for fpath in design_files:
            stem = Path(fpath).stem
            design_file_map[stem] = fpath
            # Also map without common prefixes/suffixes for fuzzy matching
            for prefix in ["design_", "ranked_", "sample_"]:
                if stem.startswith(prefix):
                    design_file_map[stem[len(prefix):]] = fpath

        # ----- Prepare upload list -----
        candidates = []
        filenames_to_upload = []

        for rank_idx, design in enumerate(passing):
            rank = rank_idx + 1
            design_name = design["design_name"]

            # Try exact match, then fuzzy match
            design_file = design_file_map.get(design_name)
            if not design_file:
                # Try matching just the numeric suffix
                for key, fpath in design_file_map.items():
                    if design_name in key or key in design_name:
                        design_file = fpath
                        break

            if not design_file:
                logger.warning(
                    "No structure file found for design %s (available: %s), skipping",
                    design_name, list(design_file_map.keys())[:10],
                )
                continue

            ext = Path(design_file).suffix  # .cif or .pdb
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

        failed_uploads = []
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
                    failed_uploads.append(upload_filename)

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

        # ----- Check for ubiquitin-risk designs -----
        ubi_warnings = check_ubiquitin_risk(
            [c["local_file"] for c in candidates if os.path.exists(c["local_file"])]
        )
        if ubi_warnings:
            logger.warning("Ubiquitin-risk designs detected: %s", ubi_warnings)

        # ----- POST results to webhook -----
        next_steps = (
            "Recommend experimental validation: SPR or BLI binding assay "
            "for top candidates, followed by counter-screen for specificity. "
            "Consider yeast display library construction for affinity maturation "
            "of the best hits."
        )
        if ubi_warnings:
            next_steps += (
                " WARNING: Some designs are in the 73-76 aa range and may "
                "resemble ubiquitin. BLAST-check these candidates before "
                "proceeding: " + "; ".join(ubi_warnings)
            )

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
            "next_steps": next_steps,
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
        post_webhook(webhook_url, job_id, pod_id, {
            "error": f"Unhandled error: {exc}",
        })
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
