"""Standalone pipeline script for RunPod GPU Pods — FreeBindCraft binder design.

Uses FreeBindCraft (github.com/cytokineking/FreeBindCraft), the PyRosetta-free
fork of BindCraft. Replaces Rosetta relaxation with OpenMM, shape complementarity
with sc-rs, and SASA with FreeSASA/Biopython. Fully MIT licensed.

Reads job configuration from the JOB_PAYLOAD environment variable,
runs the FreeBindCraft binder design pipeline, uploads results via presigned URLs,
POSTs results to the Kendrew webhook, then exits.

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, upload endpoint, and webhook config
    WEBHOOK_URL     URL to POST results to (Kendrew backend)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    RUNPOD_POD_ID   RunPod pod ID (so backend can terminate after completion)

Pipeline:
  1. Download target PDB from presigned URL
  2. Write BindCraft settings JSON to disk
  3. Run BindCraft (generates, filters, and AF2-validates binders internally)
  4. Parse output CSV for ranked candidates
  5. Upload passing PDBs + metrics CSV via on-demand presigned URLs
  6. POST results to webhook
"""

import base64
import csv
import datetime
import json
import logging
import os
import re
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
logger = logging.getLogger("bindcraft_pipeline")

# ---------------------------------------------------------------------------
# Paths inside the container (all weights baked into the Docker image)
# ---------------------------------------------------------------------------
BINDCRAFT_DIR = os.environ.get("BINDCRAFT_DIR", "/opt/BindCraft")
BINDCRAFT_SCRIPT = f"{BINDCRAFT_DIR}/bindcraft.py"
BINDCRAFT_FILTERS = f"{BINDCRAFT_DIR}/settings_filters/default_filters.json"
BINDCRAFT_ADVANCED = f"{BINDCRAFT_DIR}/settings_advanced/default_4stage_multimer.json"


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def startup_check():
    """Verify GPU, dependencies, and critical files at boot. Crash if GPU unavailable."""
    checks = {}

    # --- JAX + GPU (FreeBindCraft uses JAX, not PyTorch) ---
    try:
        import jax
        checks["jax"] = jax.__version__
        checks["jax_devices"] = [str(d) for d in jax.devices()]
        gpu_devices = jax.devices("gpu")
        if not gpu_devices:
            logger.error("No JAX GPU devices found — FreeBindCraft requires a GPU")
            sys.exit(1)
        checks["gpu"] = str(gpu_devices[0])
    except Exception as exc:
        logger.error("JAX import/GPU check failed: %s", exc)
        sys.exit(1)

    # --- Biopython ---
    try:
        from Bio.PDB import PDBParser
        checks["biopython"] = "ok"
    except Exception as exc:
        checks["biopython_error"] = str(exc)

    # --- BindCraft files ---
    for label, path in [
        ("bindcraft_script", BINDCRAFT_SCRIPT),
        ("bindcraft_filters", BINDCRAFT_FILTERS),
        ("bindcraft_advanced", BINDCRAFT_ADVANCED),
        ("bindcraft_dir", BINDCRAFT_DIR),
    ]:
        checks[label] = os.path.exists(path)

    logger.info("Startup diagnostics: %s", json.dumps(checks, indent=2))

    # --- Validate required env vars ---
    missing = []
    for var in ["JOB_PAYLOAD", "WEBHOOK_URL", "JOB_ID"]:
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    return checks


# ===========================================================================
# Helper functions
# ===========================================================================

