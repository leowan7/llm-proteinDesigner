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

Tiers:
    Absent / anything else   the webhook pipeline above (production).
    "smoke" / "mini_pilot"   bypass the webhook and upload entirely; write the
                             results to /tmp/smoke_results.json, which
                             infrastructure/modal/bindcraft_app.py returns
                             inline as ``smoke_result``. This is the only way
                             a caller invoking the Modal function directly
                             (``modal.Function.from_name(...)``) gets anything
                             back. See docs/SMOKE-TEST-SPEC.md and
                             ``run_smoke_tier`` below.
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
import tarfile
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

BINDCRAFT_FILTERS_DIR = f"{BINDCRAFT_DIR}/settings_filters"
BINDCRAFT_ADVANCED_DIR = f"{BINDCRAFT_DIR}/settings_advanced"

# ---------------------------------------------------------------------------
# Raw output capture — see archive_work_dir()
# ---------------------------------------------------------------------------
# Fixed handoff path, mirroring /tmp/preflight_failure.json: this script runs as
# a subprocess and cannot mount the Modal Volume itself, so it drops the archive
# here and infrastructure/modal/bindcraft_app.py::_ship_raw_archive moves it onto
# the volume after we exit. Must be OUTSIDE any work_dir we archive.
RAW_ARCHIVE_PATH = "/tmp/raw_archive.tgz"

# ---------------------------------------------------------------------------
# Smoke / mini_pilot tier — see docs/SMOKE-TEST-SPEC.md
# ---------------------------------------------------------------------------
# infrastructure/modal/bindcraft_app.py::run_tool reads this file after we exit
# and returns its contents as ``smoke_result``. It has advertised that contract
# since the tier work landed for the other four tools; BindCraft was the
# reference implementation those four copied, was never in scope itself, and so
# got the reader without ever getting the writer. Everything below is the
# missing writer. The single hard rule: whatever happens, this file gets
# written, because a tool that cannot report its own failure returns None and
# tells the caller nothing.
SMOKE_RESULTS_PATH = "/tmp/smoke_results.json"
SMOKE_TIERS = ("smoke", "mini_pilot")

# THE cost bound. BindCraft's own ``max_trajectories`` is not sufficient:
# ``generic_utils.check_n_trajectories`` counts PDBs in ``Trajectory/Relaxed``,
# and ``colabdesign_utils.binder_hallucination`` moves a trajectory to
# ``Trajectory/Clashing`` or ``Trajectory/LowConfidence`` (CA clash, final
# pLDDT < 0.7, or fewer than 3 interface contacts) *instead of* relaxing it —
# so a terminated trajectory never increments the counter. On a hard epitope
# where most trajectories terminate, ``max_trajectories`` alone lets the design
# loop run until the 4 h session cap. A wall-clock cap on the subprocess is the
# only bound that holds unconditionally, so it is the one the budget rests on.
# On expiry we do NOT discard the run: BindCraft writes Accepted/*.pdb
# incrementally, so partial output is parsed and returned.
SMOKE_SUBPROCESS_TIMEOUT_S = 2400       # 40 min
MINI_PILOT_SUBPROCESS_TIMEOUT_S = 6000  # 100 min


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def _payload_tier() -> str:
    """Read ``tier`` out of JOB_PAYLOAD without raising.

    Called from ``startup_check`` before the payload is properly parsed, so it
    must never be the thing that kills the run. An unreadable payload reports
    the empty tier, which routes to the legacy webhook path and its existing
    error handling.
    """
    try:
        return str(json.loads(os.environ.get("JOB_PAYLOAD") or "{}").get("tier") or "")
    except (ValueError, TypeError, AttributeError):
        return ""


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
    # WEBHOOK_URL and JOB_ID exist to POST results back to Kendrew. The smoke
    # tier posts nothing — it returns inline through /tmp/smoke_results.json —
    # so demanding a webhook URL there would reject the very call shape the
    # tier is for. The legacy tier's required list is unchanged: _payload_tier()
    # returns "" for every non-smoke payload, including a malformed one.
    required = ["JOB_PAYLOAD"]
    if _payload_tier() not in SMOKE_TIERS:
        required += ["WEBHOOK_URL", "JOB_ID"]
    missing = [var for var in required if not os.environ.get(var)]
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
    new_candidate: dict | None = None,
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
        new_candidate: Optional per-design candidate for live UI streaming.
            tools-hub gates it server-side via JOB_TOKEN and projects it to
            a fixed schema, so a malformed candidate is dropped silently.
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
        if isinstance(new_candidate, dict):
            body["new_candidate"] = new_candidate
            body["job_token"] = os.environ.get("JOB_TOKEN", "")
    except Exception as exc:
        logger.debug("Skipping new_candidate on heartbeat: %s", exc)
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

        # BindCraft writes only Accepted designs to its output; everything
        # in pdb_files is already past BindCraft's internal filter. Stamp
        # filter_status explicitly so the field is always present for the
        # tools-hub UI.
        scores["filter_status"] = "pass"

        candidates.append({
            "rank": rank,
            "pdb_path": pdb_path,
            "pdb_name": pdb_name,
            "scores": scores,
        })

    logger.info("Parsed %d BindCraft candidates", len(candidates))
    return candidates


