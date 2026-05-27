"""Standalone pipeline script for BoltzGen on RunPod GPU Pods (and Modal).

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

Smoke/mini_pilot path (see docs/SMOKE-TEST-SPEC.md):
  When JOB_PAYLOAD contains tier="smoke" or "mini_pilot", we bypass the
  webhook/upload path entirely. The baked /opt/smoke_target.pdb fixture is
  used as the target and results are written to /tmp/smoke_results.json.
"""

import base64
import csv
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# pipeline_normalize.py is mounted alongside this script at /opt by
# infrastructure/modal/boltzgen_app.py. Adding /opt to sys.path makes the
# bare module name importable.
sys.path.insert(0, "/opt")

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

# ---------------------------------------------------------------------------
# Smoke/mini_pilot constants — see docs/SMOKE-TEST-SPEC.md
# ---------------------------------------------------------------------------
SMOKE_RESULTS_PATH = "/tmp/smoke_results.json"
SMOKE_TARGET_PDB = "/opt/smoke_target.pdb"  # baked into the Docker image
SMOKE_TARGET_CHAIN = "A"
# Reasonable PD-1 binding interface residues on PD-L1 chain A (PD-1 contact
# face of the IgV sheet). The baked PDB uses author numbering 18..132.
# These hotspots are now specified in ORIGINAL author numbering — the
# build_yaml_spec hotspot remap (Bug 9 fix) converts them to the post-
# reindex 1..N coordinate space using the renumber_map produced by
# ensure_cif. Equivalents (for cross-reference with prior versions of this
# file): author 54 -> 37, 56 -> 39, 115 -> 98, 123 -> 106.
SMOKE_HOTSPOTS = [54, 56, 115, 123]


# ---------------------------------------------------------------------------
# Tier presets (mirrors backend/pipelines/boltzgen.py::smoke_preset /
# mini_pilot_preset). Kept in sync manually because this script ships inside
# the Docker image and can't import from the backend package.
# ---------------------------------------------------------------------------
def _smoke_params() -> dict:
    """Smoke tier: 1 design, 1 budget, short binder. Proves pipeline runs."""
    return {
        "num_designs": 1,
        "budget": 1,
        "protocol": "protein-anything",
        "binder_length": {"min": 30, "max": 40},
    }


def _mini_pilot_params() -> dict:
    """Mini-pilot tier: 2 designs, budget 2, real scores."""
    return {
        "num_designs": 2,
        "budget": 2,
        "protocol": "protein-anything",
        "binder_length": {"min": 50, "max": 70},
    }


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
# Smoke/mini_pilot — Layer 2 preflight + result serialization
# ===========================================================================

def _write_smoke_failure(bucket: str, check: str, detail: str) -> None:
    """Write a structured preflight/compute failure to SMOKE_RESULTS_PATH.

    See docs/SMOKE-TEST-SPEC.md Layer 2 — the orchestrator reads this file
    after the subprocess exits to classify the failure.
    """
    payload = {
        "status": "FAILED",
        "error": {"bucket": bucket, "check": check, "detail": detail[:2000]},
    }
    try:
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            json.dump(payload, fh)
    except OSError as exc:
        logger.error("Failed to write %s: %s", SMOKE_RESULTS_PATH, exc)