class _HeartbeatThread:
    """Background thread that emits heartbeats during long subprocess runs.

    BindCraft's main subprocess can block Python for 5–15 min inside JAX
    compile + AF2 first-forward-pass before it streams any output. Without
    a background heartbeat, the Kendrew stale-detection cron kills a
    perfectly healthy job. See cleanup.py:STALE_HEARTBEAT_SECONDS.
    """

    def __init__(
        self,
        webhook_url: str,
        job_id: str,
        stage: str,
        designs_completed: int = 0,
        designs_total: int = 0,
        interval_seconds: int = 60,
    ) -> None:
        import threading
        self._webhook_url = webhook_url
        self._job_id = job_id
        self._stage = stage
        self._done = designs_completed
        self._total = designs_total
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        # Fire one immediately so the backend's last_heartbeat_at updates right
        # away, then keep pinging every ``interval_seconds`` until stopped.
        while not self._stop.is_set():
            try:
                send_heartbeat(
                    self._webhook_url, self._job_id, self._stage,
                    self._done, self._total,
                )
            except Exception as exc:
                logger.warning("Background heartbeat emit failed: %s", exc)
            # Sleep on the event so stop() returns promptly.
            self._stop.wait(self._interval)

    def start(self) -> None:
        import threading
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="heartbeat",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "_HeartbeatThread":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()


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
    # Derive heartbeat URL safely using urllib rather than brittle string replace
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(webhook_url)
    heartbeat_path = "/webhooks/heartbeat"
    heartbeat_url = urlunparse(parsed._replace(path=heartbeat_path))
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
    logger.info("Downloading input PDB -> %s", dest_path)
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download input PDB: HTTP {resp.status_code}")
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(resp.content)
    logger.info("Downloaded %d bytes", len(resp.content))