# ===========================================================================
# Smoke / mini_pilot tier — Layer 2 preflight + Layer 3 execution
# See docs/SMOKE-TEST-SPEC.md. Reference implementation:
# docker/rfdiffusion/run_pipeline.py::run_smoke_tier.
# ===========================================================================

def write_smoke_results(payload: dict) -> None:
    """Write ``payload`` to SMOKE_RESULTS_PATH. Best-effort: never raises.

    Every exit path in the smoke tier funnels through here. The wrapper opens
    this file unconditionally after the subprocess exits, so an absent file is
    indistinguishable from a crash — which is precisely the failure this tier
    exists to eliminate.
    """
    try:
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            json.dump(payload, fh)
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Failed to write %s: %s", SMOKE_RESULTS_PATH, exc)


def _smoke_failure(bucket: str, check: str, detail: str, tier: str = "") -> dict:
    """Build a Layer 2 / Layer 3 structured failure dict."""
    return {
        "status": "FAILED",
        "error": {"bucket": bucket, "check": check, "detail": str(detail)[:2000]},
        "output": {"candidates": []},
        "tier": tier,
    }


def _normalize_target_chains(value) -> str:
    """Return a comma-joined chain selector from any accepted spelling.

    ``docs/MULTI-CHAIN-TARGETS.md`` accepts ``"A,B"``, ``"A B"`` and
    ``"A, B"`` interchangeably; BindCraft's own settings key wants the comma
    form (``bindcraft.py`` prompts "Enter target chains (e.g., A or A,B)").
    Order is significant and preserved; duplicates are dropped.

    Deliberately NOT applied to ``write_bindcraft_settings``: that function is
    on the legacy webhook path, which must stay byte-identical, and the comma
    form it already receives passes through unchanged either way.
    """
    if isinstance(value, (list, tuple)):
        tokens = [str(v) for v in value]
    else:
        tokens = re.split(r"[,\s]+", str(value if value is not None else ""))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)
    return ",".join(ordered)


def _build_smoke_job_spec(tier: str, overrides: dict | None = None) -> dict:
    """Build the job_spec for a smoke/mini_pilot run.

    Mirrors ``backend/pipelines/bindcraft.py::smoke_preset`` /
    ``mini_pilot_preset``. Kept in sync by hand because this script ships
    inside the Docker image and cannot import the backend package — the same
    split the other four tools live with.

    ``overrides`` is the caller's ``job_spec``. ``target_chain``,
    ``hotspot_residues`` and ``parameters.binder_length`` are honoured so a
    real multi-chain target can be smoked; everything else is pinned by the
    preset, because the preset is what bounds the cost.
    """
    if tier == "smoke":
        parameters = {
            "num_designs": 1,
            "max_trajectories": 2,
            "binder_length": {"min": 55, "max": 65},
            "filter_set": "no_filters.json",
            "advanced_overrides": {
                "max_trajectories": 2,
                "soft_iterations": 40,
                "temporary_iterations": 25,
                "hard_iterations": 3,
                "greedy_iterations": 5,
                "num_seqs": 8,
                "max_mpnn_sequences": 1,
                "num_recycles_validation": 1,
                "optimise_beta": False,
                "save_design_animations": False,
                "save_design_trajectory_plots": False,
                "zip_animations": False,
                "zip_plots": False,
            },
        }
    elif tier == "mini_pilot":
        parameters = {
            "num_designs": 2,
            "max_trajectories": 5,
            "binder_length": {"min": 55, "max": 65},
            "filter_set": "default_filters.json",
            "advanced_overrides": {
                "max_trajectories": 5,
                "save_design_animations": False,
                "save_design_trajectory_plots": False,
                "zip_animations": False,
                "zip_plots": False,
            },
        }
    else:
        raise ValueError(f"Unknown tier: {tier}")

    overrides = overrides or {}
    target_chain = _normalize_target_chains(overrides.get("target_chain") or "A")

    # Chain-prefixed tokens ("A241") and bare ints (241) both pass straight
    # through to BindCraft, whose documented hotspot format is exactly this
    # ("A1,B20-25"). Only whitespace is stripped — residue numbers stay in the
    # uploaded structure's author numbering, never pre-converted.
    hotspots = [
        str(res).strip()
        for res in (overrides.get("hotspot_residues") or [])
        if str(res).strip()
    ]

    caller_params = overrides.get("parameters") or {}
    if caller_params.get("binder_length"):
        parameters["binder_length"] = caller_params["binder_length"]

    return {
        "tool": "bindcraft",
        "target_chain": target_chain,
        "hotspot_residues": hotspots,
        "parameters": parameters,
    }


