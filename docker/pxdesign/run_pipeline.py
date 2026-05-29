"""Standalone pipeline script for PXDesign on Modal / RunPod GPU Pods.

Reads job configuration from the JOB_PAYLOAD environment variable.
Supports three execution tiers (see docs/SMOKE-TEST-SPEC.md):

  * tier == "smoke"       -> N=1 basic mode, no post-filter.
                              Writes results inline to /tmp/smoke_results.json.
  * tier == "mini_pilot"  -> N=1 basic mode, full scoring (PXDesign-specific
                              exception; other tools use N=2). See
                              docs/SMOKE-TEST-SPEC.md "Per-tool exceptions".
                              Writes results inline to /tmp/smoke_results.json.
  * default (webhook)     -> legacy RunPod pilot path: presigned I/O + webhook.

For smoke/mini_pilot when ``input_pdb_url`` is empty, the baked-in
/opt/smoke_target.pdb fixture is used (PD-L1 IgV, chain A, residues 18-132).

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, tier, input_pdb_url, etc.
    WEBHOOK_URL     URL to POST results to (webhook mode only)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    RUNPOD_POD_ID   RunPod pod ID (webhook mode only)
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
import traceback
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# pipeline_normalize.py is mounted alongside this script at /opt by
# infrastructure/modal/pxdesign_app.py (.add_local_file). Adding /opt to
# sys.path makes the bare module name importable.
sys.path.insert(0, "/opt")

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
SMOKE_TARGET_PATH = "/opt/smoke_target.pdb"
SMOKE_RESULTS_PATH = "/tmp/smoke_results.json"

# Filtering thresholds for PXDesign output (webhook-tier only)
IPTM_THRESHOLD = 0.70
PLDDT_THRESHOLD = 80.0
PAE_THRESHOLD = 10.0


# ===========================================================================
# Tier presets (mirrored from backend/pipelines/pxdesign.py)
# ===========================================================================

def smoke_preset() -> dict:
    """N=1, no-MSA (``preview``) mode, no post-filter.

    PXDesign CLI names the no-MSA mode ``preview`` (vs ``extended`` which
    requires MSA). SMOKE-TEST-SPEC.md refers to this as "Basic" mode.
    """
    return {
        "num_designs": 1,
        "preset": "preview",
        "post_filter": False,
        "binder_length": 80,
    }


def mini_pilot_preset() -> dict:
    """N=1, no-MSA (``preview``) mode, full post-scoring. Final success gate.

    PXDesign-specific exception: other tools use N=2, but each PXDesign design
    takes ~35 GPU-min due to AF2-IG validation. N=1 with real ipTM/pLDDT/pAE
    is sufficient pipeline-end-to-end evidence. Real pilots use pilot_preset
    (unchanged, N>=2). See docs/SMOKE-TEST-SPEC.md "Per-tool exceptions".
    """
    return {
        "num_designs": 1,
        "preset": "preview",
        "post_filter": True,
        "binder_length": 80,
    }


# ===========================================================================
# Smoke-result serialization
# ===========================================================================

def write_smoke_result(payload: dict) -> None:
    """Write a smoke/mini_pilot result JSON to /tmp/smoke_results.json.

    The Modal wrapper (infrastructure/modal/pxdesign_app.py::run_tool) reads
    this file after the subprocess exits and merges it into the return dict
    under key ``smoke_result``. Failures here are non-fatal.
    """
    try:
        Path(SMOKE_RESULTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        logger.info(
            "Wrote smoke_results.json (%d bytes, status=%s)",
            os.path.getsize(SMOKE_RESULTS_PATH),
            payload.get("status"),
        )
    except Exception as exc:  # pragma: no cover - best-effort
        logger.error("Failed to write smoke_results.json: %s", exc)


def fail_preflight(check: str, detail: str) -> None:
    """Emit a structured preflight failure to smoke_results.json and exit 1."""
    write_smoke_result({
        "status": "FAILED",
        "error": {
            "bucket": "preflight",
            "check": check,
            "detail": detail[-2000:] if detail else "",
        },
    })
    logger.error("Preflight failed (%s): %s", check, detail[:500])
    sys.exit(1)


def fail_compute(check: str, detail: str) -> None:
    """Emit a structured compute-stage failure and exit 1."""
    write_smoke_result({
        "status": "FAILED",
        "error": {
            "bucket": check,
            "detail": detail[-2000:] if detail else "",
        },
    })
    logger.error("Pipeline failed (%s): %s", check, detail[:500])


# ===========================================================================
# Layer 2: preflight
# ===========================================================================

def preflight(payload: dict) -> None:
    """Run fail-fast checks before consuming GPU compute.

    Budget: must complete in <=60s on an A100-80GB.

    Validates:
      1. Payload parses and required fields are present.
      2. Target PDB is accessible (local fixture or URL HEAD 200).
      3. torch.cuda + GPU SKU are visible.
      4. pxdesign CLI responds to --help with exit 0.
      5. /tmp/smoke_results.json is writable.
      6. JAX JIT primed with a tiny matmul so the first real run isn't 3x slower.

    On any failure, writes structured error to SMOKE_RESULTS_PATH and exits 1.
    """
    logger.info("=== PREFLIGHT START ===")
    t0 = time.time()

    tier = payload.get("tier", "")

    # ---- 1. writable results dir ----
    try:
        Path(SMOKE_RESULTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            fh.write("{}")
        os.remove(SMOKE_RESULTS_PATH)
    except Exception as exc:
        fail_preflight("results_writable", f"{SMOKE_RESULTS_PATH} not writable: {exc}")

    # ---- 2. target PDB accessible ----
    input_url = payload.get("input_pdb_url", "") or ""
    if tier in ("smoke", "mini_pilot") and not input_url:
        # Smoke path: use baked fixture.
        if not os.path.exists(SMOKE_TARGET_PATH):
            fail_preflight("target_fixture", f"{SMOKE_TARGET_PATH} missing in image")
        try:
            size = os.path.getsize(SMOKE_TARGET_PATH)
            if size < 1000:
                fail_preflight("target_fixture", f"{SMOKE_TARGET_PATH} too small ({size} bytes)")
        except OSError as exc:
            fail_preflight("target_fixture", f"stat failed: {exc}")
        logger.info("Target PDB fixture: %s (%d bytes)", SMOKE_TARGET_PATH, size)
    elif input_url:
        try:
            head = requests.head(input_url, timeout=15, allow_redirects=True)
            if head.status_code != 200:
                fail_preflight(
                    "target_url",
                    f"HEAD {input_url} -> HTTP {head.status_code}",
                )
        except Exception as exc:
            fail_preflight("target_url", f"HEAD failed: {exc}")

    # ---- 3. CUDA / torch ----
    try:
        import torch
        if not torch.cuda.is_available():
            fail_preflight("cuda_available", "torch.cuda.is_available() is False")
        gpu_name = torch.cuda.get_device_name(0)
        logger.info("CUDA OK: torch=%s cuda=%s gpu=%s",
                    torch.__version__, torch.version.cuda, gpu_name)
    except Exception as exc:
        fail_preflight("torch_import", f"torch import / CUDA check failed: {exc}")

    # ---- 4. JAX GPU visibility ----
    try:
        import jax
        gpu_devices = jax.devices("gpu")
        if not gpu_devices:
            fail_preflight("jax_gpu", "jax.devices('gpu') returned empty")
        logger.info("JAX OK: %s devices=%s", jax.__version__, gpu_devices)
    except Exception as exc:
        # Not all PXDesign execution paths require JAX on the driver process
        # (AF2-IG runs as a subprocess). Warn but do not abort — the subprocess
        # will surface any real issue.
        logger.warning("JAX GPU check warning (non-fatal): %s", exc)

    # ---- 5. pxdesign CLI ----
    try:
        result = subprocess.run(
            ["pxdesign", "--help"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            fail_preflight(
                "pxdesign_cli",
                f"exit {result.returncode}; stderr: {result.stderr[-800:]}",
            )
        logger.info("pxdesign CLI OK (--help exit 0)")
    except FileNotFoundError:
        fail_preflight("pxdesign_cli", "pxdesign binary not on PATH")
    except subprocess.TimeoutExpired:
        fail_preflight("pxdesign_cli", "pxdesign --help timed out after 60s")

    # ---- 6. weight presence ----
    required_paths = [
        f"{PXDESIGN_DIR}/tool_weights/af2",
        f"{PXDESIGN_DIR}/tool_weights/mpnn/vanilla_model_weights",
        f"{PXDESIGN_DIR}/tool_weights/mpnn/ca_model_weights",
        f"{PXDESIGN_DIR}/tool_weights/ccd/components.cif",
        f"{PXDESIGN_DIR}/tool_weights/ccd/clusters-by-entity-40.txt",
    ]
    for path in required_paths:
        if not os.path.exists(path):
            fail_preflight("weights", f"missing: {path}")
    logger.info("Weights OK: %d paths verified", len(required_paths))

    # ---- 7. JAX GPU init (fail-fast) in a CLEAN SUBPROCESS ----
    # Critical: AF2-IG runs as a subprocess in production, so JAX/cuDNN
    # must work in a process where torch has NOT been imported (torch 2.3.1
    # bundles cuDNN 8.9 which conflicts with jaxlib 0.4.29's required cuDNN 9
    # when both are dlopen'd in the same process).
    # The driver process here already imported torch above, so run the JAX
    # init as a subprocess — this mirrors the actual AF2-IG invocation.
    jax_probe = (
        "import os, sys, ctypes, glob; "
        # Diagnostic: show cuDNN lib paths JAX can see
        "print('=== cuDNN diagnostic ===', file=sys.stderr); "
        "print('LD_LIBRARY_PATH=', os.environ.get('LD_LIBRARY_PATH',''), file=sys.stderr); "
        "libs = glob.glob('/usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib/libcudnn*.so*'); "
        "print('cuDNN libs:', libs, file=sys.stderr); "
        "import jax, jax.numpy as jnp; "
        "devs = jax.devices(); "
        "print('devs=', devs, file=sys.stderr); "
        "_ = jax.jit(lambda x: x + 1)(jnp.ones(2)).block_until_ready(); "
        "_ = jax.jit(lambda x: jnp.dot(x, x.T))"
        "(jnp.ones((64, 64), dtype=jnp.float32)).block_until_ready(); "
        "gpu_devs = [d for d in devs if d.platform == 'gpu']; "
        "assert gpu_devs, f'no GPU devices: {devs}'; "
        "print('JAX_GPU_OK', jax.__version__, gpu_devs)"
    )
    try:
        # Merge stderr with stdout so we see cuDNN error lines
        result = subprocess.run(
            ["python3", "-c", jax_probe],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "TF_CPP_MIN_LOG_LEVEL": "0"},
        )
        if result.returncode != 0:
            fail_preflight(
                "jax_gpu_init",
                f"JAX GPU subprocess exit {result.returncode}\n"
                f"=== STDOUT (full) ===\n{result.stdout}\n"
                f"=== STDERR (last 4000) ===\n{result.stderr[-4000:]}",
            )
        logger.info("JAX GPU subprocess OK: stdout=%s stderr_tail=%s",
                    result.stdout.strip(), result.stderr[-500:].strip())
    except subprocess.TimeoutExpired:
        fail_preflight("jax_gpu_init", "JAX GPU subprocess timed out after 180s")

    logger.info("=== PREFLIGHT DONE in %.1fs ===", time.time() - t0)


# ===========================================================================
# Heartbeat / webhook (unchanged legacy helpers for tier=webhook path)
# ===========================================================================

def send_heartbeat(
    webhook_url: str,
    job_id: str,
    stage: str,
    designs_completed: int = 0,
    designs_total: int = 0,
    new_candidate: dict | None = None,
) -> None:
    """Send a heartbeat to the Kendrew backend (webhook tier).

    new_candidate is an optional per-design candidate dict for live UI
    streaming. tools-hub gates it server-side via JOB_TOKEN and projects
    it to a fixed schema, so a malformed candidate is dropped silently.
    """
    if not webhook_url:
        return
    parsed = urlparse(webhook_url)
    heartbeat_url = urlunparse(parsed._replace(path="/webhooks/heartbeat"))
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
        # Supabase tool-outputs bucket allowed_mime_types (migration
        # 0021) accepts text/plain but not text/csv, so a "text/csv"
        # PUT rejects with HTTP 400. Tag CSV as text/plain — the bytes
        # are identical and downstream consumers don't dispatch on MIME.
        content_type = "text/plain"
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
    """Run a subprocess command with timeout, streaming stdout/stderr live.

    PXDesign's ``pipeline`` subcommand can block silently for 10-20 min inside
    JAX JIT compile + AF2 first forward pass. With ``capture_output=True`` we
    would see nothing in Modal logs until the subprocess exits or times out,
    which makes triage impossible. Stream the merged output line-by-line to
    our stderr so Modal logs show live progress.

    A ring buffer retains the last 4000 chars for the RuntimeError message.
    """
    from collections import deque

    logger.info("Running: %s", " ".join(cmd[:8]) + ("..." if len(cmd) > 8 else ""))
    start = time.time()

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,  # line-buffered
    )

    tail: "deque[str]" = deque(maxlen=400)  # ~40k chars max
    deadline = start + timeout

    try:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            if time.time() > deadline:
                proc.kill()
                proc.wait(timeout=30)
                raise subprocess.TimeoutExpired(cmd, timeout)
            tail.append(line)
            # Stream to stderr so Modal captures it immediately.
            sys.stderr.write(line)
            sys.stderr.flush()
        proc.stdout.close()
        returncode = proc.wait(timeout=max(1, int(deadline - time.time())))
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        # Keep both head and tail so the actual traceback survives even when
        # the trailing buffer is filled with chatty progress-bar updates.
        # The 2026-04-28 mini_pilot FAIL surfaced 50+ identical "78.5%" lines
        # in detail[-2000:], which pushed the real exception line off the end.
        full = "".join(tail)
        head_tail = (
            full[:1500] + "\n... [truncated] ...\n" + full[-1500:]
            if len(full) > 3000 else full
        )
        logger.error(
            "Command TIMED OUT after %.1fs. Output head+tail:\n%s",
            elapsed, head_tail,
        )
        raise RuntimeError(
            f"Command timed out after {timeout}s: {head_tail[-2000:]}"
        )

    elapsed = time.time() - start
    full = "".join(tail)
    head_tail = (
        full[:1500] + "\n... [truncated] ...\n" + full[-1500:]
        if len(full) > 3000 else full
    )
    logger.info(
        "Command finished in %.1fs (exit code %d).",
        elapsed, returncode,
    )

    if returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {returncode}): {head_tail[-2000:]}"
        )
    return head_tail


_WEBHOOK_OUTCOME_PATH = "/tmp/webhook_outcome.json"


def _record_webhook_outcome(delivered: bool, detail: str) -> None:
    """Persist webhook delivery status so the Modal wrapper can surface
    it to the consuming web service even when the POST silently fails. Read by run_tool()
    in infrastructure/modal/pxdesign_app.py and merged into the function
    return value, where the web service's poller inspects it."""
    try:
        with open(_WEBHOOK_OUTCOME_PATH, "w") as fh:
            json.dump({"delivered": delivered, "detail": detail}, fh)
    except OSError as exc:
        logger.error("Failed to write webhook outcome file: %s", exc)