def preflight(payload: dict) -> None:
    """Fail-fast checks before any compute. <= 60 s on GPU.

    On any failure writes a structured error to SMOKE_RESULTS_PATH and
    calls sys.exit(1). See docs/SMOKE-TEST-SPEC.md section "Layer 2".
    """
    logger.info("=== Preflight ===")

    tier = payload.get("tier", "")
    if tier not in ("smoke", "mini_pilot"):
        # Legacy webhook mode — preflight is a no-op; main() handles it.
        return

    # 1. Required payload keys.
    if "job_spec" not in payload:
        _write_smoke_failure("preflight", "payload", "missing key: job_spec")
        sys.exit(1)

    # 2. Target PDB (baked fixture).
    if not os.path.isfile(SMOKE_TARGET_PDB):
        _write_smoke_failure(
            "preflight", "target_pdb",
            f"baked smoke target not found at {SMOKE_TARGET_PDB}",
        )
        sys.exit(1)

    # 3. GPU available.
    try:
        import torch
        if not torch.cuda.is_available():
            _write_smoke_failure(
                "preflight", "gpu",
                "torch.cuda.is_available() is False",
            )
            sys.exit(1)
        logger.info("GPU: %s", torch.cuda.get_device_name(0))
    except Exception as exc:
        _write_smoke_failure("preflight", "torch_import", str(exc))
        sys.exit(1)

    # 4. gemmi import (required for CIF re-indexing).
    try:
        import gemmi  # noqa: F401
    except Exception as exc:
        _write_smoke_failure("preflight", "gemmi_import", str(exc))
        sys.exit(1)

    # 5. boltzgen CLI responds.
    try:
        result = subprocess.run(
            ["boltzgen", "--help"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            _write_smoke_failure(
                "preflight", "boltzgen --help",
                f"exit={result.returncode} stderr={result.stderr[-500:]}",
            )
            sys.exit(1)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _write_smoke_failure("preflight", "boltzgen_cli", str(exc))
        sys.exit(1)

    # 6. Weight cache present.
    if not os.path.isdir(BOLTZGEN_CACHE):
        _write_smoke_failure(
            "preflight", "boltzgen_cache",
            f"cache dir missing: {BOLTZGEN_CACHE}",
        )
        sys.exit(1)

    # 7. /tmp/smoke_results.json writable.
    try:
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            fh.write("{}")
    except OSError as exc:
        logger.error("Preflight: /tmp not writable: %s", exc)
        sys.exit(1)

    logger.info("Preflight: OK (tier=%s)", tier)


def _encode_pdb(path: str) -> str:
    """Return base64 of file bytes, per SMOKE-TEST-SPEC.md Layer 3."""
    return base64.b64encode(Path(path).read_bytes()).decode()


def _pdb_from_cif(cif_path: str, pdb_path: str) -> None:
    """Convert a CIF to PDB using gemmi (BoltzGen emits CIF)."""
    import gemmi
    structure = gemmi.read_structure(cif_path)
    structure.setup_entities()
    structure.write_pdb(pdb_path)


def _ensure_pdb_output(design_file: str, work_dir: str, rank: int) -> str:
    """Return a PDB path for a BoltzGen design output (convert if needed)."""
    if design_file.endswith(".pdb"):
        return design_file
    pdb_path = os.path.join(work_dir, f"design_{rank:03d}.pdb")
    _pdb_from_cif(design_file, pdb_path)
    return pdb_path


def _stub_scores(rank: int) -> dict:
    """Deterministic plausible scores for smoke when real metrics absent."""
    return {
        "ipTM": round(0.45 + 0.01 * rank, 4),
        "pLDDT": round(70.0 + rank, 2),
        "refolding_rmsd": round(2.5 - 0.1 * rank, 2),
        "filter_status": "stub (smoke)",
    }


def _build_smoke_job_spec(tier: str) -> dict:
    """Build a job_spec dict for smoke/mini_pilot runs."""
    if tier == "smoke":
        parameters = _smoke_params()
    elif tier == "mini_pilot":
        parameters = _mini_pilot_params()
    else:
        raise ValueError(f"Unknown tier: {tier}")

    return {
        "tool": "boltzgen",
        "target_chain": SMOKE_TARGET_CHAIN,
        "hotspot_residues": SMOKE_HOTSPOTS,
        "parameters": parameters,
    }


def _run_boltzgen_streaming(cmd: list[str], timeout: int, cwd: str | None = None) -> int:
    """Run `boltzgen run` with live stdout/stderr streaming.

    The existing run_command() captures output which (a) buffers megabytes
    in memory for long runs and (b) hides progress. Smoke/mini_pilot uses
    this streaming variant so we can watch BoltzGen initialise in Modal's
    live logs.
    """
    logger.info("Running (streaming): %s", " ".join(cmd))
    start = time.time()
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=sys.stdout, stderr=sys.stderr,
        bufsize=1,
    )
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)
        raise
    elapsed = time.time() - start
    logger.info("boltzgen exit=%d (%.1fs)", rc, elapsed)
    return rc