def _resolve_filter_set(job_spec: dict) -> tuple[str, dict]:
    """Resolve the preset's filter file inside the image, with a report.

    FreeBindCraft is cloned unpinned at image build, so the set of files under
    ``settings_filters/`` is not something this script can assume. A missing
    file falls back to the baked default rather than crashing, and the report
    says which happened — a silent fallback would turn "no filters, guaranteed
    acceptance" into "production filters" and make a zero-design smoke run look
    like a pipeline defect.
    """
    requested = job_spec.get("parameters", {}).get("filter_set") or "default_filters.json"
    candidate = os.path.join(BINDCRAFT_FILTERS_DIR, os.path.basename(requested))
    try:
        available = sorted(os.listdir(BINDCRAFT_FILTERS_DIR))
    except OSError:
        available = []
    if os.path.isfile(candidate):
        return candidate, {"requested": requested, "resolved": candidate,
                           "fallback": False, "available": available}
    logger.error(
        "Filter set %s not found in %s (have: %s) — falling back to %s",
        requested, BINDCRAFT_FILTERS_DIR, available, BINDCRAFT_FILTERS,
    )
    return BINDCRAFT_FILTERS, {"requested": requested, "resolved": BINDCRAFT_FILTERS,
                               "fallback": True, "available": available}


def _write_smoke_advanced(work_dir: str, job_spec: dict) -> tuple[str, dict]:
    """Copy the default advanced settings and JSON-patch the preset's overrides.

    Same mechanism the pilot tier already uses for ``max_trajectories`` (see
    main()) — never mutate the default file on disk, always patch a copy in
    the work dir.

    Only keys that already exist in the default are patched. FreeBindCraft is
    cloned unpinned, so an upstream rename would otherwise turn a cost bound
    into a no-op that nothing reports; ``unknown`` in the returned report is
    the alarm for that, and ``preflight`` refuses to start if the key the
    budget depends on is among them.
    """
    with open(BINDCRAFT_ADVANCED) as fh:
        advanced_cfg = json.load(fh)

    overrides = job_spec.get("parameters", {}).get("advanced_overrides") or {}
    applied: dict = {}
    unknown: dict = {}
    for key, value in overrides.items():
        if key in advanced_cfg:
            applied[key] = {"from": advanced_cfg[key], "to": value}
            advanced_cfg[key] = value
        else:
            unknown[key] = value

    advanced_path = os.path.join(work_dir, "advanced_smoke.json")
    with open(advanced_path, "w") as fh:
        json.dump(advanced_cfg, fh, indent=2)

    if unknown:
        logger.error(
            "Advanced-settings keys absent from %s and therefore NOT applied: %s",
            BINDCRAFT_ADVANCED, sorted(unknown),
        )
    logger.info(
        "Smoke tier: wrote patched advanced settings to %s (%d applied, %d unknown)",
        advanced_path, len(applied), len(unknown),
    )
    return advanced_path, {"path": advanced_path, "applied": applied, "unknown": unknown}