def post_webhook(
    webhook_url: str, job_id: str, pod_id: str, payload: dict,
) -> None:
    """POST results to the Kendrew backend webhook (webhook tier)."""
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
    """Convert the downloaded PDB into a PXDesign-ready mmCIF.

    Returns a tuple ``(cif_path, renumber_map)``. The renumber_map is
    ``{(chain_id, original_resnum): new_resnum}`` produced by the
    Biopython normalizer, which residues are renumbered 1..N per chain.
    Callers (``build_yaml_spec``) use it to rewrite hotspot indices into
    the cleaned coordinate space — without this, user-supplied hotspots
    silently point at the wrong residues for any input whose chain A
    doesn't start at residue 1.

    Pipeline (Bug 9 fix, 2026-04-30):
      1. Sanitize with Biopython (``pipeline_normalize.normalize_for_pxdesign``):
         drop waters, HETATM, hydrogens, altlocs, multi-model, MSE->MET,
         filter to ``target_chain`` only, renumber 1..N. Result is a clean
         single-chain PDB on disk.
      2. Read that cleaned PDB with gemmi for the custom CIF write below.
         ``setup_entities()`` is called defensively before any cleanup —
         a no-op on a polymer-only structure but protects against gemmi
         API drift.
      3. Write the custom mmCIF (label_asym_id == auth_asym_id ==
         chain_name) so PXDesign's CIF reader can find the chain by name.
    """
    from pipeline_normalize import normalize_for_pxdesign  # noqa: PLC0415

    import gemmi  # noqa: PLC0415
    from gemmi import cif  # noqa: PLC0415

    # ---- Stage 1: Biopython sanitize + renumber ----
    cleaned_pdb = os.path.join(work_dir, "cleaned.pdb")
    norm_report = normalize_for_pxdesign(
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

    # ---- Stage 2: gemmi CIF write (now operating on a clean polymer-only PDB) ----
    structure = gemmi.read_structure(cleaned_pdb)
    # Defensive: setup_entities derives _entity / _entity_poly metadata from
    # SEQRES + heuristics. On a polymer-only structure this is a no-op for
    # remove_ligands_and_waters but inexpensive insurance against gemmi
    # version drift if the cleanup-call ordering ever changes again.
    try:
        structure.setup_entities()
    except Exception as exc:  # pragma: no cover
        logger.warning("setup_entities() raised %s; continuing", exc)
    structure.remove_alternative_conformations()
    structure.remove_hydrogens()
    # remove_ligands_and_waters is now safe — the input is polymer-only —
    # but kept as defense in depth.
    try:
        structure.remove_ligands_and_waters()
    except Exception as exc:
        logger.warning(
            "remove_ligands_and_waters() raised %s post-normalize; continuing",
            exc,
        )
    structure.remove_empty_chains()

    # The MODRES_MAP and STANDARD_AA filter below is now mostly redundant
    # (Biopython already applied them) but kept as belt-and-braces in case
    # anything slipped through gemmi's own parser. The loop is cheap.
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

    chains_data: dict[str, list[dict]] = {}
    modres_renames = 0
    dropped_counts: dict[str, int] = {}

    for model in structure:
        for chain in model:
            chain_residues: list[dict] = []
            for residue in chain:
                name = residue.name
                atom_rename: dict = {}
                if name in MODRES_MAP:
                    new_name, atom_fixes = MODRES_MAP[name]
                    name = new_name
                    atom_rename = atom_fixes
                    modres_renames += 1
                if name not in STANDARD_AA:
                    dropped_counts[residue.name] = dropped_counts.get(residue.name, 0) + 1
                    continue
                atoms = []
                for atom in residue:
                    atom_name = atom.name
                    atom_elem = atom.element.name
                    if atom_name in atom_rename:
                        atom_name, atom_elem = atom_rename[atom_name]
                    if atom_elem == "H":
                        continue
                    atoms.append((
                        atom_name, atom_elem,
                        atom.pos.x, atom.pos.y, atom.pos.z,
                    ))
                if atoms:
                    chain_residues.append({"resname": name, "atoms": atoms})
            if chain_residues:
                chains_data.setdefault(chain.name, []).extend(chain_residues)
        break  # first model only

    if not chains_data:
        raise RuntimeError("No standard polymer residues survived cleanup")

    for chain_name, residues in chains_data.items():
        for idx, res in enumerate(residues, start=1):
            res["resnum"] = idx

    kept_counts = {c: len(r) for c, r in chains_data.items()}
    logger.info(
        "CIF prep: modres_renames=%d, kept_per_chain=%s, dropped=%s",
        modres_renames, kept_counts, dropped_counts,
    )

    doc = cif.Document()
    block = doc.add_new_block("target")

    chain_names = list(chains_data.keys())
    chain_to_entity = {name: str(i + 1) for i, name in enumerate(chain_names)}

    e_loop = block.init_loop("_entity.", ["id", "type"])
    for name in chain_names:
        e_loop.add_row([chain_to_entity[name], "polymer"])

    ep_loop = block.init_loop("_entity_poly.", ["entity_id", "type"])
    for name in chain_names:
        ep_loop.add_row([chain_to_entity[name], "polypeptide(L)"])

    eps_loop = block.init_loop(
        "_entity_poly_seq.", ["entity_id", "num", "mon_id"],
    )
    for name in chain_names:
        eid = chain_to_entity[name]
        for res in chains_data[name]:
            eps_loop.add_row([eid, str(res["resnum"]), res["resname"]])

    sa_loop = block.init_loop("_struct_asym.", ["id", "entity_id"])
    for name in chain_names:
        sa_loop.add_row([name, chain_to_entity[name]])

    as_loop = block.init_loop("_atom_site.", [
        "group_PDB", "id", "type_symbol", "label_atom_id",
        "label_alt_id", "label_comp_id", "label_asym_id",
        "label_entity_id", "label_seq_id",
        "pdbx_PDB_ins_code",
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
                    "?",
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


def get_chain_length(cif_path: str, chain_id: str) -> int:
    """Count residues in a specific chain of a CIF file."""
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
    job_spec: dict, target_cif_path: str, preset: str = "preview",
    num_designs: int | None = None, binder_length=None,
    renumber_map: dict | None = None,
) -> dict:
    """Build PXDesign YAML task spec from job parameters.

    Reads the target CIF to determine chain length for the crop range.
    PXDesign requires crop as a list of string ranges, e.g. ["1-116"].

    Hotspot remapping (Bug 9 fix): the CIF prep stage renumbers residues
    1..N per chain. User-supplied hotspots refer to original PDB
    numbering. ``renumber_map`` is ``{(chain, orig_resnum): new_resnum}``
    produced by ``ensure_cif()``. We use it to rewrite each hotspot into
    the new coordinate space before handing it to PXDesign. Hotspots
    that fall outside the kept range (e.g. on a residue that was dropped
    as non-standard) are logged and skipped.
    """
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    raw_hotspots = list(job_spec.get("hotspot_residues", []) or [])

    if binder_length is None:
        binder_length = params.get("binder_length", 80)
    if num_designs is None:
        num_designs = params.get("num_designs", 100)

    chain_length = get_chain_length(target_cif_path, chain)
    logger.info("Target chain %s has %d residues", chain, chain_length)

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

    chain_spec = {
        "crop": [f"1-{chain_length}"],
        "hotspots": hotspots if hotspots else [],
    }

    yaml_spec = {
        "target": {
            "file": target_cif_path,
            "chains": {chain: chain_spec},
        },
        "binder_length": binder_length,
        "preset": preset,
        "N_sample": num_designs,
    }

    logger.info(
        "YAML spec: chain=%s, crop=[1-%d], hotspots(orig=%s, mapped=%s), "
        "binder_length=%s, N_sample=%d, preset=%s",
        chain, chain_length, raw_hotspots, hotspots,
        binder_length, num_designs, preset,
    )
    return yaml_spec


# ===========================================================================
# Result parsing
# ===========================================================================

def _safe_float(value: str, default: float) -> float:
    """Parse a float from a CSV value, returning default on failure."""
    if not value or not str(value).strip():
        return default
    try:
        parsed = float(value)
        if parsed != parsed:  # NaN check
            return default
        return parsed
    except (ValueError, TypeError):
        return default


def parse_summary_csv(csv_path: str) -> list[dict]:
    """Parse PXDesign summary.csv into a list of candidate dicts."""
    if not os.path.exists(csv_path):
        logger.warning("summary.csv not found at %s", csv_path)
        return []

    results = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        logger.info("summary.csv columns: %s", columns)

        for row in reader:
            row_lower = {k.lower().strip(): v for k, v in row.items()}

            design_name = (
                row_lower.get("design_name")
                or row_lower.get("name")
                or row_lower.get("sample")
                or f"design_{len(results)}"
            )

            scores: dict = {}
            for metric, keys in [
                ("ipTM", ["af2_iptm", "af2_ip_tm", "iptm", "iptm_score", "ip_tm"]),
                ("pLDDT", ["af2_plddt", "af2_mean_plddt", "plddt", "mean_plddt"]),
                # Prefer unscaled (Angstrom-scale) pAE over the [0,1] normalized
                # form that PXDesign reports as af2_pae / af2_ipae.
                ("pAE", [
                    "unscaled_i_pae", "unscaled_ipae", "unscaled_pae",
                    "af2_unscaled_ipae", "af2_unscaled_i_pae",
                    "af2_ipae", "af2_pae", "ipae", "pae", "i_pae", "mean_pae",
                ]),
            ]:
                for key in keys:
                    if key in row_lower and row_lower[key]:
                        scores[metric] = _safe_float(
                            row_lower[key],
                            0.0 if metric != "pAE" else 99.0,
                        )
                        break

            # PXDesign reports pLDDT on the [0,1] scale; SMOKE-TEST-SPEC and
            # every downstream consumer expects [0,100]. Only scale when the
            # value looks normalized (0 <= v <= 1) to avoid double-scaling if
            # a future CSV version ships [0,100] natively.
            if "pLDDT" in scores and 0.0 <= scores["pLDDT"] <= 1.0:
                scores["pLDDT"] = scores["pLDDT"] * 100.0

            filter_status = "unknown"
            for key in ["af2-ig-success", "af2-ig-easy-success",
                        "filter_status", "status", "pass"]:
                if key in row_lower and row_lower[key]:
                    val = str(row_lower[key]).strip().lower()
                    if val in ("true", "1", "yes", "pass", "passed"):
                        filter_status = "pass"
                    elif val in ("false", "0", "no", "fail", "failed"):
                        filter_status = "fail"
                    else:
                        filter_status = val
                    break
            scores["filter_status"] = filter_status

            # PXDesign's summary.csv ships a chosen_struct_path column
            # pointing directly at the produced PDB/CIF. Capture it so the
            # downstream candidate loop can resolve local_file even when
            # the CSV has no design_name/name/sample column to match
            # against the design_files glob index (which fails the
            # substring match for the synthetic "design_N" fallback name).
            chosen_path = (
                row_lower.get("chosen_struct_path")
                or row_lower.get("chosen_struct_path_relative")
                or ""
            ).strip()

            results.append({
                "design_name": design_name,
                "scores": scores,
                "chosen_struct_path": chosen_path,
            })

    logger.info("Parsed %d entries from summary.csv", len(results))
    return results


def find_design_files(output_dir: str) -> dict[str, str]:
    """Map design names to their PDB or CIF file paths in the output directory.

    PXDesign's observed output layout is:
        <output_dir>/global_run_0/<spec_name>/seed_<NNN>/predictions/converted_pdbs/<name>.pdb
    Documented layouts also use passing-AF2-IG-easy/ and passing-AF2-IG/ subdirs.
    Summary.csv may reference designs by stem (e.g. ``spec_sample_0``) or by
    basename without extension. We index both forms.
    """
    design_files: dict[str, str] = {}

    # Layer 1: documented PXDesign passing-* subdirs + orig_designed.
    # Note passing-AF2-IG-easy is the primary key per PXDesign README.
    for subdir in ["passing-AF2-IG-easy", "passing-AF2-IG", "orig_designed"]:
        candidate_dir = os.path.join(output_dir, subdir)
        if os.path.isdir(candidate_dir):
            for ext in ("*.pdb", "*.cif"):
                for path in Path(candidate_dir).rglob(ext):
                    design_files.setdefault(path.stem, str(path))
                    design_files.setdefault(path.name, str(path))

    # Layer 2: any converted_pdbs directory, observed live in
    # global_run_0/<spec>/seed_<NNN>/predictions/converted_pdbs/spec_sample_<M>.pdb.
    for pred_dir in Path(output_dir).rglob("converted_pdbs"):
        for path in pred_dir.rglob("*.pdb"):
            design_files.setdefault(path.stem, str(path))
            design_files.setdefault(path.name, str(path))
        for path in pred_dir.rglob("*.cif"):
            design_files.setdefault(path.stem, str(path))
            design_files.setdefault(path.name, str(path))

    # Layer 3: last-ditch recursive fallback if nothing above hit.
    if not design_files:
        for ext in ("*.pdb", "*.cif"):
            for path in Path(output_dir).rglob(ext):
                design_files.setdefault(path.stem, str(path))
                design_files.setdefault(path.name, str(path))

    logger.info(
        "Found %d design structure file keys in %s (sample=%s)",
        len(design_files), output_dir, list(design_files.keys())[:6],
    )
    return design_files


def resolve_design_local_path(
    *,
    design_name: str,
    rank_idx: int,
    design_files: dict[str, str],
    chosen_struct_path: str = "",
    output_dir: str = "",
    summary_csv_dir: str = "",
) -> tuple[str | None, str]:
    """Resolve the on-disk PDB path for a single passing design.

    The "preview" preset emits a summary.csv that lacks design_name /
    name / sample columns; parse_summary_csv synthesizes "design_N"
    labels in that case which do not match anything in design_files.
    chosen_struct_path may be relative to the summary.csv directory or
    the output_dir, so we try several bases before falling through to
    the design_files glob index. Returns (path, source_tag) — tag
    identifies which layer matched, for log triage.
    """
    cp = (chosen_struct_path or "").strip()
    if cp:
        if os.path.isabs(cp) and os.path.exists(cp):
            return cp, "chosen_struct_path:abs"
        if os.path.exists(cp):
            return cp, "chosen_struct_path:cwd"
        for base, tag in (
            (output_dir, "output_dir"),
            (summary_csv_dir, "summary_csv_dir"),
        ):
            if base:
                joined = os.path.normpath(os.path.join(base, cp))
                if os.path.exists(joined):
                    return joined, f"chosen_struct_path:rel_{tag}"
        if output_dir:
            basename = os.path.basename(cp)
            if basename:
                matches = list(Path(output_dir).rglob(basename))
                if matches:
                    return str(matches[0]), f"chosen_struct_path:rglob_{basename}"

    if design_name in design_files:
        return design_files[design_name], "design_files:direct"
    stem = Path(design_name).stem
    if stem in design_files:
        return design_files[stem], "design_files:stem"
    for key, fpath in design_files.items():
        if design_name in key or key in design_name:
            return fpath, f"design_files:substring({key})"

    sample_key = f"spec_sample_{rank_idx}"
    if sample_key in design_files:
        return design_files[sample_key], f"design_files:{sample_key}"
    sorted_keys = sorted(
        (k for k in design_files if k.startswith("spec_sample_")),
        key=lambda s: int(s.rsplit("_", 1)[-1])
        if s.rsplit("_", 1)[-1].isdigit() else 9999,
    )
    if rank_idx < len(sorted_keys):
        return (
            design_files[sorted_keys[rank_idx]],
            f"design_files:sorted_spec_sample[{rank_idx}]",
        )

    return None, "no_match"


def locate_summary_csv(output_dir: str) -> str | None:
    """Find summary.csv anywhere under output_dir."""
    for candidate in (
        os.path.join(output_dir, "summary.csv"),
        os.path.join(output_dir, "results", "summary.csv"),
    ):
        if os.path.exists(candidate):
            return candidate
    matches = list(Path(output_dir).rglob("summary.csv"))
    return str(matches[0]) if matches else None


# ===========================================================================
# Pipeline execution helpers
# ===========================================================================

def validate_input(spec_path: str) -> None:
    """Run pxdesign check-input to validate the YAML spec before design."""
    logger.info("Validating PXDesign input spec: %s", spec_path)
    try:
        run_command(
            ["pxdesign", "check-input", "--yaml", spec_path],
            timeout=180,
        )
        logger.info("Input validation passed")
    except FileNotFoundError:
        run_command(
            ["python3", "-m", "pxdesign", "check-input", "--yaml", spec_path],
            timeout=180,
        )
        logger.info("Input validation passed (python -m fallback)")


def run_pxdesign(
    spec_path: str,
    output_dir: str,
    num_designs: int,
    preset: str = "preview",
    timeout: int = 5400,
    tier: str = "",
) -> None:
    """Run the PXDesign pipeline.

    Tries the pxdesign CLI first; falls back to python -m pxdesign.
    """
    cmd = [
        "pxdesign", "pipeline",
        "--preset", preset,
        "-i", spec_path,
        "-o", output_dir,
        "--N_sample", str(num_designs),
        "--dtype", "bf16",
    ]
    # Deterministic seed for smoke + mini_pilot so validation runs are
    # reproducible. Pilot tier draws a fresh seed every run for diversity
    # across caller submissions. If upstream PXDesign rejects --seed,
    # remove this branch — the pipeline ran for months without it.
    if tier in ("smoke", "mini_pilot"):
        cmd.extend(["--seed", "42"])
    try:
        run_command(cmd, timeout=timeout, cwd=PXDESIGN_DIR)
    except FileNotFoundError:
        logger.warning("pxdesign CLI not found, trying python -m fallback")
        fallback_cmd = ["python3", "-m", "pxdesign", "pipeline"] + cmd[2:]
        run_command(fallback_cmd, timeout=timeout, cwd=PXDESIGN_DIR)


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
# Smoke / mini_pilot main
# ===========================================================================

def _resolve_preset_params(tier: str, job_spec: dict) -> dict:
    """Merge caller params with the tier's preset."""
    if tier == "smoke":
        preset = smoke_preset()
    elif tier == "mini_pilot":
        preset = mini_pilot_preset()
    else:
        return job_spec.get("parameters", {})
    caller = dict(job_spec.get("parameters", {}))
    caller.update(preset)
    return caller


def _candidate_from_design(
    rank: int, design_name: str, local_path: str | None, scores: dict,
) -> dict:
    """Build a candidate dict compliant with SMOKE-TEST-SPEC.md."""
    if local_path and os.path.exists(local_path):
        content_b64 = base64.b64encode(Path(local_path).read_bytes()).decode()
        ext = Path(local_path).suffix
    else:
        content_b64 = ""
        ext = ".pdb"
    return {
        "rank": rank,
        "pdb_key": f"design_{rank:03d}{ext}",
        "pdb_content_b64": content_b64,
        "scores": scores,
    }


def run_smoke_or_mini_pilot(tier: str, job_payload: dict) -> None:
    """Run PXDesign in smoke or mini_pilot tier and serialize results.

    Writes /tmp/smoke_results.json per docs/SMOKE-TEST-SPEC.md layer-3 shape.
    Always exits 0 on completion (success OR handled failure serialized).
    """
    logger.info("=== TIER=%s START ===", tier)
    pipeline_start = time.time()
    job_spec = job_payload.get("job_spec", {}) or {}
    input_url = job_payload.get("input_pdb_url", "") or ""

    preset_params = _resolve_preset_params(tier, job_spec)
    preset = preset_params.get("preset", "basic")
    num_designs = int(preset_params.get("num_designs", 1))
    binder_length = preset_params.get("binder_length", 80)

    work_dir = tempfile.mkdtemp(prefix=f"pxdesign_{tier}_")
    output_dir = os.path.join(work_dir, "pxdesign_output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        # ----- Resolve target PDB (fixture or URL) -----
        if input_url:
            target_input = os.path.join(work_dir, "target_input.pdb")
            download_input(input_url, target_input)
        else:
            target_input = os.path.join(work_dir, "target_input.pdb")
            shutil.copyfile(SMOKE_TARGET_PATH, target_input)
            logger.info("Using baked smoke fixture %s", SMOKE_TARGET_PATH)

        # ----- Convert to CIF -----
        try:
            target_chain = job_spec.get("target_chain", "A")
            target_cif, renumber_map = ensure_cif(
                target_input, work_dir, target_chain=target_chain,
            )
        except Exception as exc:
            fail_compute("cif_conversion", f"{exc}\n{traceback.format_exc()}")
            return

        # ----- YAML spec -----
        try:
            yaml_spec = build_yaml_spec(
                job_spec, target_cif,
                preset=preset,
                num_designs=num_designs,
                binder_length=binder_length,
                renumber_map=renumber_map,
            )
        except Exception as exc:
            fail_compute("yaml_build", f"{exc}\n{traceback.format_exc()}")
            return

        spec_path = os.path.join(work_dir, "spec.yaml")
        with open(spec_path, "w") as fh:
            yaml.dump(yaml_spec, fh, default_flow_style=False)
        logger.info("YAML spec written to %s", spec_path)
        with open(spec_path) as fh:
            logger.info("Spec contents:\n%s", fh.read())

        # ----- Validate input -----
        try:
            validate_input(spec_path)
        except Exception as exc:
            fail_compute("check_input", f"{exc}\n{traceback.format_exc()}")
            return

        # ----- Run PXDesign -----
        # First-run JAX JIT + AF2 compile alone takes 10-15 min on A100-80.
        # Smoke (N=1) completes in ~17 min wall / ~1000 gpu_seconds on a cold
        # container, so 1700s leaves ~12 min of headroom. Mini_pilot (N=1) and
        # pilot (caller target) can take longer when the Protenix DDIM sampler
        # hits an unfortunate seed (see VALIDATION-LOG.md 2026-04-28 mini_pilot
        # FAIL — hung at step 157/200 of AF2-IG diffusion at 75 min wallclock).
        # Bumped 4500 -> 5200 (2026-04-29) to leave 200s headroom under the
        # web service's Modal-level 5400s wait cap; matches the agent investigation
        # recommendation. When upstream PXDesign / ColabDesign are pinned to
        # the 2026-04-22 known-good SHAs (Dockerfile.modal lines 30, 35), the
        # 4500s envelope was sufficient — the bump compensates for upstream
        # drift until the pin is restored.
        timeout_s = 1700 if tier == "smoke" else 5200
        try:
            run_pxdesign(
                spec_path, output_dir, num_designs,
                preset=preset, timeout=timeout_s, tier=tier,
            )
        except Exception as exc:
            # Log output tree to aid triage
            try:
                for root, _dirs, files in os.walk(output_dir):
                    for fname in files:
                        logger.info(
                            "Output file: %s",
                            os.path.relpath(os.path.join(root, fname), output_dir),
                        )
            except Exception:
                pass
            fail_compute("pxdesign_run", f"{exc}\n{traceback.format_exc()}")
            return

        # ----- Log output tree for debugging -----
        for root, _dirs, files in os.walk(output_dir):
            rel_root = os.path.relpath(root, output_dir)
            for fname in files:
                logger.info("Output file: %s/%s", rel_root, fname)

        # ----- Parse results -----
        summary_csv = locate_summary_csv(output_dir)
        if summary_csv is None:
            all_files = [str(p.name) for p in Path(output_dir).rglob("*")][:80]
            fail_compute(
                "missing_summary_csv",
                f"no summary.csv under {output_dir}; files={all_files}",
            )
            return

        parsed_results = parse_summary_csv(summary_csv)
        design_files = find_design_files(output_dir)

        if not parsed_results:
            fail_compute(
                "empty_summary",
                f"summary.csv at {summary_csv} had zero rows",
            )
            return

        # ----- Rank by ipTM descending -----
        parsed_results.sort(
            key=lambda r: r["scores"].get("ipTM", 0.0), reverse=True,
        )

        # Match requested N (or all if fewer were produced)
        top_results = parsed_results[:num_designs]

        # ----- Assemble candidates with inline base64 PDBs -----
        candidates: list[dict] = []
        for rank_idx, result in enumerate(top_results):
            rank = rank_idx + 1
            design_name = result["design_name"]

            # Prefer chosen_struct_path from the CSV when present — most
            # authoritative path to the structure file.
            chosen_path = (result.get("chosen_struct_path") or "").strip()
            local_path = chosen_path if chosen_path and os.path.exists(chosen_path) else None
            if not local_path:
                local_path = design_files.get(design_name)
            if not local_path:
                # Try stem-based matching.
                local_path = design_files.get(Path(design_name).stem)
            if not local_path:
                # Substring match both directions.
                for key, fpath in design_files.items():
                    if design_name in key or key in design_name:
                        local_path = fpath
                        break
            if not local_path:
                # PXDesign fallback: summary rows may be named by spec/seed; the
                # actual PDB is spec_sample_<rank_idx>.pdb in converted_pdbs/.
                # Try rank-indexed fallback on spec_sample_<N>.
                sample_key = f"spec_sample_{rank_idx}"
                local_path = design_files.get(sample_key)
                if not local_path:
                    # Try any .pdb whose stem starts with "spec_sample_"
                    candidates_sample = sorted(
                        (k for k in design_files if k.startswith("spec_sample_")),
                        key=lambda s: int(s.rsplit("_", 1)[-1])
                        if s.rsplit("_", 1)[-1].isdigit() else 9999,
                    )
                    if rank_idx < len(candidates_sample):
                        local_path = design_files[candidates_sample[rank_idx]]

            if not local_path:
                logger.warning(
                    "No structure file for design %s; available=%s",
                    design_name, list(design_files.keys())[:10],
                )

            # If CIF only, try to sibling .pdb or leave CIF (spec says pdb_content_b64;
            # we still base64 whatever structural output exists — orchestrator's
            # Bio.PDB.PDBParser may need a PDB; convert CIF -> PDB via gemmi.
            final_path = local_path
            if local_path and local_path.endswith(".cif"):
                try:
                    import gemmi
                    pdb_out = os.path.join(work_dir, f"design_{rank:03d}.pdb")
                    gemmi.read_structure(local_path).write_pdb(pdb_out)
                    final_path = pdb_out
                    logger.info("Converted %s -> %s", local_path, pdb_out)
                except Exception as exc:
                    logger.warning("CIF->PDB convert failed (%s); keeping CIF", exc)

            candidates.append(_candidate_from_design(
                rank=rank,
                design_name=design_name,
                local_path=final_path,
                scores=result["scores"],
            ))

        # ----- Sanity: required shape per SMOKE-TEST-SPEC.md -----
        if tier == "mini_pilot":
            for c in candidates:
                for k in ("ipTM", "pLDDT"):
                    v = c["scores"].get(k)
                    if not isinstance(v, (int, float)) or v != v or v == 0.0:
                        logger.warning(
                            "mini_pilot candidate rank=%d has suspect %s=%r",
                            c["rank"], k, v,
                        )

        gpu_seconds = int(time.time() - pipeline_start)
        write_smoke_result({
            "status": "COMPLETED",
            "output": {"candidates": candidates},
            "tier": tier,
            "gpu_seconds": gpu_seconds,
        })
        logger.info(
            "=== TIER=%s DONE: %d candidates in %ds ===",
            tier, len(candidates), gpu_seconds,
        )

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ===========================================================================
# Legacy webhook-tier main
# ===========================================================================

def startup_check_legacy() -> dict:
    """Validate env vars for webhook tier only."""
    checks: dict = {}
    required_vars = ["WEBHOOK_URL", "JOB_ID", "JOB_PAYLOAD"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    try:
        import torch
        checks["torch"] = torch.__version__
        checks["cuda_available"] = torch.cuda.is_available()
        if not torch.cuda.is_available():
            logger.error("CUDA not available")
            sys.exit(1)
    except ImportError:
        logger.error("PyTorch is not installed.")
        sys.exit(1)
    return checks


def run_webhook_tier(job_payload: dict) -> None:
    """Legacy RunPod pilot path: presigned I/O + webhook."""
    startup_check_legacy()
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))
    job_token = os.environ.get("JOB_TOKEN", "")

    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")

    num_designs = job_spec.get("parameters", {}).get("num_designs", 100)
    pipeline_start = time.time()

    work_dir = tempfile.mkdtemp(prefix="pxdesign_job_")
    output_dir = os.path.join(work_dir, "pxdesign_output")
    os.makedirs(output_dir, exist_ok=True)

    input_ext = ".pdb"
    if urlparse(input_url).path.endswith(".cif"):
        input_ext = ".cif"
    target_input = os.path.join(work_dir, f"target_input{input_ext}")

    try:
        download_input(input_url, target_input)
        send_heartbeat(webhook_url, job_id, "Input downloaded", 0, num_designs)

        target_chain = job_spec.get("target_chain", "A")
        target_cif, renumber_map = ensure_cif(
            target_input, work_dir, target_chain=target_chain,
        )

        yaml_spec = build_yaml_spec(
            job_spec, target_cif,
            preset="preview",
            num_designs=num_designs,
            renumber_map=renumber_map,
        )
        spec_path = os.path.join(work_dir, "spec.yaml")
        with open(spec_path, "w") as fh:
            yaml.dump(yaml_spec, fh, default_flow_style=False)

        validate_input(spec_path)

        send_heartbeat(webhook_url, job_id, "Running PXDesign", 0, num_designs)
        heartbeat_stop = threading.Event()

        def _keepalive() -> None:
            while not heartbeat_stop.wait(300):
                try:
                    send_heartbeat(
                        webhook_url, job_id,
                        "Running PXDesign", 0, num_designs,
                    )
                except Exception as exc:  # pragma: no cover
                    logger.warning("keepalive heartbeat failed: %s", exc)

        keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
        keepalive_thread.start()
        try:
            run_pxdesign(
                spec_path, output_dir, num_designs,
                preset="preview", timeout=5400,
            )
        finally:
            heartbeat_stop.set()

        send_heartbeat(webhook_url, job_id, "PXDesign complete", num_designs, num_designs)

        summary_csv = locate_summary_csv(output_dir)
        if summary_csv is None:
            post_webhook(webhook_url, job_id, pod_id, {
                "error": "PXDesign produced no summary.csv",
            })
            return

        parsed_results = parse_summary_csv(summary_csv)
        design_files = find_design_files(output_dir)

        tier = (job_payload.get("tier") or "").strip().lower()

        passing = []
        for result in parsed_results:
            scores = result["scores"]
            iptm = scores.get("ipTM", 0.0)
            plddt = scores.get("pLDDT", 0.0)
            pae = scores.get("pAE", 99.0)

            pxdesign_passed = scores.get("filter_status", "") == "pass"
            threshold_passed = (
                iptm >= IPTM_THRESHOLD
                and plddt >= PLDDT_THRESHOLD
                and pae <= PAE_THRESHOLD
            )
            if pxdesign_passed or threshold_passed:
                passing.append(result)

        passing.sort(key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True)

        # Pilot fallback: when no design clears the production threshold,
        # surface the top-ipTM rows with filter_status="below threshold" so
        # users see the score distribution rather than an empty candidates
        # table. Mirrors rfdiffusion / rfantibody / boltzgen pilot fallback
        # (Kendrew commit d19080c). Only fires at pilot tier — sprint/full
        # keep strict filtering since deeper sampling makes a true zero a
        # real signal.
        if not passing and tier == "pilot" and parsed_results:
            parsed_results.sort(
                key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True,
            )
            passing = parsed_results[:2]
            for r in passing:
                r["scores"]["filter_status"] = "below threshold"
            logger.info(
                "Pilot fallback: 0/%d designs cleared thresholds; surfacing "
                "top %d by ipTM with filter_status='below threshold'",
                len(parsed_results), len(passing),
            )

        candidates = []
        filenames_to_upload = []
        summary_csv_dir = os.path.dirname(summary_csv) if summary_csv else ""
        for rank_idx, result in enumerate(passing):
            rank = rank_idx + 1
            design_name = result["design_name"]
            local_path, resolved_via = resolve_design_local_path(
                design_name=design_name,
                rank_idx=rank_idx,
                design_files=design_files,
                chosen_struct_path=result.get("chosen_struct_path", ""),
                output_dir=output_dir,
                summary_csv_dir=summary_csv_dir,
            )
            logger.info(
                "webhook tier: rank=%d design=%s -> %s (resolved via %s)",
                rank, design_name, local_path, resolved_via,
            )
            ext = Path(local_path).suffix if local_path else ".pdb"
            upload_filename = f"design_{rank:03d}{ext}"
            filenames_to_upload.append(upload_filename)
            # pdb_key MUST share basename with upload_filename so the
            # web service's resolver can find the Storage object at
            # {user}/{job}/designs/<basename>. Using the original
            # design_name here created a permanent 404 because the
            # web service uploads as design_{rank:03d}.{pdb,cif} while
            # design_name is the PXDesign internal label ("design_0",
            # "design_run3-1", etc). The contracts module
            # (/opt/contracts/rpc.py) defines the upload-URL exchange
            # shape consumed by the web service.
            candidates.append({
                "rank": rank,
                "pdb_key": f"designs/{upload_filename}",
                "scores": result["scores"],
                "local_file": local_path,
                "upload_filename": upload_filename,
            })

            # Emit per-candidate heartbeat for live UI streaming. PXDesign
            # scores expose ipTM / pLDDT / pAE (pAE is the interaction PAE
            # in this tool's parser; mapped to the contract field i_pae).
            # filter_status comes from the row (set by parse_summary_csv
            # or the pilot fallback above); designs that cleared the pass
            # gate without an explicit status default to "pass".
            try:
                scores_d = result.get("scores", {}) or {}
                iptm_v = scores_d.get("ipTM", scores_d.get("iptm"))
                plddt_v = scores_d.get("pLDDT", scores_d.get("plddt"))
                ipae_v = scores_d.get(
                    "pAE",
                    scores_d.get("i_pAE", scores_d.get("ipae")),
                )
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
                    webhook_url, job_id, "Ranking candidates",
                    rank, len(passing),
                    new_candidate=new_cand,
                )
        if filenames_to_upload:
            filenames_to_upload.append("metrics.csv")

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
            local_file = candidate.get("local_file")
            if upload_filename in upload_urls and local_file and os.path.exists(local_file):
                try:
                    upload_output(upload_urls[upload_filename], local_file)
                except RuntimeError as exc:
                    logger.warning("Upload failed: %s", exc)

        if candidates:
            metrics_csv_path = os.path.join(work_dir, "metrics.csv")
            write_metrics_csv(metrics_csv_path, candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], metrics_csv_path)
                except RuntimeError as exc:
                    logger.warning("Metrics CSV upload failed: %s", exc)

        elapsed_minutes = (time.time() - pipeline_start) / 60.0
        # Inline base64 of each candidate's PDB so candidate_table.html can
        # render the 3D-viewer + PDB-download buttons (otherwise it falls
        # through to the em-dash branch keyed on pdb_content_b64). Mirrors
        # the smoke path's _candidate_from_design helper at line 1045.
        # PXDesign emits .cif or .pdb depending on output config — convert
        # CIF -> PDB via gemmi before encoding so the frontend mol viewer
        # gets a consistent format.
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
                    final_path = local_file
                    if local_file.endswith(".cif"):
                        import gemmi
                        pdb_out = os.path.join(
                            work_dir, f"webhook_design_{c['rank']:03d}.pdb",
                        )
                        gemmi.read_structure(local_file).write_pdb(pdb_out)
                        final_path = pdb_out
                    entry["pdb_content_b64"] = base64.b64encode(
                        Path(final_path).read_bytes(),
                    ).decode()
                except Exception as exc:
                    logger.warning(
                        "Failed to encode PDB for rank %d (%s): %s",
                        c["rank"], local_file, exc,
                    )
            webhook_candidates.append(entry)

        post_webhook(webhook_url, job_id, pod_id, {
            "candidates": webhook_candidates,
            "candidate_count": len(candidates),
            "total_designs": num_designs,
            "runtime_minutes": round(elapsed_minutes, 1),
        })

    except Exception as exc:
        logger.exception("Webhook-tier pipeline failed: %s", exc)
        try:
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"Pipeline crashed: {exc}",
            })
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    """Dispatch on JOB_PAYLOAD['tier']."""
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    if not job_payload_str:
        logger.error("JOB_PAYLOAD environment variable not set")
        fail_preflight("job_payload", "JOB_PAYLOAD not set")

    try:
        job_payload = json.loads(job_payload_str)
    except json.JSONDecodeError as exc:
        fail_preflight("job_payload_json", f"{exc}")
        return

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

    tier = (job_payload.get("tier") or "").strip().lower()
    logger.info("Dispatching tier=%r", tier)

    # Preflight runs for every tier — fail fast cheaply.
    preflight(job_payload)

    if tier in ("smoke", "mini_pilot"):
        run_smoke_or_mini_pilot(tier, job_payload)
    else:
        run_webhook_tier(job_payload)


if __name__ == "__main__":
    main()