def run_smoke_tier(tier: str, work_dir: str) -> dict:
    """Execute the BoltzGen smoke/mini_pilot pipeline.

    Returns a dict shaped per SMOKE-TEST-SPEC.md Layer 3 "output shape".
    """
    start = time.time()
    job_spec = _build_smoke_job_spec(tier)
    params = job_spec["parameters"]
    num_designs = int(params["num_designs"])
    budget = int(params["budget"])
    protocol = params["protocol"]

    # ---- Stage 1: copy baked fixture + re-index to CIF ----
    target_input = os.path.join(work_dir, "target_input.pdb")
    shutil.copy(SMOKE_TARGET_PDB, target_input)

    try:
        target_chain = job_spec.get("target_chain", "A")
        target_cif, renumber_map = ensure_cif(
            target_input, work_dir, target_chain=target_chain,
        )
    except Exception as exc:
        logger.exception("CIF conversion failed")
        return {
            "status": "FAILED",
            "error": {"bucket": "preflight", "check": "cif_prep",
                      "detail": str(exc)},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    # ---- Stage 2: build YAML spec ----
    yaml_spec = build_yaml_spec(job_spec, target_cif, renumber_map=renumber_map)
    spec_path = write_yaml_spec(yaml_spec, target_cif, work_dir)

    # ---- Stage 3: run BoltzGen (streamed) ----
    output_dir = os.path.join(work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        "boltzgen", "run", spec_path,
        "--output", output_dir,
        "--protocol", protocol,
        "--num_designs", str(num_designs),
        "--budget", str(budget),
        "--devices", "1",
    ]

    # Smoke timeout: 20 min. Mini-pilot: 30 min. Budget in GPU-minutes is 30
    # and 20 respectively per the agent brief, keep runs well inside that.
    timeout_s = 1200 if tier == "smoke" else 1800
    try:
        rc = _run_boltzgen_streaming(cmd, timeout=timeout_s, cwd=work_dir)
    except subprocess.TimeoutExpired:
        return {
            "status": "FAILED",
            "error": {"bucket": "tool-invocation", "check": "boltzgen_timeout",
                      "detail": f"boltzgen run exceeded {timeout_s}s"},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    if rc != 0:
        return {
            "status": "FAILED",
            "error": {"bucket": "tool-invocation", "check": "boltzgen_exit",
                      "detail": f"boltzgen run exited {rc}"},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    # ---- Stage 4: locate design files ----
    # Log output tree for debugging, independent of whether the metrics CSV
    # shows up where we expect.
    for root, _dirs, files in os.walk(output_dir):
        rel = os.path.relpath(root, output_dir)
        for fname in files:
            logger.info("Output: %s/%s", rel, fname)

    design_files = find_design_files(output_dir, budget)
    if not design_files:
        # Fall back to searching the whole output tree for any *.cif/*.pdb
        design_files = []
        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                if fname.endswith((".cif", ".pdb")) and "target" not in fname.lower():
                    design_files.append(os.path.join(root, fname))
        design_files.sort()

    if not design_files:
        return {
            "status": "FAILED",
            "error": {"bucket": "output-parse", "check": "design_files",
                      "detail": "no .cif/.pdb produced by boltzgen run"},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    # ---- Stage 5: parse metrics (best-effort for smoke, required for mini_pilot) ----
    metrics_csv = find_metrics_csv(output_dir)
    design_scores_by_name: dict[str, dict] = {}
    if metrics_csv:
        logger.info("Metrics CSV: %s", metrics_csv)
        try:
            for entry in parse_metrics_csv(metrics_csv):
                design_scores_by_name[entry["design_name"]] = entry["scores"]
        except Exception as exc:
            logger.warning("metrics CSV parse failed: %s", exc)
    else:
        logger.warning("No metrics CSV found in BoltzGen output tree")

    # ---- Stage 6: build candidate list ----
    # Rank by ipTM desc where we have real scores; preserve file order otherwise.
    scored = []
    for design_file in design_files:
        name = Path(design_file).stem
        scores = design_scores_by_name.get(name, {})
        # fuzzy match
        if not scores:
            for key, val in design_scores_by_name.items():
                if name in key or key in name:
                    scores = val
                    break
        scored.append({"design_file": design_file, "name": name, "scores": scores})

    if any("ipTM" in s["scores"] for s in scored):
        scored.sort(key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True)

    scored = scored[:num_designs]

    candidates = []
    for rank_idx, s in enumerate(scored):
        rank = rank_idx + 1
        try:
            pdb_path = _ensure_pdb_output(s["design_file"], work_dir, rank)
        except Exception as exc:
            return {
                "status": "FAILED",
                "error": {"bucket": "output-parse", "check": "cif_to_pdb",
                          "detail": f"{s['design_file']}: {exc}"},
                "tier": tier,
                "gpu_seconds": int(time.time() - start),
            }
        scores = dict(s["scores"]) if s["scores"] else {}
        # Mini-pilot requires real float scores. For smoke, stub if absent.
        if not scores:
            if tier == "mini_pilot":
                return {
                    "status": "FAILED",
                    "error": {"bucket": "output-parse", "check": "missing_scores",
                              "detail": f"no metrics for design {s['name']}"},
                    "tier": tier,
                    "gpu_seconds": int(time.time() - start),
                }
            scores = _stub_scores(rank)
        candidates.append({
            "rank": rank,
            "pdb_key": f"design_{rank:03d}.pdb",
            "pdb_content_b64": _encode_pdb(pdb_path),
            "scores": scores,
        })

    if len(candidates) < num_designs:
        return {
            "status": "FAILED",
            "error": {"bucket": "output-parse", "check": "candidate_count",
                      "detail": f"got {len(candidates)}, expected {num_designs}"},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    return {
        "status": "COMPLETED",
        "output": {"candidates": candidates},
        "tier": tier,
        "gpu_seconds": int(time.time() - start),
    }


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


_WEBHOOK_OUTCOME_PATH = "/tmp/webhook_outcome.json"


def _record_webhook_outcome(delivered: bool, detail: str) -> None:
    """Persist webhook delivery status so the Modal wrapper can surface
    it to the consuming web service even when the POST silently fails. Read by run_tool()
    in infrastructure/modal/boltzgen_app.py and merged into the function
    return value, where the web service's poller inspects it."""
    try:
        with open(_WEBHOOK_OUTCOME_PATH, "w") as fh:
            json.dump({"delivered": delivered, "detail": detail}, fh)
    except OSError as exc:
        logger.error("Failed to write webhook outcome file: %s", exc)


def post_webhook(
    webhook_url: str, job_id: str, pod_id: str, payload: dict,
) -> None:
    """POST results to the Kendrew backend webhook."""
    if not webhook_url:
        logger.error(
            "post_webhook: empty webhook_url for job %s; cannot deliver result",
            job_id,
        )
        _record_webhook_outcome(False, "empty webhook_url")
        return

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
        resp.raise_for_status()
        _record_webhook_outcome(True, f"http {resp.status_code}")
    except Exception as exc:
        logger.error("Webhook POST failed: %s", exc)
        _record_webhook_outcome(False, f"{type(exc).__name__}: {exc}")


# ===========================================================================
# CIF conversion and re-indexing
# ===========================================================================

def ensure_cif(
    input_path: str, work_dir: str, target_chain: str = "A",
) -> tuple[str, dict]:
    """Convert the downloaded PDB into a BoltzGen-ready mmCIF.

    Returns a tuple ``(cif_path, renumber_map)``. The renumber_map is
    ``{(chain_id, original_resnum): new_resnum}``. Callers
    (``build_yaml_spec``) use it to rewrite hotspot indices into the
    cleaned coordinate space.

    BoltzGen's mmcif parser is strict: it requires the
    ``_entity_poly_seq`` block to exist and every residue in
    ``_atom_site`` to match by name the entry at the same seq position
    in ``_entity_poly_seq``. We side-step the whole problem by writing a
    minimal CIF from scratch with exactly the blocks BoltzGen reads.

    Pipeline (Bug 9 fix, 2026-04-30):
      1. Sanitize with Biopython (``pipeline_normalize.normalize_for_boltzgen``):
         drop waters, HETATM, hydrogens, altlocs, multi-model, MSE->MET,
         filter to ``target_chain`` only, renumber 1..N. Result is a clean
         single-chain PDB on disk.
      2. Read that cleaned PDB with gemmi for the custom CIF write below.

    The resulting CIF contains only standard-20-AA polymer residues with
    contiguous seqids starting at 1 per chain, no altlocs, no hydrogens,
    no ligands, no waters.
    """
    from pipeline_normalize import normalize_for_boltzgen  # noqa: PLC0415

    import gemmi  # noqa: PLC0415
    from gemmi import cif  # noqa: PLC0415

    # ---- Stage 1: Biopython sanitize + renumber ----
    cleaned_pdb = os.path.join(work_dir, "cleaned.pdb")
    norm_report = normalize_for_boltzgen(
        input_path, cleaned_pdb, target_chain=target_chain,
    )
    logger.info(
        "Normalize: chains_kept=%s chains_dropped=%s residues_kept=%s "
        "residues_dropped=%s changes=%s",
        norm_report.chains_kept, norm_report.chains_dropped,
        norm_report.residues_kept_per_chain,
        norm_report.residues_dropped_per_chain,
        norm_report.changes,
    )

    STANDARD_AA = frozenset([
        "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
        "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    ])
    MODRES_MAP = {
        "MSE": ("MET", {"SE": ("SD", "S")}),
        "CME": ("CYS", {}), "CSO": ("CYS", {}), "SEP": ("SER", {}),
        "TPO": ("THR", {}), "PTR": ("TYR", {}), "KCX": ("LYS", {}),
        "HYP": ("PRO", {}), "LLP": ("LYS", {}),
    }

    logger.info("Reading cleaned PDB into gemmi: %s", cleaned_pdb)
    structure = gemmi.read_structure(cleaned_pdb)

    # Defensive setup_entities + cleanup quartet. With a polymer-only input
    # these are largely no-ops, but kept as belt-and-braces.
    try:
        structure.setup_entities()
    except Exception as exc:  # pragma: no cover
        logger.warning("setup_entities() raised %s; continuing", exc)
    structure.remove_alternative_conformations()
    structure.remove_hydrogens()
    try:
        structure.remove_ligands_and_waters()
    except Exception as exc:
        logger.warning(
            "remove_ligands_and_waters() raised %s post-normalize; continuing",
            exc,
        )
    structure.remove_empty_chains()

    # Extract per-chain sequences and atom records.
    # chains_data: { chain_name: [ {resnum, resname, atoms: [...]} ] }
    chains_data: dict[str, list[dict]] = {}
    modres_renames = 0
    dropped_counts: dict[str, int] = {}

    for model in structure:
        for chain in model:
            chain_residues: list[dict] = []
            for residue in chain:
                name = residue.name

                # Rename modified residues.
                atom_rename = {}
                if name in MODRES_MAP:
                    new_name, atom_fixes = MODRES_MAP[name]
                    name = new_name
                    atom_rename = atom_fixes
                    modres_renames += 1

                # Drop anything still non-standard.
                if name not in STANDARD_AA:
                    dropped_counts[residue.name] = dropped_counts.get(residue.name, 0) + 1
                    continue

                atoms: list[tuple[str, str, float, float, float]] = []
                for atom in residue:
                    atom_name = atom.name
                    atom_elem = atom.element.name
                    if atom_name in atom_rename:
                        atom_name, atom_elem = atom_rename[atom_name]
                    # Skip any stray hydrogens that slipped through.
                    if atom_elem == "H":
                        continue
                    atoms.append((
                        atom_name, atom_elem,
                        atom.pos.x, atom.pos.y, atom.pos.z,
                    ))
                if atoms:
                    chain_residues.append({
                        "resname": name,
                        "atoms": atoms,
                    })

            if chain_residues:
                chains_data.setdefault(chain.name, []).extend(chain_residues)

        break  # Only first model

    if not chains_data:
        raise RuntimeError("No standard polymer residues survived cleanup")

    # Re-index residues 1..N per chain.
    for chain_name, residues in chains_data.items():
        for idx, res in enumerate(residues, start=1):
            res["resnum"] = idx

    kept_counts = {c: len(r) for c, r in chains_data.items()}
    logger.info(
        "CIF prep: modres_renames=%d, kept_per_chain=%s, dropped=%s",
        modres_renames, kept_counts, dropped_counts,
    )

    # Build CIF document manually so we control every required block.
    doc = cif.Document()
    block = doc.add_new_block("target")

    # Each chain -> its own entity (entity_id = index+1).
    chain_names = list(chains_data.keys())
    chain_to_entity = {name: str(i + 1) for i, name in enumerate(chain_names)}

    # _entity
    e_loop = block.init_loop("_entity.", ["id", "type"])
    for name in chain_names:
        e_loop.add_row([chain_to_entity[name], "polymer"])

    # _entity_poly
    ep_loop = block.init_loop("_entity_poly.", ["entity_id", "type"])
    for name in chain_names:
        ep_loop.add_row([chain_to_entity[name], "polypeptide(L)"])

    # _entity_poly_seq — one row per residue.
    eps_loop = block.init_loop(
        "_entity_poly_seq.", ["entity_id", "num", "mon_id"],
    )
    for name in chain_names:
        eid = chain_to_entity[name]
        for res in chains_data[name]:
            eps_loop.add_row([eid, str(res["resnum"]), res["resname"]])

    # _struct_asym — subchain ids. Use chain name as asym_id.
    sa_loop = block.init_loop("_struct_asym.", ["id", "entity_id"])
    for name in chain_names:
        sa_loop.add_row([name, chain_to_entity[name]])

    # _atom_site — coords.
    as_loop = block.init_loop("_atom_site.", [
        "group_PDB", "id", "type_symbol", "label_atom_id",
        "label_alt_id", "label_comp_id", "label_asym_id",
        "label_entity_id", "label_seq_id",
        "Cartn_x", "Cartn_y", "Cartn_z",
        "occupancy", "B_iso_or_equiv",
        "auth_asym_id", "auth_seq_id", "auth_comp_id",
        "pdbx_PDB_model_num",
    ])
    atom_id = 0
    for name in chain_names:
        eid = chain_to_entity[name]
        for res in chains_data[name]:
            resnum = str(res["resnum"])
            resname = res["resname"]
            for atom_name, atom_elem, x, y, z in res["atoms"]:
                atom_id += 1
                as_loop.add_row([
                    "ATOM", str(atom_id), atom_elem, atom_name,
                    ".", resname, name,
                    eid, resnum,
                    f"{x:.3f}", f"{y:.3f}", f"{z:.3f}",
                    "1.00", "20.00",
                    name, resnum, resname,
                    "1",
                ])

    cif_path = os.path.join(work_dir, "target.cif")
    doc.write_file(cif_path)
    logger.info(
        "CIF written to %s (%d bytes, %d atoms, %d residues)",
        cif_path, os.path.getsize(cif_path), atom_id,
        sum(kept_counts.values()),
    )
    return cif_path, dict(norm_report.renumber_map)


# ===========================================================================
# BoltzGen YAML spec generation
# ===========================================================================

def build_yaml_spec(
    job_spec: dict, target_cif_path: str,
    renumber_map: dict | None = None,
) -> dict:
    """Build the BoltzGen YAML design spec from the JobSpec.

    Mirrors backend/pipelines/boltzgen.py::generate_config so the container
    is self-sufficient when the backend dispatches a raw JobSpec without a
    pre-built yaml_spec in parameters.

    Produces:
      entities:
        - file:
            path: <target_cif>
            include:
              - chain: {id: <chain>}
            binding_types:           # only when hotspots specified
              - chain: {id: <chain>, binding: "50,51,52"}
        - protein:
            id: B
            sequence: "<min>..<max>"  # binder length range

    Args:
        job_spec: Deserialized JobSpec dict from JOB_PAYLOAD.
        target_cif_path: Path to the re-indexed target CIF inside the container.

    Returns:
        Dict representing the BoltzGen YAML spec (with an ``entities`` list).
    """
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    raw_hotspots = list(job_spec.get("hotspot_residues", []) or [])

    # Hotspot remap (Bug 9 fix): user-supplied hotspots refer to original
    # PDB numbering; the CIF stage renumbered residues 1..N. Use the
    # renumber_map produced by ensure_cif to convert.
    if renumber_map:
        remapped: list = []
        missing: list = []
        for h in raw_hotspots:
            try:
                orig = int(h)
            except (TypeError, ValueError):
                missing.append(str(h))
                continue
            new = renumber_map.get((chain, orig))
            if new is None:
                missing.append(orig)
            else:
                remapped.append(new)
        if missing:
            logger.warning(
                "build_yaml_spec: hotspot residues not found after cleanup "
                "(skipped): %s. Original hotspots: %s. Chain %s has "
                "renumber-map entries for residues: %s",
                missing, raw_hotspots, chain,
                sorted(r for c, r in renumber_map if c == chain)[:25],
            )
        hotspots = remapped
    else:
        hotspots = raw_hotspots

    # Binder length range from parameters.
    binder_length = params.get("binder_length", {"min": 50, "max": 100})
    if isinstance(binder_length, dict):
        min_len = binder_length.get("min", 50)
        max_len = binder_length.get("max", 100)
    else:
        min_len, max_len = 50, 100

    file_entity: dict = {
        "file": {
            "path": target_cif_path,
            "include": [{"chain": {"id": chain}}],
        },
    }
    if hotspots:
        binding_str = ",".join(str(r) for r in sorted(hotspots))
        file_entity["file"]["binding_types"] = [
            {"chain": {"id": chain, "binding": binding_str}},
        ]

    binder_entity = {
        "protein": {
            "id": "B",
            "sequence": f"{min_len}..{max_len}",
        },
    }

    yaml_spec = {"entities": [file_entity, binder_entity]}
    logger.info(
        "Built YAML spec: chain=%s, hotspots=%s, binder_length=%d..%d",
        chain, hotspots, min_len, max_len,
    )
    return yaml_spec


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
      final_ranked_designs/final_{budget}_designs/rank{N}_{spec_id}.cif

    There's also an ``intermediate_ranked_{K}_designs`` sibling (K == the
    intermediate trajectory count, e.g. 10, not the budget). We prefer the
    final_{budget}_designs directory because those are the post-refold
    ranked outputs.

    Args:
        output_dir: BoltzGen output directory.
        budget: The --budget value used (determines subdirectory name).

    Returns:
        Sorted list of paths to design structure files (excluding the
        before_refolding/ subdir).
    """
    parent = os.path.join(output_dir, "final_ranked_designs")
    ranked_dir: str | None = None

    # Preferred: final_{budget}_designs
    candidate = os.path.join(parent, f"final_{budget}_designs")
    if os.path.isdir(candidate):
        ranked_dir = candidate

    # Fallback: any final_*_designs (BoltzGen may round/adjust the count)
    if ranked_dir is None and os.path.isdir(parent):
        for d in sorted(os.listdir(parent)):
            if d.startswith("final_") and d.endswith("_designs"):
                candidate = os.path.join(parent, d)
                if os.path.isdir(candidate):
                    ranked_dir = candidate
                    break

    # Fallback: intermediate_ranked_*_designs
    if ranked_dir is None and os.path.isdir(parent):
        for d in sorted(os.listdir(parent)):
            if d.startswith("intermediate_ranked_") and d.endswith("_designs"):
                candidate = os.path.join(parent, d)
                if os.path.isdir(candidate):
                    ranked_dir = candidate
                    break

    # Last resort: parent directory itself
    if ranked_dir is None:
        ranked_dir = parent if os.path.isdir(parent) else output_dir

    design_files = []
    for root, _dirs, files in os.walk(ranked_dir):
        # Skip the pre-refold staging directory — we want the final structures.
        if "before_refolding" in os.path.relpath(root, ranked_dir).split(os.sep):
            continue
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

    BoltzGen aggregate_metrics_analyze.csv columns include:
      id, file_name, designed_sequence, ...,
      native_rmsd, native_rmsd_bb, native_rmsd_refolded, native_rmsd_bb_refolded,
      designfolding-bb_rmsd, bb_rmsd, iptm, ptm, design_iptm, complex_plddt, ...

    We pick iptm for ipTM, complex_plddt for pLDDT, and prefer the refolded
    backbone RMSD for refolding_rmsd. Multiplies pLDDT by 100 if it looks
    normalized (BoltzGen emits complex_plddt in [0,1]).

    Returns:
        List of dicts with design_name and scores.
    """
    # BoltzGen metrics value order of preference for each canonical score key.
    # IMPORTANT: For de novo binder design (no native binder reference) BoltzGen
    # ships the `native_rmsd_*` columns as 0.0, not NaN — so they MUST come
    # AFTER the actual refolding self-RMSD columns or every binder shows
    # RMSD=0.00. The "Refolding RMSD" UI label refers to a structure-vs-
    # refolded-from-sequence comparison, which lives in `designfolding-bb_rmsd`
    # and `bb_rmsd`, not in any `native_rmsd_*`.
    RMSD_KEYS = [
        "designfolding-bb_rmsd", "bb_rmsd",
        "refolding_rmsd",
        "native_rmsd_bb_refolded", "native_rmsd_refolded",
        "native_rmsd_bb", "native_rmsd",
        "rmsd", "RMSD", "design_rmsd", "ca_rmsd",
    ]
    IPTM_KEYS = [
        "iptm", "ipTM", "iPTM", "design_iptm", "protein_iptm",
        "interface_ptm", "iptm_score",
    ]
    PLDDT_KEYS = [
        "complex_plddt", "complex_iplddt",
        "pLDDT", "plddt", "mean_plddt", "binder_plddt", "avg_plddt",
    ]

    results = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        logger.info("Metrics CSV columns (%d): first20=%s", len(columns), columns[:20])

        for row in reader:
            # Design name: use file_name (BoltzGen's canonical key), else id.
            name_raw = (
                row.get("file_name")
                or row.get("design_name")
                or row.get("design")
                or row.get("name")
                or row.get("id")
                or row.get("sample")
                or "unknown"
            )
            design_name = Path(str(name_raw)).stem

            scores: dict = {}

            for key in RMSD_KEYS:
                if key in row and row[key] not in (None, ""):
                    scores["refolding_rmsd"] = _safe_float(row[key], 99.0)
                    break

            for key in IPTM_KEYS:
                if key in row and row[key] not in (None, ""):
                    scores["ipTM"] = _safe_float(row[key], 0.0)
                    break

            for key in PLDDT_KEYS:
                if key in row and row[key] not in (None, ""):
                    val = _safe_float(row[key], 0.0)
                    # BoltzGen emits complex_plddt in [0,1]; rescale to 0..100.
                    if 0.0 <= val <= 1.0:
                        val = val * 100.0
                    scores["pLDDT"] = round(val, 2)
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

    # Validate the payload against the shared contract module mounted at
    # /opt/contracts by the Modal image build. On import or validation
    # failure, write a preflight marker and exit non-zero so the wrapper
    # surfaces a clear contract error rather than a downstream KeyError.
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

    # ---- Smoke / mini_pilot tier: bypass webhook+upload, write to
    #      /tmp/smoke_results.json. See docs/SMOKE-TEST-SPEC.md. ----
    tier = job_payload.get("tier", "")
    if tier in ("smoke", "mini_pilot"):
        preflight(job_payload)
        work_dir = tempfile.mkdtemp(prefix="boltzgen_smoke_")
        try:
            result = run_smoke_tier(tier, work_dir)
            with open(SMOKE_RESULTS_PATH, "w") as fh:
                json.dump(result, fh)
            logger.info(
                "Smoke tier %s: status=%s gpu_seconds=%s",
                tier, result.get("status"), result.get("gpu_seconds"),
            )
            if result.get("status") != "COMPLETED":
                sys.exit(1)
            return
        except Exception as exc:
            logger.exception("smoke tier crashed")
            _write_smoke_failure("unhandled", "run_smoke_tier", str(exc))
            sys.exit(1)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ---- Legacy webhook path ----
    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    # Extract BoltzGen-specific config from job_spec parameters.
    # The backend may (a) pre-build yaml_spec in parameters, or (b) dispatch a
    # raw JobSpec and expect the container to build it from target_chain +
    # hotspot_residues + binder_length. Support both.
    params = job_spec.get("parameters", {})
    yaml_spec = params.get("yaml_spec") or {}
    protocol = params.get("protocol", "protein-anything")
    num_designs = params.get("num_designs", 10000)
    # Pilot tier clamps budget to 5; honour it container-side as well.
    # tier is read from job_payload (line ~1296), not job_spec — the wrapper
    # keys (job_tier/tier) sit at the payload level, not inside job_spec.
    default_budget = 5 if tier == "pilot" else 60
    budget = params.get("budget", default_budget)

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
            target_chain = job_spec.get("target_chain", "A")
            target_cif, renumber_map = ensure_cif(
                target_input, work_dir, target_chain=target_chain,
            )
        except Exception as exc:
            logger.error("CIF conversion failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"CIF conversion failed: {exc}",
            })
            return

        # ----- Stage 3: Write YAML spec -----
        # Backend's dispatcher currently sends the raw JobSpec without calling
        # BoltzGenPipeline.generate_config(), so parameters.yaml_spec is often
        # empty. Build it here from target_chain + hotspot_residues in that case.
        if not yaml_spec.get("entities"):
            logger.info(
                "yaml_spec has no entities; constructing from JobSpec "
                "(target_chain=%s, hotspots=%s)",
                job_spec.get("target_chain", "A"),
                job_spec.get("hotspot_residues", []),
            )
            yaml_spec = build_yaml_spec(
                job_spec, target_cif, renumber_map=renumber_map,
            )

        if not yaml_spec.get("entities"):
            logger.error("yaml_spec must contain at least one entity")
            post_webhook(webhook_url, job_id, pod_id, {
                "error": "Invalid yaml_spec: no entities defined",
            })
            return

        spec_path = write_yaml_spec(yaml_spec, target_cif, work_dir)

        # ----- Stage 4: Run BoltzGen -----
        # BoltzGen's generation + refolding runs as a single long subprocess
        # (15-45 min). Without a sidecar heartbeat the backend's
        # STALE_HEARTBEAT_SECONDS (1800s = 30min) cron reaps the job
        # mid-run. Fire a keepalive heartbeat every 5 min.
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
        heartbeat_stop = threading.Event()

        def _keepalive() -> None:
            while not heartbeat_stop.wait(300):  # 5 min
                try:
                    send_heartbeat(
                        webhook_url, job_id,
                        "Running BoltzGen", 0, budget,
                    )
                except Exception as exc:  # pragma: no cover - best-effort
                    logger.warning("keepalive heartbeat failed: %s", exc)

        keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
        keepalive_thread.start()
        try:
            run_command(cmd, timeout=boltzgen_timeout, cwd=work_dir)
        except RuntimeError as exc:
            logger.error("BoltzGen failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"BoltzGen failed: {exc}",
            })
            return
        finally:
            heartbeat_stop.set()

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

        # ----- Build structure file map (needed up front so the pilot fallback
        # can intersect ipTM-top-N with designs that actually have structures
        # on disk; BoltzGen only writes CIFs for its own internally-ranked
        # final_5_designs/, so a pure top-by-ipTM fallback drops 4/5 designs).
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

        def _has_structure_file(design_name: str) -> bool:
            """Return True if the design has a matching CIF/PDB on disk."""
            if design_name in design_file_map:
                return True
            for key in design_file_map:
                if design_name in key or key in design_name:
                    return True
            return False

        # ----- Filter and rank -----
        passing = filter_and_rank(all_designs)
        # Pilot tier fallback: E2E pipeline validation must not be gated by
        # design quality — a pilot with a random target + low num_designs
        # often produces designs below production thresholds. Upload the top
        # N by ipTM regardless so the job can COMPLETE with candidates and
        # prove the MinIO-upload / webhook / parse_results path works. The
        # filter_status field records that these didn't pass production
        # thresholds so downstream agents know not to trust the score.
        # IMPORTANT: only consider designs that actually have a structure file
        # — BoltzGen writes CIFs only for its own internally-ranked top-N, so
        # a naive top-by-ipTM fallback drops most candidates silently and
        # leaves the user with 1 candidate when the UI promises up to budget.
        if not passing and tier == "pilot" and all_designs:
            structured = [d for d in all_designs if _has_structure_file(d["design_name"])]
            structured.sort(key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True)
            passing = structured[: max(1, budget)]
            for d in passing:
                d["scores"]["filter_status"] = "below threshold"
            logger.warning(
                "No designs passed production thresholds; pilot fallback "
                "emitting top %d by ipTM (filtered to designs with structure "
                "files; all marked filter_status='below threshold') so "
                "validation succeeds.",
                len(passing),
            )

        # ----- Prepare upload list -----
        # rank_idx is the index into `passing`, but designs without a matching
        # structure file get skipped — so we can't use rank_idx for the
        # candidate rank or we end up with gaps (e.g. rank=5 for the only
        # surviving candidate). Use a separate counter that only advances
        # when we actually keep a design.
        candidates = []
        filenames_to_upload = []
        emitted_rank = 0

        for design in passing:
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

            emitted_rank += 1
            rank = emitted_rank

            ext = Path(design_file).suffix  # .cif or .pdb
            upload_filename = f"design_{rank:03d}{ext}"
            filenames_to_upload.append(upload_filename)

            # pdb_key MUST share basename with upload_filename so the
            # web service's resolver finds the Storage object at
            # {user}/{job}/designs/<basename>. design_name diverges
            # from upload_filename and would 404 the resolver. The
            # contracts module (/opt/contracts/rpc.py) defines the
            # upload-URL exchange shape consumed by the web service.
            candidates.append({
                "rank": rank,
                "design_name": design_name,
                "pdb_key": f"designs/{upload_filename}",
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

        # Build candidates with inline base64 PDBs so the consuming web
        # service's frontend can render the 3D viewer + PDB-download
        # buttons. Without this the candidate table template falls back
        # to the em-dash branch because it keys off cand.pdb_content_b64.
        # Mirrors the smoke/mini_pilot path (line 496) for consistency.
        webhook_candidates: list[dict] = []
        for c in candidates:
            entry = {
                "rank": c["rank"],
                "pdb_key": c["pdb_key"],
                "scores": c["scores"],
            }
            local_file = c.get("local_file")
            if local_file and os.path.exists(local_file):
                try:
                    pdb_path = _ensure_pdb_output(local_file, work_dir, c["rank"])
                    entry["pdb_content_b64"] = _encode_pdb(pdb_path)
                except Exception as exc:
                    logger.warning(
                        "Failed to encode PDB for rank %d (%s): %s",
                        c["rank"], local_file, exc,
                    )
            webhook_candidates.append(entry)

        result_payload = {
            "candidates": webhook_candidates,
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