def _freebindcraft_version() -> dict:
    """Record which FreeBindCraft the image actually contains.

    Dockerfile.modal clones FreeBindCraft unpinned (``git clone --depth 1``),
    so the upstream version moves whenever Modal's image cache misses and the
    image is rebuilt — silently, and with no record of what changed. Stamping
    the commit into every smoke result makes "the tool started behaving
    differently" a diffable fact rather than a hypothesis. Best-effort: a
    missing .git must never fail a run.
    """
    info: dict = {"dir": BINDCRAFT_DIR}
    try:
        proc = subprocess.run(
            ["git", "-C", BINDCRAFT_DIR, "log", "-1", "--format=%H %cI"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            info["commit"], _, info["committed_at"] = proc.stdout.strip().partition(" ")
        else:
            info["error"] = (proc.stderr or "").strip()[:200]
    except (OSError, subprocess.SubprocessError) as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _interface_census(pdb_path: str, cutoff: float = 5.0) -> dict:
    """Is the returned complex actually in contact, or has the binder floated off?

    Observed on the first real multi-chain smoke run and the reason this exists:
    BindCraft picks which AF2 model to accept by highest binder pLDDT
    (``bindcraft.py``, ``highest_plddt_key = max(plddt_values, ...)``). A
    dissociated pose scores *higher* pLDDT than a bound one — nothing is
    strained — so with no_filters the accepted structure was model2 at
    10.2 A from the target, while model1 of the same sequence was bound at
    1.7 A. The i_pTM (0.13 vs 0.17) and the empty InterfaceResidues column
    both said so, but only in a CSV that dies with the container.

    Production filters reject this outright, so the pilot path never had the
    problem; the smoke tier disables filters on purpose and therefore has to
    report the consequence itself. Best-effort — never raises.
    """
    try:
        import numpy as np
        by_chain: dict[str, list] = {}
        with open(pdb_path) as fh:
            for line in fh:
                if line.startswith("ATOM"):
                    by_chain.setdefault(line[21], []).append(
                        (float(line[30:38]), float(line[38:46]), float(line[46:54])),
                    )
        if len(by_chain) < 2:
            return {"chains": {c: len(v) for c, v in by_chain.items()},
                    "note": "single chain; no interface to measure"}
        coords = {c: np.asarray(v, dtype=np.float32) for c, v in by_chain.items()}
        # ColabDesign merges every target chain into the first chain and appends
        # the hallucinated binder, so the binder is the smallest chain. Chosen by
        # size rather than by id so this holds however upstream labels them.
        binder = min(coords, key=lambda c: len(coords[c]))
        target = np.concatenate([v for c, v in coords.items() if c != binder])
        best, pairs = float("inf"), 0
        for i in range(0, len(target), 2048):
            d = np.linalg.norm(
                target[i:i + 2048, None, :] - coords[binder][None, :, :], axis=-1,
            )
            best = min(best, float(d.min()))
            pairs += int((d < cutoff).sum())
        return {
            "chains": {c: len(v) for c, v in coords.items()},
            "binder_chain": binder,
            "binder_atoms": len(coords[binder]),
            "min_distance_angstrom": round(best, 2),
            "atom_pairs_within_cutoff": pairs,
            "cutoff_angstrom": cutoff,
            "in_contact": pairs > 0,
        }
    except Exception as exc:  # noqa: BLE001 — a diagnostic must not fail a run
        return {"error": f"{type(exc).__name__}: {exc}"}


def _census_output_tree(output_dir: str) -> dict:
    """Count what BindCraft actually produced, per directory.

    A zero-candidate run is only interpretable next to this: 3 trajectories all
    in ``Trajectory/Clashing`` is a target/hotspot problem, 3 in
    ``Trajectory/Relaxed`` with an empty ``Accepted/`` is a filter problem, and
    an empty tree is a tool-invocation problem. Without the census all three
    look identical from outside.
    """
    census: dict = {}
    for sub in ("", "Accepted", "Accepted/Ranked", "Trajectory", "Trajectory/Relaxed",
                "Trajectory/LowConfidence", "Trajectory/Clashing", "MPNN", "MPNN/Relaxed"):
        path = os.path.join(output_dir, sub) if sub else output_dir
        if os.path.isdir(path):
            census[sub or "."] = len(
                [f for f in os.listdir(path) if f.endswith(".pdb")]
            )
    try:
        census["csvs"] = sorted(
            os.path.basename(p) for p in glob(os.path.join(output_dir, "*.csv"))
        )
    except OSError:
        pass
    return census


def _best_effort_structure(output_dir: str) -> dict | None:
    """Return the best available NON-accepted structure, base64-encoded.

    A run that accepts nothing has still paid for a GPU and still produced
    complexes; shipping the best of them inline is what makes a zero-design
    result diagnosable (is the binder a real second chain? does it touch the
    hotspots?) instead of merely disappointing. Deliberately kept OUT of
    ``output.candidates`` — these did not pass BindCraft's filters and must
    never be mistaken for designs that did.
    """
    for sub in ("MPNN/Relaxed", "MPNN", "Trajectory/Relaxed", "Trajectory",
                "Trajectory/LowConfidence", "Trajectory/Clashing"):
        hits = sorted(glob(os.path.join(output_dir, sub, "*.pdb")))
        if hits:
            try:
                return {
                    "source": sub,
                    "pdb_key": os.path.basename(hits[0]),
                    "pdb_content_b64": base64.b64encode(
                        Path(hits[0]).read_bytes(),
                    ).decode("ascii"),
                    "siblings": len(hits),
                }
            except OSError as exc:
                logger.warning("Could not read best-effort PDB %s: %s", hits[0], exc)
    return None


def preflight(payload: dict) -> None:
    """Layer 2 fail-fast checks. Writes a structured failure and exits on any.

    A no-op for the legacy webhook tier, exactly like the RFdiffusion
    reference. See docs/SMOKE-TEST-SPEC.md "Layer 2".
    """
    tier = payload.get("tier", "")
    if tier not in SMOKE_TIERS:
        return

    logger.info("=== Preflight (tier=%s) ===", tier)

    # 1. A caller-supplied target. Unlike the other four tools this image bakes
    #    NO /opt/smoke_target.pdb fixture, and adding one would mean editing
    #    Dockerfile.modal — which reclones FreeBindCraft unpinned and would move
    #    the upstream version underneath us. Requiring the URL is also simply
    #    correct: falling back to a stand-in target while reporting COMPLETED is
    #    the single worst failure this tier could have.
    if not (payload.get("input_pdb_url") or payload.get("input_presigned_url")):
        write_smoke_results(_smoke_failure(
            "preflight", "input_url",
            "no target supplied: set input_pdb_url or input_presigned_url. "
            "This image bakes no smoke fixture, so there is nothing to fall back to.",
            tier,
        ))
        sys.exit(1)

    # 2. BindCraft's own files.
    for check, path in (("bindcraft_script", BINDCRAFT_SCRIPT),
                        ("bindcraft_advanced", BINDCRAFT_ADVANCED),
                        ("bindcraft_filters_dir", BINDCRAFT_FILTERS_DIR)):
        if not os.path.exists(path):
            write_smoke_results(_smoke_failure(
                "preflight", check, f"not found at {path}", tier))
            sys.exit(1)

    # 3. The cost bound must be real. ``max_trajectories`` is the key the whole
    #    budget rests on; if an unpinned FreeBindCraft ever renames it, patching
    #    it silently becomes a no-op and the run is unbounded. Refuse to start.
    try:
        with open(BINDCRAFT_ADVANCED) as fh:
            advanced_cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        write_smoke_results(_smoke_failure(
            "preflight", "advanced_settings_parse", str(exc), tier))
        sys.exit(1)
    if "max_trajectories" not in advanced_cfg:
        write_smoke_results(_smoke_failure(
            "preflight", "max_trajectories",
            f"key absent from {BINDCRAFT_ADVANCED}; the trajectory cap would "
            f"be a silent no-op and the run unbounded. Keys: "
            f"{sorted(advanced_cfg)[:40]}",
            tier,
        ))
        sys.exit(1)

    # 4. GPU. FreeBindCraft is JAX, not torch.
    try:
        import jax
        if not jax.devices("gpu"):
            write_smoke_results(_smoke_failure(
                "preflight", "gpu", "jax.devices('gpu') is empty", tier))
            sys.exit(1)
        logger.info("GPU: %s", jax.devices("gpu")[0])
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — any JAX failure is a hard stop
        write_smoke_results(_smoke_failure("preflight", "jax_import", str(exc), tier))
        sys.exit(1)

    # 5. SMOKE_RESULTS_PATH is writable — checked last so the checks above can
    #    still report through it.
    try:
        with open(SMOKE_RESULTS_PATH, "a"):
            pass
    except OSError as exc:
        logger.error("Preflight: %s not writable: %s", SMOKE_RESULTS_PATH, exc)
        sys.exit(1)

    logger.info("Preflight: OK (tier=%s)", tier)


def run_smoke_tier(
    tier: str,
    work_dir: str,
    job_spec_override: dict | None = None,
    input_url: str = "",
) -> dict:
    """Run BindCraft for smoke/mini_pilot and return a Layer 3 result dict.

    Returns rather than raises on every foreseeable failure, because the return
    value is the only channel this tier has — see docs/SMOKE-TEST-SPEC.md
    Layer 3 "output shape".

    BindCraft has no stage to stub: it hallucinates, redesigns with MPNN and
    AF2-validates inside one subprocess. So unlike the RFdiffusion reference
    there is no ``skip_af2`` here, and the tier differences are entirely about
    how much work BindCraft is allowed to do before it must stop.
    """
    start = time.time()
    diagnostics: dict = {}

    def _finish(status: str, **extra) -> dict:
        result = {
            "status": status,
            "output": {"candidates": []},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
            "diagnostics": diagnostics,
        }
        result.update(extra)
        return result

    job_spec = _build_smoke_job_spec(tier, job_spec_override)
    params = job_spec["parameters"]
    num_designs = int(params.get("num_designs", 1))
    diagnostics["job_spec"] = job_spec
    diagnostics["freebindcraft"] = _freebindcraft_version()

    # ----- Resolve the target -----
    # Both key spellings are accepted: rfdiffusion and pxdesign read
    # ``input_pdb_url`` on the smoke path, the legacy BindCraft path reads
    # ``input_presigned_url``, and the wrapper forwards both. Accepting only
    # one of them would reject half the callers already in the field.
    target_pdb = os.path.join(work_dir, "target.pdb")
    try:
        download_input(input_url, target_pdb)
    except Exception as exc:  # noqa: BLE001 — network/HTTP failure is a clean report
        return _finish("FAILED", error={
            "bucket": "preflight", "check": "target_download",
            "detail": str(exc)[:2000]})
    diagnostics["target_bytes"] = os.path.getsize(target_pdb)

    # ----- Settings -----
    # Reuses write_bindcraft_settings, the same function the pilot path uses,
    # so the settings shape cannot drift between tiers. It passes ``chains``
    # through verbatim and joins hotspots with "," — which is already exactly
    # BindCraft's multi-chain contract, so chain-prefixed tokens survive.
    output_dir = os.path.join(work_dir, "outputs")
    settings_path = write_bindcraft_settings(job_spec, target_pdb, output_dir)
    try:
        with open(settings_path) as fh:
            diagnostics["bindcraft_settings"] = json.load(fh)
    except (OSError, ValueError):
        pass

    advanced_path, advanced_report = _write_smoke_advanced(work_dir, job_spec)
    diagnostics["advanced"] = advanced_report
    filters_path, filter_report = _resolve_filter_set(job_spec)
    diagnostics["filters"] = filter_report

    # ----- Run BindCraft -----
    cmd = [
        sys.executable, "-u", BINDCRAFT_SCRIPT,
        "--settings", settings_path,
        "--filters", filters_path,
        "--advanced", advanced_path,
        "--no-pyrosetta",
        "--no-plots",
        "--no-animations",
    ]
    timeout_s = (SMOKE_SUBPROCESS_TIMEOUT_S if tier == "smoke"
                 else MINI_PILOT_SUBPROCESS_TIMEOUT_S)

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    run_status, run_detail = "ok", ""
    try:
        if webhook_url:
            # Only when a webhook exists. A direct modal.Function caller has no
            # URL, and an unguarded heartbeat thread would POST to "" every 60 s.
            with _HeartbeatThread(
                webhook_url, job_id, stage=f"BindCraft ({tier})",
                designs_completed=0, designs_total=num_designs,
                interval_seconds=60,
            ):
                run_command(cmd, timeout=timeout_s, cwd=BINDCRAFT_DIR)
        else:
            run_command(cmd, timeout=timeout_s, cwd=BINDCRAFT_DIR)
    except subprocess.TimeoutExpired as exc:
        run_status, run_detail = "timeout", f"exceeded {timeout_s}s: {exc}"
        logger.error("BindCraft hit the %ss wall-clock cap; parsing partial output",
                     timeout_s)
    except RuntimeError as exc:
        run_status, run_detail = "nonzero_exit", str(exc)
        logger.error("BindCraft exited non-zero; parsing whatever it wrote: %s", exc)
    diagnostics["bindcraft_run"] = {
        "status": run_status, "timeout_s": timeout_s, "detail": run_detail[:2000],
    }

    # ----- Parse -----
    # Reached on EVERY path above, including the timeout and the crash, because
    # BindCraft writes Accepted/*.pdb incrementally: a run that died at minute
    # 39 may still have banked a design, and throwing it away would mean paying
    # for the GPU twice.
    diagnostics["output_tree"] = _census_output_tree(output_dir)
    parsed = parse_bindcraft_results(output_dir)

    candidates = []
    for candidate in parsed[:num_designs]:
        entry = {
            "rank": candidate["rank"],
            "pdb_key": f"design_{candidate['rank']:03d}.pdb",
            "scores": candidate["scores"],
            "bindcraft_design_name": candidate["pdb_name"],
            # Whether this pose is actually touching the target. Only meaningful
            # to state because smoke can run with filters off — see
            # _interface_census.
            "interface": _interface_census(candidate["pdb_path"]),
        }
        try:
            entry["pdb_content_b64"] = base64.b64encode(
                Path(candidate["pdb_path"]).read_bytes(),
            ).decode("ascii")
        except OSError as exc:
            logger.warning("Could not read %s: %s", candidate["pdb_path"], exc)
        candidates.append(entry)

    if not candidates:
        diagnostics["best_effort_structure"] = _best_effort_structure(output_dir)
        bucket = "output-parse" if run_status == "ok" else "tool-invocation"
        return _finish("FAILED", accepted_count=0, error={
            "bucket": bucket,
            "check": "zero_accepted",
            "detail": (
                f"BindCraft accepted 0 designs (subprocess status={run_status}). "
                f"Output tree: {diagnostics['output_tree']}. "
                f"{run_detail[:600]}"
            ),
        })

    logger.info("Smoke tier %s: %d accepted design(s)", tier, len(candidates))
    return _finish(
        "COMPLETED",
        output={"candidates": candidates},
        accepted_count=len(candidates),
        partial=(run_status != "ok"),
    )


# ===========================================================================
# Raw output capture
# ===========================================================================

def archive_work_dir(work_dir: str, dest: str = RAW_ARCHIVE_PATH) -> None:
    """Tar the COMPLETE work dir to ``dest``. Best-effort: never raises.

    A container must not decide which fields are worth keeping. parse_bindcraft_results
    above keeps only Accepted/*.pdb plus the handful of ``Average_*`` columns named in
    _METRIC_MAP out of final_design_stats.csv; the rejected designs, the trajectory
    tree, the per-trajectory stats, the MPNN intermediates and every unmapped column
    die with the container, recoverable only by paying for the GPU again. That is how
    ``design_iptm`` (the real binder->target interface) was lost behind ``iptm``
    (averaged over every chain pair) on 460 designs across two campaigns. Decide
    LOCALLY, where re-parsing is free.

    This is deliberately NOT gated on candidates, on exit status, or on
    filenames_to_upload. parse_bindcraft_results returns [] outright when Accepted/ is
    missing, which turns a dead run into a silent zero-candidate "success" that
    requests no upload URLs and ships nothing — exactly the run whose tree you need.
    Likewise a crash. Hence the call site is the ``finally``, immediately before the
    rmtree.

    Failure to archive must never break the run: a job that died before writing output
    is when diagnostics matter most, so problems are logged, not raised.
    """
    stage_dir = None
    try:
        if not os.path.isdir(work_dir):
            logger.warning(
                "Raw capture: work dir %s does not exist; nothing to archive",
                work_dir,
            )
            return

        # The tar must never be written inside the tree it archives, or it tars
        # itself. dest defaults to /tmp/raw_archive.tgz and work_dir is a
        # /tmp/bindcraft_job_*/ mkdtemp, so this cannot trip today — but the guard
        # is cheap and a caller passing a dest under work_dir would otherwise
        # produce a corrupt archive silently.
        work_abs = os.path.abspath(work_dir)
        dest_abs = os.path.abspath(dest)
        if dest_abs == work_abs or dest_abs.startswith(work_abs + os.sep):
            logger.error(
                "Raw capture: refusing to write archive %s inside the tree it "
                "archives (%s)", dest_abs, work_abs,
            )
            return

        # Stage in a FRESH mkdtemp (which cannot be inside an already-existing
        # work_dir) and move into place, so dest is never a half-written tar.
        # Stream to a file rather than io.BytesIO: ~1x peak RSS instead of ~3-4x,
        # which matters on a multi-hundred-MB BindCraft trajectory tree.
        stage_dir = tempfile.mkdtemp(prefix="rawtar_")
        staged = os.path.join(stage_dir, "raw_archive.tgz")
        with tarfile.open(staged, "w:gz") as tf:
            tf.add(work_abs, arcname=os.path.basename(work_abs.rstrip(os.sep)) or "work")
        shutil.move(staged, dest_abs)
        logger.info(
            "Raw capture: archived %s -> %s (%d bytes)",
            work_abs, dest_abs, os.path.getsize(dest_abs),
        )
    except Exception as exc:  # noqa: BLE001 — capture is best-effort by design
        logger.warning(
            "Raw capture failed (non-fatal): %s: %s", type(exc).__name__, exc,
        )
    finally:
        if stage_dir and os.path.isdir(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full pipeline: diagnostics -> download -> BindCraft -> upload -> webhook."""
    # Modal reuses warm containers, and the wrapper reads SMOKE_RESULTS_PATH
    # unconditionally — so a file left by a previous call in this container
    # would be returned as THIS call's smoke_result. It clears the raw archive
    # for exactly this reason but not this path. Two lines here close it for
    # every tier without touching the wrapper.
    _tier_at_boot = _payload_tier()
    try:
        os.remove(SMOKE_RESULTS_PATH)
    except OSError:
        pass
    if _tier_at_boot in SMOKE_TIERS:
        # Placeholder written BEFORE startup_check, which sys.exit(1)s on a
        # missing GPU. Anything that kills the process from here on still
        # leaves the caller a well-formed explanation instead of a None.
        write_smoke_results(_smoke_failure(
            "startup", "did_not_complete",
            "run_pipeline.py exited before the smoke tier produced a result; "
            "see the Modal function logs for the failing stage.",
            _tier_at_boot,
        ))

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

    # ---- Smoke / mini_pilot tier: bypass webhook + upload entirely and write
    #      /tmp/smoke_results.json, which the Modal wrapper returns inline as
    #      ``smoke_result``. See docs/SMOKE-TEST-SPEC.md. ----
    tier = job_payload.get("tier", "")
    if tier in SMOKE_TIERS:
        preflight(job_payload)
        smoke_work_dir = tempfile.mkdtemp(prefix="bindcraft_smoke_")
        try:
            result = run_smoke_tier(
                tier, smoke_work_dir,
                job_spec_override=job_payload.get("job_spec") or {},
                # Both spellings, caller's choice — see run_smoke_tier.
                input_url=(job_payload.get("input_pdb_url")
                           or job_payload.get("input_presigned_url") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — the report is the deliverable
            logger.exception("Smoke tier raised")
            result = _smoke_failure(
                "tool-invocation", "unhandled_exception",
                f"{type(exc).__name__}: {exc}", tier)
        finally:
            # Archive before the rmtree, same contract as the legacy path: the
            # tree of a run that accepted nothing is the tree you most need.
            archive_work_dir(smoke_work_dir)
            shutil.rmtree(smoke_work_dir, ignore_errors=True)

        write_smoke_results(result)
        logger.info(
            "Smoke tier %s: status=%s accepted=%s gpu_seconds=%s",
            tier, result.get("status"), result.get("accepted_count"),
            result.get("gpu_seconds"),
        )
        if result.get("status") != "COMPLETED":
            sys.exit(1)
        return

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
        url_exchange_error = None
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(upload_endpoint, job_token, filenames_to_upload)
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)
                url_exchange_error = str(exc)

        failed_uploads: list[str] = []
        if filenames_to_upload and not upload_urls:
            # The URL exchange yielded nothing, so every `if upload_filename in upload_urls` below is
            # False: nothing uploads, work_dir is rmtree'd in the finally, and the job STILL posts a
            # success webhook. An entire multi-hour GPU run disappears while the UI says COMPLETED.
            # failed_uploads is surfaced to tools-hub via result_payload, where
            # _slim_result_for_persist KEEPS the inline b64 structures for any listed design rather
            # than dropping them as "already in Storage". Telling it the truth is enough - the bug
            # was only the silence.
            logger.error(
                "Upload URL exchange yielded no URLs (%s); marking all %d artifact(s) as failed "
                "so the run is not reported as a clean success",
                url_exchange_error or "empty response", len(filenames_to_upload),
            )
            failed_uploads.extend(filenames_to_upload)

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
                    failed_uploads.append(upload_filename)

            # Emit per-candidate heartbeat for live UI streaming. BindCraft
            # only writes designs that it has already accepted, so default
            # filter_status to "pass" unless a score row tagged otherwise.
            # Score keys are canonical (ipTM, pLDDT, i_pAE) per _METRIC_MAP.
            # pdb_key is included because the candidate's PDB upload has
            # been attempted just above and the basename matches the
            # tools-hub resolver path.
            try:
                scores_d = candidate.get("scores", {}) or {}
                iptm_v = scores_d.get("ipTM", scores_d.get("iptm"))
                plddt_v = scores_d.get("pLDDT", scores_d.get("plddt"))
                ipae_v = scores_d.get("i_pAE", scores_d.get("ipae"))
                fstatus = scores_d.get("filter_status") or "pass"
                new_cand = {
                    "rank": rank,
                    "pdb_key": upload_filename,
                    "iptm": round(float(iptm_v), 4) if isinstance(iptm_v, (int, float)) else None,
                    "plddt": round(float(plddt_v), 4) if isinstance(plddt_v, (int, float)) else None,
                    "i_pae": round(float(ipae_v), 4) if isinstance(ipae_v, (int, float)) else None,
                    "filter_status": fstatus,
                }
            except Exception as exc:
                logger.debug("Failed to build new_candidate: %s", exc)
                new_cand = None
            if webhook_url and job_id:
                send_heartbeat(
                    webhook_url, job_id, "Uploading candidates",
                    rank, len(candidates),
                    new_candidate=new_cand,
                )

        # Upload Kendrew-formatted metrics CSV
        if webhook_candidates:
            csv_path = os.path.join(work_dir, "metrics.csv")
            _write_metrics_csv(csv_path, webhook_candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], csv_path)
                except RuntimeError as exc:
                    logger.warning("Failed to upload metrics CSV: %s", exc)
                    failed_uploads.append("metrics.csv")

        # Upload raw BindCraft results CSV
        if "bindcraft_results.csv" in upload_urls and os.path.exists(bindcraft_csv):
            try:
                upload_output(upload_urls["bindcraft_results.csv"], bindcraft_csv)
            except RuntimeError as exc:
                logger.warning("Failed to upload BindCraft results CSV: %s", exc)
                failed_uploads.append("bindcraft_results.csv")

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
        if failed_uploads:
            result_payload["failed_uploads"] = failed_uploads
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
        # Archive BEFORE the rmtree destroys the tree. This finally is the only
        # point every exit path after the mkdtemp converges on: the clean return,
        # the zero-candidate "success" that uploads nothing, the failed upload-URL
        # exchange, and the catch-all except arm that posts the FAILED webhook.
        archive_work_dir(work_dir)
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