def request_upload_urls(upload_endpoint: str, job_token: str, filenames: list[str]) -> dict[str, str]:
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
        raise RuntimeError(f"Failed to get upload URLs: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()["urls"]


def upload_output(url: str, file_path: str) -> None:
    """Upload a file to R2/S3 via a presigned PUT URL."""
    data = Path(file_path).read_bytes()
    content_type = "text/csv" if file_path.endswith(".csv") else "chemical/x-pdb"
    resp = requests.put(
        url, data=data, headers={"Content-Type": content_type}, timeout=120,
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Upload failed for {file_path}: HTTP {resp.status_code}")
    logger.info("Uploaded %s (%d bytes)", file_path, len(data))


def run_command(cmd: list[str], timeout: int = 14400, cwd: str | None = None) -> str:
    """Run a subprocess command with timeout and logging.

    Always logs last 2000 chars of stdout/stderr, even on success.

    Args:
        cmd: Command and arguments list.
        timeout: Max seconds to wait (default 4 hours for BindCraft).
        cwd: Working directory for the subprocess.

    Returns:
        Combined stdout + stderr output.
    """
    logger.info("Running: %s", " ".join(cmd[:8]) + ("..." if len(cmd) > 8 else ""))
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    elapsed = time.time() - start
    combined_output = (result.stdout or "") + (result.stderr or "")

    # Always log last 2000 chars, even on success
    logger.info(
        "Command finished in %.1fs (exit code %d). Output tail:\n%s",
        elapsed, result.returncode, combined_output[-2000:],
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
# BindCraft config and output parsing
# ===========================================================================

def write_bindcraft_settings(job_spec: dict, target_pdb_path: str, output_dir: str) -> str:
    """Write the BindCraft settings JSON file to disk.

    Args:
        job_spec: Deserialized JobSpec dict from the backend.
        target_pdb_path: Path to the target PDB inside the container.
        output_dir: Directory where BindCraft should write its outputs.

    Returns:
        Path to the written settings JSON file.
    """
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    hotspots = job_spec.get("hotspot_residues", [])

    binder_length = params.get("binder_length", {"min": 50, "max": 100})
    if isinstance(binder_length, dict):
        min_len = binder_length.get("min", 50)
        max_len = binder_length.get("max", 100)
    else:
        min_len, max_len = 50, 100

    num_designs = params.get("num_designs", 10)
    hotspot_str = ",".join(str(res) for res in hotspots) if hotspots else ""

    settings = {
        "starting_pdb": target_pdb_path,
        "chains": chain,
        "target_hotspot_residues": hotspot_str,
        "lengths": [min_len, max_len],
        "number_of_final_designs": num_designs,
        "binder_name": "design",
        "design_path": output_dir,
    }

    settings_path = os.path.join(output_dir, "target_settings.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(settings_path, "w") as fh:
        json.dump(settings, fh, indent=2)

    logger.info("Wrote BindCraft settings to %s: %s", settings_path, json.dumps(settings))
    return settings_path


def parse_bindcraft_results(output_dir: str) -> list[dict]:
    """Parse BindCraft output directory for ranked candidates and metrics.

    BindCraft writes:
      - {output_dir}/Accepted/*.pdb (final passing designs)
      - {output_dir}/final_design_stats.csv with per-design metrics

    Args:
        output_dir: The design_path passed to BindCraft settings.

    Returns:
        List of candidate dicts with rank, pdb_path, and scores.
        May be empty if BindCraft filtered all candidates.
    """
    accepted_dir = os.path.join(output_dir, "Accepted")
    csv_path = os.path.join(output_dir, "final_design_stats.csv")

    # List everything BindCraft produced for debugging
    if os.path.isdir(output_dir):
        all_dirs = [d for d in os.listdir(output_dir) if os.path.isdir(os.path.join(output_dir, d))]
        all_csvs = glob(os.path.join(output_dir, "*.csv"))
        logger.info("BindCraft output dirs: %s", all_dirs)
        logger.info("BindCraft output CSVs: %s", [os.path.basename(c) for c in all_csvs])
    else:
        logger.warning("BindCraft output directory does not exist: %s", output_dir)
        return []

    if os.path.isdir(accepted_dir):
        all_files = os.listdir(accepted_dir)
        logger.info("BindCraft Accepted/ files: %s", all_files)
    else:
        logger.warning("BindCraft Accepted/ directory does not exist: %s", accepted_dir)
        return []

    # Collect PDB files from Accepted/ (ranked by BindCraft's internal scoring)
    pdb_files = sorted(glob(os.path.join(accepted_dir, "*.pdb")))
    logger.info("Found %d candidate PDB files", len(pdb_files))

    if not pdb_files:
        logger.info("BindCraft returned zero passing candidates (expected behavior)")
        return []

    # Parse metrics CSV if available.
    # BindCraft writes final_design_stats.csv with columns:
    #   Rank, Design, Length, ... Average_i_pTM, Average_pLDDT,
    #   Average_Binder_pAE, Average_Binder_RMSD, Average_Hotspot_RMSD, ...
    # The Design column value (e.g. "design_l100_s354445_mpnn2") is the MPNN
    # design name; the Accepted/*.pdb filename adds a "_modelN" suffix
    # (the top AF2 model picked for that design). We key by Design.
    metrics_by_name = {}
    csv_columns: list[str] = []
    if os.path.exists(csv_path):
        try:
            with open(csv_path) as fh:
                reader = csv.DictReader(fh)
                csv_columns = list(reader.fieldnames or [])
                logger.info("BindCraft CSV columns: %s", csv_columns)
                for row in reader:
                    name = row.get("Design", "") or row.get("design_name", "") or row.get("name", "")
                    if name:
                        metrics_by_name[name] = row
            logger.info("Parsed metrics for %d designs from CSV", len(metrics_by_name))
        except Exception as exc:
            logger.warning("Failed to parse results CSV: %s", exc)
    else:
        logger.warning("No final_design_stats.csv found at %s", csv_path)

    # Map from BindCraft CSV column -> canonical Kendrew score key.
    # We prefer the "Average_*" aggregate across AF2 models since Accepted/
    # PDBs correspond to the highest-scoring model but downstream display
    # uses the aggregate for ranking parity with other tools.
    #
    # The web service's bindcraft results template renders 5 columns:
    # ipTM, pLDDT, RMSD, shape_complementarity, SAP. Each must be
    # populated here or the table renders an em-dash. We accept multiple CSV
    # column names per canonical key because FreeBindCraft (cytokineking fork)
    # ships a different column layout than upstream BindCraft and naming has
    # drifted across versions — earlier dict entries lose to later ones, so
    # the most common name should appear last for each canonical key.
    _METRIC_MAP = {
        # ipTM
        "Average_i_pTM": "ipTM",
        # pLDDT
        "Average_pLDDT": "pLDDT",
        # pTM (overall)
        "Average_pTM": "pTM",
        # Interface pAE
        "Average_Binder_pAE": "i_pAE",
        "Average_i_pAE": "i_pAE",
        # Binder RMSD — template column header is "RMSD"; this is the headline
        # value (binder fold deviation from the AF2 redocked prediction).
        "Average_Binder_RMSD": "RMSD",
        # Hotspot / target RMSDs surface as secondary columns if requested.
        "Average_Hotspot_RMSD": "Hotspot_RMSD",
        "Average_Target_RMSD": "Target_RMSD",
        # Shape complementarity — accept prefixed + unprefixed across versions.
        "ShapeComplementarity": "shape_complementarity",
        "Average_Shape_Complementarity": "shape_complementarity",
        "Average_ShapeComplementarity": "shape_complementarity",
        # SAP (Spatial Aggregation Propensity) / surface hydrophobicity.
        # FreeBindCraft writes Average_Surface_Hydrophobicity by default;
        # legacy / alternate naming covered for forward compat.
        "HydrophobicityScore": "SAP",
        "Average_Binder_Surface_Hydrophobicity": "SAP",
        "Average_Surface_Hydrophobicity": "SAP",
    }

    candidates = []
    for rank_idx, pdb_path in enumerate(pdb_files):
        pdb_name = Path(pdb_path).stem  # e.g. "design_l100_s354445_mpnn2_model1"
        rank = rank_idx + 1

        # Strip the "_modelN" suffix to match the Design column.
        design_key = re.sub(r"_model\d+$", "", pdb_name)
        row = metrics_by_name.get(design_key, {})

        scores: dict[str, float | str] = {}
        for csv_col, canonical in _METRIC_MAP.items():
            if csv_col in row and row[csv_col] not in ("", None):
                try:
                    scores[canonical] = float(row[csv_col])
                except (ValueError, TypeError):
                    scores[canonical] = row[csv_col]

        if not scores and row:
            logger.warning(
                "Found CSV row for %s but no expected metric columns matched. Columns: %s",
                design_key, list(row.keys())[:10],
            )
        elif not row:
            logger.warning(
                "No CSV row matched design %s (looked up key=%s). Available: %s",
                pdb_name, design_key, list(metrics_by_name.keys()),
            )

        candidates.append({
            "rank": rank,
            "pdb_path": pdb_path,
            "pdb_name": pdb_name,
            "scores": scores,
        })

    logger.info("Parsed %d BindCraft candidates", len(candidates))
    return candidates


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full pipeline: diagnostics -> download -> BindCraft -> upload -> webhook."""
    startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))
    job_token = os.environ.get("JOB_TOKEN", "")

    job_payload = json.loads(job_payload_str)

    # Validate the payload against the shared contract module mounted at
    # /opt/contracts by the Modal image build. On import or validation
    # failure, write a preflight marker and exit non-zero so the wrapper
    # surfaces a clear contract error rather than a downstream KeyError.
    import sys
    sys.path.insert(0, "/opt")
    try:
        from contracts.rpc import ToolPayload
        _validated = ToolPayload.model_validate(job_payload)
    except Exception as _e:
        import time as _time
        print(f"[contract] payload validation failed: {_e}", flush=True)
        try:
            with open("/tmp/preflight_failure.json", "w") as _f:
                json.dump({"error": "payload_validation_failed", "detail": str(_e), "ts": _time.time()}, _f)
        except Exception:
            pass
        sys.exit(1)

    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    num_designs = job_spec.get("parameters", {}).get("num_designs", 10)
    job_tier = job_spec.get("job_tier", "pilot")
    pipeline_start = time.time()

    work_dir = tempfile.mkdtemp(prefix="bindcraft_job_")
    target_pdb = os.path.join(work_dir, "target.pdb")
    output_dir = os.path.join(work_dir, "outputs")

    try:
        # ----- Download input PDB -----
        download_input(input_url, target_pdb)
        send_heartbeat(webhook_url, job_id, "Input downloaded", 0, num_designs)

        # ----- Write BindCraft settings -----
        settings_path = write_bindcraft_settings(job_spec, target_pdb, output_dir)
        send_heartbeat(webhook_url, job_id, "Running BindCraft", 0, num_designs)

        # ----- Resolve advanced settings JSON -----
        # FreeBindCraft's default advanced JSON has max_trajectories=false
        # (unbounded). On hard targets a pilot can grind for 4h and hit the
        # Modal timeout with zero accepted designs. For pilots, copy the
        # default into work_dir and JSON-patch max_trajectories=10 so the
        # main loop exits cleanly after ~10 × 5 min = 50 min worst case.
        # Never mutate the default file on disk.
        advanced_path = BINDCRAFT_ADVANCED
        if job_tier == "pilot":
            advanced_path = os.path.join(work_dir, "advanced_pilot.json")
            with open(BINDCRAFT_ADVANCED) as fh:
                advanced_cfg = json.load(fh)
            advanced_cfg["max_trajectories"] = 10
            with open(advanced_path, "w") as fh:
                json.dump(advanced_cfg, fh, indent=2)
            logger.info(
                "Pilot tier: wrote patched advanced settings to %s (max_trajectories=10)",
                advanced_path,
            )

        # ----- Run FreeBindCraft -----
        # FreeBindCraft handles the full pipeline internally:
        #   1. Diffusion-based binder backbone generation
        #   2. Sequence design (ProteinMPNN)
        #   3. AF2 multimer validation (4-stage protocol)
        #   4. OpenMM relaxation (replaces PyRosetta FastRelax)
        #   5. Filtering and ranking
        # Cannot be parallelized on single GPU — one trajectory at a time.
        # Must run from BINDCRAFT_DIR so relative imports resolve.
        cmd = [
            sys.executable, "-u", BINDCRAFT_SCRIPT,
            "--settings", settings_path,
            "--filters", BINDCRAFT_FILTERS,
            "--advanced", advanced_path,
            "--no-pyrosetta",
            "--no-plots",
            "--no-animations",
        ]

        # Background heartbeat every 60s so Kendrew's stale-detection cron
        # doesn't kill a healthy job while BindCraft is inside its multi-minute
        # JAX compile + AF2 init phase (during which no stdout is emitted).
        with _HeartbeatThread(
            webhook_url, job_id,
            stage="Running BindCraft - 0/{} designs".format(num_designs),
            designs_completed=0, designs_total=num_designs,
            interval_seconds=60,
        ):
            run_command(cmd, timeout=14400, cwd=BINDCRAFT_DIR)

        send_heartbeat(webhook_url, job_id, "BindCraft complete, parsing results", 0, num_designs)

        # ----- Parse results -----
        candidates = parse_bindcraft_results(output_dir)

        logger.info(
            "BindCraft produced %d passing candidates (requested %d)",
            len(candidates), num_designs,
        )

        # ----- Upload outputs (on-demand URLs) -----
        filenames_to_upload = []
        for candidate in candidates:
            filenames_to_upload.append(f"design_{candidate['rank']:03d}.pdb")
        if candidates:
            filenames_to_upload.append("metrics.csv")

        # Also upload the BindCraft results CSV if it exists
        bindcraft_csv = os.path.join(output_dir, "final_design_stats.csv")
        if os.path.exists(bindcraft_csv) and candidates:
            filenames_to_upload.append("bindcraft_results.csv")

        upload_urls = {}
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(upload_endpoint, job_token, filenames_to_upload)
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)

        # Upload PDB files
        webhook_candidates = []
        for candidate in candidates:
            rank = candidate["rank"]
            pdb_path = candidate["pdb_path"]
            upload_filename = f"design_{rank:03d}.pdb"

            # pdb_key MUST share basename with upload_filename so the
            # web service's resolver finds the Storage object at
            # {user}/{job}/designs/<basename>. design_name / pdb_name
            # diverges from upload_filename and would 404 the resolver.
            # The contracts module (/opt/contracts/rpc.py) defines the
            # upload-URL exchange shape consumed by the web service.
            pdb_key = f"designs/{upload_filename}"
            webhook_candidate = {
                "rank": rank,
                "pdb_key": pdb_key,
                "scores": candidate["scores"],
            }
            # Inline base64 of the PDB so candidate_table.html can render
            # the 3D-viewer + PDB-download buttons (otherwise it falls
            # through to the em-dash branch). BindCraft writes PDBs
            # directly so no CIF conversion needed.
            if pdb_path and os.path.exists(pdb_path):
                try:
                    pdb_bytes = Path(pdb_path).read_bytes()
                    webhook_candidate["pdb_content_b64"] = base64.b64encode(
                        pdb_bytes,
                    ).decode("ascii")
                except OSError as exc:
                    logger.warning(
                        "Failed to read PDB for rank %d (%s): %s",
                        rank, pdb_path, exc,
                    )
            webhook_candidates.append(webhook_candidate)

            if upload_filename in upload_urls and os.path.exists(pdb_path):
                try:
                    upload_output(upload_urls[upload_filename], pdb_path)
                except RuntimeError as exc:
                    logger.warning("Failed to upload PDB for rank %d: %s", rank, exc)

        # Upload Kendrew-formatted metrics CSV
        if webhook_candidates:
            csv_path = os.path.join(work_dir, "metrics.csv")
            _write_metrics_csv(csv_path, webhook_candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], csv_path)
                except RuntimeError as exc:
                    logger.warning("Failed to upload metrics CSV: %s", exc)

        # Upload raw BindCraft results CSV
        if "bindcraft_results.csv" in upload_urls and os.path.exists(bindcraft_csv):
            try:
                upload_output(upload_urls["bindcraft_results.csv"], bindcraft_csv)
            except RuntimeError as exc:
                logger.warning("Failed to upload BindCraft results CSV: %s", exc)

        elapsed_minutes = (time.time() - pipeline_start) / 60.0
        logger.info(
            "Pipeline complete: %d candidates in %.1f minutes",
            len(webhook_candidates), elapsed_minutes,
        )

        # ----- POST results to webhook -----
        # Pass webhook_candidates through unchanged so pdb_content_b64
        # survives to the frontend; the earlier list-comprehension rebuild
        # was stripping it and falling back to em-dashes in the 3D / PDB
        # columns of candidate_table.html.
        result_payload = {
            "candidates": webhook_candidates,
            "candidate_count": len(webhook_candidates),
            "total_designs_requested": num_designs,
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
        # Catch-all: guarantee a FAILED webhook for ANY unhandled error
        # (bad payload JSON, download failure, upload failure, etc.)
        # Without this, the backend never learns the job died — zombie jobs.
        logger.error("Pipeline failed: %s", exc)
        post_webhook(webhook_url, job_id, pod_id, {
            "error": f"Pipeline failed: {exc}",
        })

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a metrics CSV summarizing all passing candidates.

    Args:
        csv_path: Path to write the CSV file.
        candidates: List of candidate dicts with rank, pdb_key, and scores.
    """
    # Collect all score keys across candidates for CSV header
    all_score_keys = set()
    for candidate in candidates:
        all_score_keys.update(candidate.get("scores", {}).keys())
    score_columns = sorted(all_score_keys)

    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "design_name"] + score_columns)
        for candidate in candidates:
            design_name = Path(candidate["pdb_key"]).stem
            scores = candidate.get("scores", {})
            row = [candidate["rank"], design_name]
            for col in score_columns:
                row.append(scores.get(col, ""))
            writer.writerow(row)

    logger.info("Wrote metrics CSV with %d candidates to %s", len(candidates), csv_path)


if __name__ == "__main__":
    main()
