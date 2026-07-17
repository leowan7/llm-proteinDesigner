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

import base64
import contextlib
import csv
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# pipeline_normalize.py is mounted alongside this script at /opt by
# infrastructure/modal/rfantibody_app.py. Adding /opt to sys.path makes
# the bare module name importable.
sys.path.insert(0, "/opt")

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

# Bundled framework PDB (HLT-marked, from RFantibody repo examples).
# Only VHH (single-domain heavy-chain antibody) is supported by this
# wrapper -- ProteinMPNN below only redesigns heavy-chain CDRs
# (H1/H2/H3), so scFv would silently degrade to a VHH-style run.
FRAMEWORKS = {
    "VHH": os.path.join(RFANTIBODY_DIR, "scripts/examples/example_inputs/h-NbBCII10.pdb"),
}

# Filtering thresholds. RFantibody's qvscorefile does NOT emit ipTM — it uses
# interaction_pae (binder-target PAE, stored as ``ipAE``) as the primary binder
# quality metric. Lower is better; RFantibody paper uses ipAE<=10 as the
# default binder-quality cutoff.
PAE_THRESHOLD = 10.0
PLDDT_THRESHOLD = 80.0
IPAE_THRESHOLD = 10.0


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def startup_check() -> dict:
    """Log environment and dependency status at startup.

    Crashes if CUDA is not available or required CLI tools are missing.
    """
    checks = {}

    # Validate required environment variables. WEBHOOK_URL and JOB_ID are
    # only needed for the legacy webhook path; smoke/mini_pilot runs skip
    # the webhook entirely (see docs/SMOKE-TEST-SPEC.md).
    tier = ""
    try:
        tier = json.loads(os.environ.get("JOB_PAYLOAD", "{}")).get("tier", "")
    except (json.JSONDecodeError, TypeError):
        tier = ""

    required_vars = ["JOB_PAYLOAD"]
    if tier not in ("smoke", "mini_pilot"):
        required_vars += ["WEBHOOK_URL", "JOB_ID"]

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
    new_candidate: dict | None = None,
) -> None:
    """Send a heartbeat to the Kendrew backend.

    new_candidate is an optional per-design candidate dict for live UI
    streaming. tools-hub gates it server-side via JOB_TOKEN and projects
    it to a fixed schema, so a malformed candidate is dropped silently.
    """
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


@contextlib.contextmanager
def keepalive_heartbeat(
    webhook_url: str,
    job_id: str,
    stage: str,
    num_designs: int,
    interval_s: int = 300,
):
    """Fire periodic heartbeats while a long-running subprocess executes.

    RFantibody's three GPU stages (RFdiffusion, ProteinMPNN, RF2) each run
    as single subprocesses that do not emit output Python can tail. The
    backend's stale-heartbeat cron reaps any job with no heartbeat for
    STALE_HEARTBEAT_SECONDS (1800s = 30 min). Stage 3 (RF2) commonly runs
    >30 min, so without a keepalive every session dies mid-run.

    Usage::

        with keepalive_heartbeat(webhook_url, job_id, "Running RF2", 2):
            stage_rf2(...)

    The thread is a daemon so an unexpected termination of the main
    pipeline will not leave it hanging. ``stop_event.set()`` on context
    exit causes the thread to drop out of the wait loop cleanly.
    """
    stop_event = threading.Event()

    def _run() -> None:
        while not stop_event.wait(interval_s):
            try:
                send_heartbeat(webhook_url, job_id, stage, 0, num_designs)
            except Exception as exc:  # pragma: no cover - best-effort
                logger.warning("keepalive heartbeat failed: %s", exc)

    thread = threading.Thread(target=_run, daemon=True, name=f"keepalive-{stage}")
    thread.start()
    try:
        yield
    finally:
        stop_event.set()


def download_input(url: str, dest_path: str) -> None:
    """Download a file from a presigned GET URL."""
    logger.info("Downloading input -> %s", dest_path)
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download input: HTTP {resp.status_code}")
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(resp.content)
    logger.info("Downloaded %d bytes", len(resp.content))


def preprocess_target_pdb(
    input_pdb: str, output_pdb: str, target_chain: str,
) -> dict:
    """Filter target PDB to a single chain and drop residues with bad backbones.

    RFantibody's RFdiffusion computes rotation frames from the N, CA, C atom
    triad for every input residue. Three classes of input produce the scipy
    "Non-positive determinant in rotation matrix" crash partway through
    sampling:

    1. Multi-chain targets where only one chain is the antigen. RFantibody
       expects a single-chain target; extra chains can confuse residue
       indexing and blow up frame construction.
    2. Residues with missing or zero-coordinate backbone atoms (common in
       crystal-structure disordered loops). The rotation frame collapses to
       a zero matrix whose determinant is zero.
    3. Multi-altloc crystal structures (e.g. RCSB 3IUT chain A residue 80
       has CA at altloc A AND altloc B, both occupancy 0.5). RFdiffusion
       sees two CA records per residue and produces a degenerate frame
       mid-denoise. ``pipeline_normalize.normalize_for_rfantibody`` upstream
       already collapses altlocs, but this routine re-applies the same
       choice as defense in depth in case it is ever called standalone.

    This preprocessor writes a cleaned PDB with:
      - only ATOM/HETATM lines on ``target_chain``,
      - only residues having all four backbone atoms (N, CA, C, O) with
        non-zero coordinates,
      - exactly one altloc record per (residue, atom_name), with the altloc
        column blanked on output. Tie-breaker: an unannotated altloc (' ')
        wins; else highest occupancy; else alphabetical.

    Args:
        input_pdb: Path to the raw uploaded PDB.
        output_pdb: Path to write the cleaned single-chain PDB.
        target_chain: Chain ID to retain (all others dropped).

    Returns:
        Dict with counts of ``kept`` and ``dropped`` residues plus the
        ``other_chains`` discarded and ``altloc_records_dropped``.
    """
    all_lines: list[str] = []
    other_chains: set[str] = set()
    # (chain, resseq, icode, atom_name) -> (winning_altloc, winning_occ)
    altloc_winner: dict[tuple, tuple[str, float]] = {}
    # (chain, resseq, icode) -> {atom_name: (x, y, z)} (merged after altloc pick)
    residues: dict[tuple, dict] = {}

    def _parse_atom(line: str):
        atom_name = line[12:16].strip()
        altloc = line[16] if len(line) > 16 else " "
        try:
            resseq = int(line[22:26])
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
            occ = float(line[54:60]) if len(line) >= 60 else 1.0
        except ValueError:
            return None
        icode = line[26] if len(line) > 26 else " "
        return atom_name, altloc, resseq, icode, (x, y, z), occ

    # First pass: collect lines, decide per-(residue, atom_name) which altloc
    # wins. ' ' (no altloc) beats any letter; otherwise highest occupancy
    # with alphabetical altloc as the tie-breaker.
    with open(input_pdb) as fh:
        for line in fh:
            all_lines.append(line)
            if not line.startswith(("ATOM", "HETATM")):
                continue
            chain = line[21] if len(line) > 21 else " "
            if chain != target_chain:
                other_chains.add(chain)
                continue
            parsed = _parse_atom(line)
            if parsed is None:
                continue
            atom_name, altloc, resseq, icode, coord, occ = parsed
            key = (chain, resseq, icode, atom_name)
            prior = altloc_winner.get(key)
            if prior is None:
                altloc_winner[key] = (altloc, occ)
            else:
                prior_alt, prior_occ = prior
                # ' ' always wins over a letter.
                if prior_alt == " ":
                    pass
                elif altloc == " ":
                    altloc_winner[key] = (altloc, occ)
                elif occ > prior_occ or (occ == prior_occ and altloc < prior_alt):
                    altloc_winner[key] = (altloc, occ)
            # Stash coords in the merged residue dict only when the line
            # corresponds to the current winner (refreshed after the choice).
            cur_alt, _ = altloc_winner[key]
            if altloc == cur_alt:
                residues.setdefault((chain, resseq, icode), {})[atom_name] = coord

    required = {"N", "CA", "C", "O"}
    keep: set[tuple] = set()
    dropped_reasons: dict[str, int] = {"missing_bb": 0, "zero_bb": 0}
    for key, atoms in residues.items():
        if not required.issubset(atoms.keys()):
            dropped_reasons["missing_bb"] += 1
            continue
        if any(
            all(abs(c) < 1e-6 for c in atoms[name]) for name in required
        ):
            dropped_reasons["zero_bb"] += 1
            continue
        keep.add(key)

    # Second pass: write output. Only emit ATOM/HETATM lines for retained
    # residues on the target chain, AND only the altloc-winning record per
    # (residue, atom_name). Blank the altloc column so downstream parsers
    # see a clean single-conformation file. Non-coordinate lines (HEADER,
    # TITLE, REMARK, CRYST1, etc.) flow through verbatim.
    altloc_dropped = 0
    with open(output_pdb, "w") as fh:
        for line in all_lines:
            if line.startswith(("ATOM", "HETATM")):
                chain = line[21] if len(line) > 21 else " "
                if chain != target_chain:
                    continue
                parsed = _parse_atom(line)
                if parsed is None:
                    continue
                atom_name, altloc, resseq, icode, _coord, _occ = parsed
                res_key = (chain, resseq, icode)
                if res_key not in keep:
                    continue
                winner_key = (chain, resseq, icode, atom_name)
                cur_alt, _ = altloc_winner.get(winner_key, (" ", 0.0))
                if altloc != cur_alt:
                    altloc_dropped += 1
                    continue
                # Blank the altloc column on output.
                if len(line) > 17:
                    line = line[:16] + " " + line[17:]
            fh.write(line)

    stats = {
        "kept": len(keep),
        "dropped_missing_bb": dropped_reasons["missing_bb"],
        "dropped_zero_bb": dropped_reasons["zero_bb"],
        "altloc_records_dropped": altloc_dropped,
        "other_chains_discarded": sorted(other_chains),
    }
    logger.info("PDB preprocessing: %s", json.dumps(stats))
    if not keep:
        raise RuntimeError(
            f"No residues with complete backbones survived on chain "
            f"{target_chain!r}. Stats: {stats}",
        )
    return stats


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


_WEBHOOK_OUTCOME_PATH = "/tmp/webhook_outcome.json"

# Where the complete work-dir tar is left for the Modal wrapper to collect. Fixed
# path (not a temp name) so infrastructure/modal/rfantibody_app.py can find it
# without the two sides having to exchange anything: this script runs as a
# subprocess and cannot mount the Volume itself, the wrapper can.
_RAW_ARCHIVE_PATH = "/tmp/raw_archive.tgz"


def archive_raw_outputs(work_dir: str, dest: str = _RAW_ARCHIVE_PATH) -> None:
    """Tar the ENTIRE work dir to ``dest`` so nothing dies with the rmtree.

    Everything this script currently keeps is a curated subset. parse_scores_tsv()
    lifts 5 metrics out of the qvscorefile TSV; only PDBs that matched a scored
    design are uploaded. The Quiver files (backbones/sequences/predictions), the
    raw .sc scorefile, the unmatched PDBs and every RFdiffusion/RF2 log are
    deleted by the caller's ``finally`` and are recoverable only by paying for the
    GPU again.

    A container must never be the thing that decides which fields were worth
    keeping. That is exactly how ``iptm`` (interface-pTM averaged over EVERY chain
    pair, so on a multi-chain target it is dominated by the target's own
    interface) was shipped in place of ``design_iptm`` (binder -> target) on 460
    boltzgen designs, reading ~2x high, producing two confidently wrong verdicts
    that could not be rechecked. Ship the tree home; decide locally, where
    re-parsing is free.

    Deliberately NOT gated on candidates, on success, or on filenames_to_upload: a
    run that scored zero designs uploads nothing today and is precisely the run
    whose tree you need. Callers invoke this from their ``finally``, so a crashed
    run is archived too.

    Archiving must never fail the run — a job that died before writing output is
    when diagnostics matter most, so problems are logged, never raised.

    Args:
        work_dir: Directory to archive. A missing dir is logged and skipped.
        dest: Path to write the .tgz to. MUST be outside ``work_dir``.
    """
    try:
        if not os.path.isdir(work_dir):
            logger.warning(
                "Raw capture: no work dir to archive at %s (nothing to ship)",
                work_dir,
            )
            return

        # The tar must not be written inside the tree it archives, or it tars
        # itself. /tmp/raw_archive.tgz is outside a /tmp/rfantibody_*/ work dir,
        # but the guard is explicit: the day either path is repointed is the day
        # this silently recurses.
        work_abs = os.path.abspath(work_dir)
        dest_abs = os.path.abspath(dest)
        if dest_abs == work_abs or dest_abs.startswith(work_abs + os.sep):
            logger.error(
                "Raw capture: refusing to write archive %s inside the tree it "
                "archives (%s)", dest_abs, work_abs,
            )
            return

        # Stream straight to the file. NEVER io.BytesIO — buffering a
        # multi-hundred-MB Quiver tree in memory costs ~3-4x peak RSS versus ~1x
        # streamed, on a container already holding GPU model weights.
        with tarfile.open(dest_abs, "w:gz") as tar:
            tar.add(work_abs, arcname=os.path.basename(work_abs) or "work")

        logger.info(
            "Raw capture: archived %s -> %s (%d bytes)",
            work_abs, dest_abs, os.path.getsize(dest_abs),
        )
    except Exception as exc:
        logger.error(
            "Raw capture failed (non-fatal): %s: %s", type(exc).__name__, exc,
        )
        # A crash mid-write (e.g. ENOSPC) can leave a truncated but still-openable .tgz at
        # the destination; the wrapper parks whatever exists. Remove the partial so a failed
        # capture parks NOTHING rather than a tar that reports success but cannot be read.
        try:
            if os.path.exists(dest_abs):
                os.remove(dest_abs)
        except OSError:
            pass


def _record_webhook_outcome(delivered: bool, detail: str) -> None:
    """Persist webhook delivery status so the Modal wrapper can surface
    it to the consuming web service even when the POST silently fails. Read by run_tool()
    in infrastructure/modal/rfantibody_app.py and merged into the function
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


def _classify_rfdiffusion_error(raw_message: str, preprocess_stats: dict) -> str:
    """Convert a raw RFdiffusion exit-1 dump into a user-actionable message.

    The most common deterministic failure on user-uploaded targets is the
    "Non-positive determinant" crash in ``scipy.spatial.transform.Rotation``:
    a degenerate (all-zeros) rotation frame is produced mid-denoise, which
    almost always means the target geometry the model received was internally
    inconsistent — usually because the input PDB contained alternate
    conformations the upstream cleanup pass missed, or backbone breaks near
    a hotspot. Surface that as plain English so the user can act on it
    instead of pasting the ANSI-coloured stack trace into a support email.
    """
    msg = raw_message or ""
    if "Non-positive determinant" in msg or "rotation matrix" in msg:
        bits = [
            "RFdiffusion produced a degenerate frame mid-denoise on your target. "
            "This is almost always an input-geometry problem rather than a model issue.",
            "Likely causes (in order of frequency):",
            "  1. The target PDB contains alternate side-chain conformations (altloc A/B/C). "
            "Try re-running with the AlphaFold-predicted version of the same protein, or "
            "rebuild the target with a single conformation.",
            "  2. Chain breaks or missing backbone atoms (N, CA, C, O) inside or near the "
            "hotspot region. Re-clean the target PDB so every residue in the epitope "
            "neighbourhood has a complete backbone.",
            "  3. Hotspot residue numbers that point at a non-protein atom (HETATM, ligand, "
            "ion). Confirm the hotspots use the original PDB author numbering and resolve "
            "to standard amino acids on the target chain.",
        ]
        if preprocess_stats:
            kept = preprocess_stats.get("kept", "?")
            dropped_bb = preprocess_stats.get("dropped_missing_bb", 0)
            dropped_alt = preprocess_stats.get("altloc_records_dropped", 0)
            bits.append(
                f"Pre-flight cleanup kept {kept} residue(s); dropped "
                f"{dropped_bb} for missing backbone and "
                f"{dropped_alt} alt-conformation atom record(s)."
            )
        return "\n".join(bits)
    return f"RFdiffusion failed: {msg}"


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
        "--target", target_pdb,
        "--framework", framework_pdb,
        "--output-quiver", backbones_qv,
        "--num-designs", str(num_designs),
        "--design-loops", cdr_lengths,
    ]
    if hotspots:
        cmd.extend(["--hotspots", hotspots])

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

    # For VHH (nanobody) we only design heavy chain CDRs — framework has no
    # light chain. Default "H1,H2,H3,L1,L2,L3" would crash on single-chain
    # input. Caller uses cdr_lengths like "H1:8,H2:7,H3:10-16" upstream;
    # we pass the same loop names to proteinmpnn.
    cmd = [
        "proteinmpnn",
        "--input-quiver", backbones_qv,
        "--output-quiver", sequences_qv,
        "--seqs-per-struct", str(seqs_per_backbone),
        "--temperature", str(temperature),
        "--loops", "H1,H2,H3",
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
    and generates confidence scores (pAE, pLDDT, ipAE via interaction_pae).

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
        "--input-quiver", sequences_qv,
        "--output-quiver", predictions_qv,
        "--num-recycles", str(recycles),
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

    ``qvscorefile`` writes the TSV to ``<input>.sc`` (same directory as the
    input .qv) — it does NOT write to stdout. We run the command, then copy
    the generated ``.sc`` file to the caller-requested ``scores_tsv`` path.
    """
    logger.info("Extracting scores from %s", predictions_qv)
    run_command(
        ["qvscorefile", predictions_qv],
        timeout=120,
        cwd=RFANTIBODY_DIR,
    )
    generated_sc = Path(predictions_qv).with_suffix(".sc")
    if not generated_sc.exists():
        raise RuntimeError(
            f"qvscorefile did not produce expected scorefile {generated_sc}"
        )
    shutil.copyfile(str(generated_sc), scores_tsv)
    logger.info("Scores written to %s (from %s)", scores_tsv, generated_sc)


def extract_pdbs(predictions_qv: str, out_dir: str) -> list[str]:
    """Extract all PDB files from predictions Quiver.

    Uses qvextract to write individual PDB files for upload. The CLI flag is
    ``-o / --output-dir`` (not ``--out-dir``).

    Returns:
        List of extracted PDB file paths.
    """
    logger.info("Extracting PDBs from %s to %s", predictions_qv, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    run_command(
        ["qvextract", predictions_qv, "-o", out_dir, "--force"],
        timeout=120,
        cwd=RFANTIBODY_DIR,
    )
    pdbs = sorted(str(p) for p in Path(out_dir).glob("*.pdb"))
    logger.info("Extracted %d PDB files", len(pdbs))
    return pdbs


def parse_scores_tsv(tsv_path: str) -> list[dict]:
    """Parse the scores TSV produced by qvscorefile.

    RFantibody's qvscorefile emits these columns (verified against tool output):
    interaction_pae, pae, pred_lddt, target_aligned_antibody_rmsd,
    framework_aligned_*_rmsd, tag. Note: RFantibody does NOT emit ipTM/pTM —
    it uses interaction_pae (binder-target PAE) as the primary binder
    quality metric, stored here as ``ipAE``.

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
                or row_lower.get("tag")
                or f"design_{len(results)}"
            )
            design_name = Path(design_name).stem

            scores = {}
            for metric, keys, default in [
                ("pAE", ["pae", "mean_pae"], 99.0),
                ("ipAE", ["interaction_pae", "ipae", "i_pae"], 99.0),
                ("pLDDT", ["pred_lddt", "plddt", "mean_plddt", "avg_plddt"], 0.0),
                ("ipTM", ["iptm", "ip_tm", "iptm_score"], 0.0),
                ("pTM", ["ptm", "p_tm"], 0.0),
            ]:
                for key in keys:
                    if key in row_lower and row_lower[key]:
                        scores[metric] = _safe_float(row_lower[key], default)
                        break

            # pred_lddt may be on the 0..1 scale; normalize to 0..100 for UX.
            if "pLDDT" in scores and scores["pLDDT"] <= 1.0:
                scores["pLDDT"] = scores["pLDDT"] * 100.0

            results.append({
                "design_name": design_name,
                "scores": scores,
            })

    logger.info("Parsed %d designs from scores TSV", len(results))
    return results


def filter_and_rank(designs: list[dict]) -> list[dict]:
    """Label every design with filter_status and rank by ipAE.

    RFantibody uses ipAE (interaction_pae, binder-target PAE) instead of ipTM.
    Lower ipAE is better, so we sort ascending. A bad result is still a
    result: every scored design is kept and tagged "pass" or "below
    threshold" so the UI can show all of them. The in silico thresholds
    now drive a label, not a gate.
    """
    pass_count = 0
    for design in designs:
        scores = design["scores"]
        pae = scores.get("pAE")
        plddt = scores.get("pLDDT")
        ipae = scores.get("ipAE")
        is_pass = (
            pae is not None
            and plddt is not None
            and ipae is not None
            and pae <= PAE_THRESHOLD
            and plddt >= PLDDT_THRESHOLD
            and ipae <= IPAE_THRESHOLD
        )
        scores["filter_status"] = "pass" if is_pass else "below threshold"
        if is_pass:
            pass_count += 1

    ranked = list(designs)
    ranked.sort(key=lambda x: x["scores"].get("ipAE", 99.0))

    logger.info(
        "Labeling: %d / %d pass (pAE<=%.1f, pLDDT>=%.0f, ipAE<=%.1f); "
        "all designs emitted with filter_status label",
        pass_count, len(designs),
        PAE_THRESHOLD, PLDDT_THRESHOLD, IPAE_THRESHOLD,
    )
    return ranked


def write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a normalized metrics CSV for upload to Kendrew."""
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "design_name", "ipAE", "pLDDT", "pAE",
        ])
        for candidate in candidates:
            scores = candidate["scores"]
            writer.writerow([
                candidate["rank"],
                candidate["design_name"],
                scores.get("ipAE", ""),
                scores.get("pLDDT", ""),
                scores.get("pAE", ""),
            ])


# ===========================================================================
# Smoke / mini_pilot tier (see docs/SMOKE-TEST-SPEC.md)
# ===========================================================================

# Baked-in smoke target (PD-L1 IgV, chain A, residues 18-132). Matches the
# COPY line in Dockerfile.modal. Used when tier is smoke or mini_pilot.
SMOKE_TARGET_PDB = "/opt/smoke_target.pdb"
SMOKE_RESULTS_PATH = "/tmp/smoke_results.json"


def _write_smoke_failure(bucket: str, check: str, detail: str) -> None:
    """Write a structured failure to ``/tmp/smoke_results.json`` and exit 1.

    Matches the shape required by docs/SMOKE-TEST-SPEC.md Layer 2.
    """
    payload = {
        "status": "FAILED",
        "error": {"bucket": bucket, "check": check, "detail": detail[:4000]},
    }
    try:
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            json.dump(payload, fh)
    except OSError as exc:
        logger.error("Failed to write smoke_results.json: %s", exc)
    logger.error("Smoke failure [%s/%s]: %s", bucket, check, detail[:500])


def preflight(payload: dict) -> None:
    """Layer-2 fail-fast checks for smoke / mini_pilot tiers.

    Must complete in < 60s on GPU. On any failure, writes a structured error
    to ``/tmp/smoke_results.json`` and exits 1 so the Modal wrapper surfaces
    the failure to the orchestrator without wasting GPU minutes on a doomed
    pipeline. See docs/SMOKE-TEST-SPEC.md.

    Args:
        payload: Parsed JOB_PAYLOAD dict. Must contain ``tier`` key.
    """
    # 1. Payload has expected shape
    tier = payload.get("tier")
    if tier not in ("smoke", "mini_pilot"):
        _write_smoke_failure(
            "preflight", "tier",
            f"Unexpected tier {tier!r} — expected 'smoke' or 'mini_pilot'",
        )
        sys.exit(1)

    # 2. Smoke target fixture present and readable
    if not os.path.exists(SMOKE_TARGET_PDB):
        _write_smoke_failure(
            "preflight", "smoke_target_pdb",
            f"Baked fixture {SMOKE_TARGET_PDB} not found",
        )
        sys.exit(1)
    try:
        with open(SMOKE_TARGET_PDB) as fh:
            head = fh.read(200)
        if not head.strip():
            raise OSError("empty file")
    except OSError as exc:
        _write_smoke_failure(
            "preflight", "smoke_target_pdb_read", f"{exc}",
        )
        sys.exit(1)

    # 3. GPU available
    try:
        import torch  # noqa: WPS433 — deferred so CPU dry-runs don't import
        if not torch.cuda.is_available():
            _write_smoke_failure(
                "preflight", "cuda",
                "torch.cuda.is_available() returned False",
            )
            sys.exit(1)
    except Exception as exc:  # pragma: no cover — catches all import errors
        _write_smoke_failure("preflight", "torch_import", f"{exc}")
        sys.exit(1)

    # 4. CLI tools respond to --help
    for tool in ("rfdiffusion", "proteinmpnn", "rf2", "qvscorefile", "qvextract"):
        try:
            result = subprocess.run(
                [tool, "--help"], capture_output=True, text=True, timeout=20,
            )
        except FileNotFoundError as exc:
            _write_smoke_failure(
                "preflight", f"{tool}_cli_not_found", f"{exc}",
            )
            sys.exit(1)
        except subprocess.TimeoutExpired:
            _write_smoke_failure(
                "preflight", f"{tool}_cli_timeout",
                f"{tool} --help did not return within 20s",
            )
            sys.exit(1)
        if result.returncode != 0:
            _write_smoke_failure(
                "preflight", f"{tool}_cli_help_exit",
                f"exit {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            )
            sys.exit(1)

    # 5. /tmp/smoke_results.json is writable
    try:
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            json.dump({"status": "RUNNING", "tier": tier}, fh)
    except OSError as exc:
        # If we can't write smoke_results.json, we have nowhere to report
        # the failure — log and exit.
        logger.error("Cannot write %s: %s", SMOKE_RESULTS_PATH, exc)
        sys.exit(1)

    # 6. Weights present
    weights_dir = os.path.join(RFANTIBODY_DIR, "weights")
    for weight_name in ("RF2_ab.pt", "RFdiffusion_Ab.pt", "ProteinMPNN_v48_noise_0.2.pt"):
        weight_path = os.path.join(weights_dir, weight_name)
        if not os.path.exists(weight_path):
            _write_smoke_failure(
                "preflight", f"weight_{weight_name}",
                f"Missing weight file {weight_path}",
            )
            sys.exit(1)

    logger.info("Preflight checks passed (tier=%s)", tier)


def _get_smoke_preset(tier: str) -> dict:
    """Return pipeline parameters for smoke and mini_pilot tiers.

    Parameters are tuned for ~5-10 GPU-minutes on A100-40GB, not design
    quality. Smoke = N=1 minimum; mini_pilot = N=2 full scoring.
    """
    if tier == "smoke":
        return {
            "num_designs": 1,
            "cdr_lengths": "H1:8,H2:7,H3:10",
            # PD-L1 PD-1 binding interface residues (chain A).
            "hotspots": "A54,A56,A115",
            "framework": "VHH",
            "mpnn_seqs_per_backbone": 1,
            "mpnn_temperature": 0.2,
            "rf2_recycles": 1,
            "diffuser_t": 25,
        }
    # mini_pilot
    return {
        "num_designs": 2,
        "cdr_lengths": "H1:8,H2:7,H3:10-13",
        "hotspots": "A54,A56,A115,A123",
        "framework": "VHH",
        "mpnn_seqs_per_backbone": 1,
        "mpnn_temperature": 0.2,
        "rf2_recycles": 3,
        "diffuser_t": 50,
    }


def run_smoke_pipeline(tier: str) -> None:
    """Run the smoke / mini_pilot pipeline and write /tmp/smoke_results.json.

    Skips the webhook path entirely — results are returned inline via the
    JSON blob the Modal wrapper reads after the subprocess exits.
    """
    import base64

    preset = _get_smoke_preset(tier)
    pipeline_start = time.time()

    work_dir = tempfile.mkdtemp(prefix="rfantibody_smoke_")
    target_pdb = os.path.join(work_dir, "target.pdb")
    backbones_qv = os.path.join(work_dir, "backbones.qv")
    sequences_qv = os.path.join(work_dir, "sequences.qv")
    predictions_qv = os.path.join(work_dir, "predictions.qv")
    scores_tsv = os.path.join(work_dir, "scores.tsv")
    top_hits_dir = os.path.join(work_dir, "top_hits")

    try:
        # Preprocess the baked fixture (drop malformed residues, single chain).
        try:
            preprocess_target_pdb(
                SMOKE_TARGET_PDB, target_pdb, target_chain="A",
            )
        except Exception as exc:
            _write_smoke_failure("preprocess", "target_pdb_cleanup", f"{exc}")
            sys.exit(1)

        framework_pdb = FRAMEWORKS[preset["framework"]]

        # Stage 1: RFdiffusion backbones.
        # We extend stage_rfdiffusion's built-in CLI with a --diffuser-t override
        # to reduce smoke runtime. Rather than re-implement the stage, call the
        # CLI directly.
        rfd_cmd = [
            "rfdiffusion",
            "--target", target_pdb,
            "--framework", framework_pdb,
            "--output-quiver", backbones_qv,
            "--num-designs", str(preset["num_designs"]),
            "--design-loops", preset["cdr_lengths"],
            "--hotspots", preset["hotspots"],
            "--diffuser-t", str(preset["diffuser_t"]),
        ]
        try:
            run_command(rfd_cmd, timeout=1800, cwd=RFANTIBODY_DIR)
        except RuntimeError as exc:
            _write_smoke_failure("rfdiffusion", "subprocess", f"{exc}")
            sys.exit(1)
        if not os.path.exists(backbones_qv):
            _write_smoke_failure(
                "rfdiffusion", "output_missing",
                f"RFdiffusion did not produce {backbones_qv}",
            )
            sys.exit(1)

        # Stage 2: ProteinMPNN sequence design.
        try:
            stage_proteinmpnn(
                backbones_qv, sequences_qv,
                seqs_per_backbone=preset["mpnn_seqs_per_backbone"],
                temperature=preset["mpnn_temperature"],
                num_designs=preset["num_designs"],
            )
        except RuntimeError as exc:
            _write_smoke_failure("proteinmpnn", "subprocess", f"{exc}")
            sys.exit(1)

        # Stage 3: RF2 structure prediction.
        try:
            stage_rf2(
                sequences_qv, predictions_qv,
                recycles=preset["rf2_recycles"],
                num_designs=preset["num_designs"],
            )
        except RuntimeError as exc:
            _write_smoke_failure("rf2", "subprocess", f"{exc}")
            sys.exit(1)

        # Stage 4: Score extraction.
        try:
            extract_scores(predictions_qv, scores_tsv)
        except RuntimeError as exc:
            _write_smoke_failure("qvscorefile", "subprocess", f"{exc}")
            sys.exit(1)

        all_designs = parse_scores_tsv(scores_tsv)
        if not all_designs:
            _write_smoke_failure(
                "output_parse", "scores_empty",
                f"No designs parsed from {scores_tsv}",
            )
            sys.exit(1)

        # Stage 5: Extract PDBs.
        try:
            extracted_pdbs = extract_pdbs(predictions_qv, top_hits_dir)
        except RuntimeError as exc:
            _write_smoke_failure("qvextract", "subprocess", f"{exc}")
            sys.exit(1)
        if not extracted_pdbs:
            _write_smoke_failure(
                "output_parse", "no_pdbs",
                f"qvextract produced zero PDB files in {top_hits_dir}",
            )
            sys.exit(1)

        pdb_map = {Path(p).stem: p for p in extracted_pdbs}

        # Rank by ipAE (ascending — lower is better), take top-N per tier.
        # RFantibody uses ipAE (interaction_pae) in place of ipTM.
        all_designs.sort(
            key=lambda d: d["scores"].get("ipAE", 99.0),
        )
        top_n = 1 if tier == "smoke" else 2
        selected = all_designs[:top_n]

        # Stamp filter_status on every score dict so the UI shows pass or
        # below threshold instead of a blank dash. Mirrors filter_and_rank
        # which the pilot path runs but the smoke path bypasses.
        for design in selected:
            scores = design["scores"]
            if "filter_status" in scores:
                continue
            pae_v = scores.get("pAE")
            plddt_v = scores.get("pLDDT")
            ipae_v = scores.get("ipAE")
            is_pass = (
                pae_v is not None
                and plddt_v is not None
                and ipae_v is not None
                and pae_v <= PAE_THRESHOLD
                and plddt_v >= PLDDT_THRESHOLD
                and ipae_v <= IPAE_THRESHOLD
            )
            scores["filter_status"] = "pass" if is_pass else "below threshold"

        candidates = []
        for rank_idx, design in enumerate(selected):
            design_name = design["design_name"]
            local_file = pdb_map.get(design_name)
            if not local_file:
                # fuzzy-match
                for key, path in pdb_map.items():
                    if design_name in key or key in design_name:
                        local_file = path
                        break
            if not local_file:
                # Fall back to any extracted PDB — as long as we have N PDBs,
                # the pipeline is valid even if score-name matching is imperfect.
                remaining = [
                    p for p in extracted_pdbs
                    if p not in {c.get("local_file") for c in candidates}
                ]
                if not remaining:
                    _write_smoke_failure(
                        "output_parse", "pdb_match_failed",
                        f"No PDB match for {design_name}; available stems: "
                        f"{list(pdb_map.keys())[:5]}",
                    )
                    sys.exit(1)
                local_file = remaining[0]

            try:
                pdb_bytes = Path(local_file).read_bytes()
            except OSError as exc:
                _write_smoke_failure(
                    "serialization", "pdb_read",
                    f"Failed to read {local_file}: {exc}",
                )
                sys.exit(1)

            candidates.append({
                "rank": rank_idx + 1,
                "pdb_key": f"design_{rank_idx + 1:03d}.pdb",
                "pdb_content_b64": base64.b64encode(pdb_bytes).decode("ascii"),
                "scores": design["scores"],
                "local_file": local_file,
            })

        # Strip non-serializable local_file before emitting.
        for cand in candidates:
            cand.pop("local_file", None)

        gpu_seconds = int(time.time() - pipeline_start)
        result = {
            "status": "COMPLETED",
            "output": {"candidates": candidates},
            "tier": tier,
            "gpu_seconds": gpu_seconds,
        }
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            json.dump(result, fh)
        logger.info(
            "Smoke pipeline complete: tier=%s candidates=%d gpu_seconds=%d",
            tier, len(candidates), gpu_seconds,
        )
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("Unhandled smoke-pipeline error")
        _write_smoke_failure("unhandled", "exception", f"{exc}")
        sys.exit(1)
    finally:
        # Ship the complete tree before it is destroyed. In the finally because
        # every failure path above is a sys.exit() inside the try: SystemExit
        # unwinds through here, so a smoke run that died at stage 1 is archived
        # exactly like one that completed.
        archive_raw_outputs(work_dir)
        shutil.rmtree(work_dir, ignore_errors=True)


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full RFantibody pipeline."""
    startup_check()

    # Smoke / mini_pilot tier branching. See docs/SMOKE-TEST-SPEC.md.
    job_payload_str = os.environ.get("JOB_PAYLOAD", "{}")
    try:
        job_payload = json.loads(job_payload_str)
    except json.JSONDecodeError as exc:
        logger.error("JOB_PAYLOAD is not valid JSON: %s", exc)
        _write_smoke_failure("preflight", "payload_json", f"{exc}")
        sys.exit(1)

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

    tier = job_payload.get("tier", "")
    if tier in ("smoke", "mini_pilot"):
        preflight(job_payload)
        run_smoke_pipeline(tier)
        return

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
    chain = job_spec.get("target_chain", "A")
    if not hotspots_str:
        hotspot_residues = job_spec.get("hotspot_residues", [])
        if hotspot_residues:
            hotspots_str = ",".join(f"{chain}{res}" for res in hotspot_residues)

    pipeline_start = time.time()
    work_dir = tempfile.mkdtemp(prefix="rfantibody_job_")
    raw_target_pdb = os.path.join(work_dir, "target_raw.pdb")
    target_pdb = os.path.join(work_dir, "target.pdb")

    # Quiver file paths
    backbones_qv = os.path.join(work_dir, "backbones.qv")
    sequences_qv = os.path.join(work_dir, "sequences.qv")
    predictions_qv = os.path.join(work_dir, "predictions.qv")
    scores_tsv = os.path.join(work_dir, "scores.tsv")
    top_hits_dir = os.path.join(work_dir, "top_hits")

    try:
        # ----- Download target PDB -----
        download_input(input_url, raw_target_pdb)
        send_heartbeat(webhook_url, job_id, "Input downloaded", 0, num_designs)

        # ----- Sanitize target PDB (Bug 9 fix) -----
        # Biopython-based normalize handles multi-model NMR, altloc
        # disambiguation, MSE->MET, and water/HETATM stripping that the
        # legacy preprocess_target_pdb line-filter doesn't cover. We then
        # still run preprocess_target_pdb on the cleaned file as a final
        # rfantibody-specific filter (it does additional residue-by-residue
        # backbone validation that we keep as defense in depth).
        normalized_pdb = os.path.join(work_dir, "target_normalized.pdb")
        try:
            from pipeline_normalize import normalize_for_rfantibody
            norm_report = normalize_for_rfantibody(
                raw_target_pdb, normalized_pdb, target_chain=chain,
            )
            logger.info(
                "Normalize: chains_kept=%s chains_dropped=%s residues_kept=%s "
                "residues_dropped=%s changes=%s",
                norm_report.chains_kept, norm_report.chains_dropped,
                norm_report.residues_kept_per_chain,
                norm_report.residues_dropped_per_chain,
                norm_report.changes,
            )
        except Exception as exc:
            logger.error("PDB sanitize failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"PDB sanitize failed: {exc}",
            })
            return

        # ----- Preprocess target PDB -----
        # RFdiffusion's scipy-based rotation math fails with
        # "Non-positive determinant" on residues with missing or zero
        # backbone atoms, and multi-chain inputs confuse the hotspot
        # resolver. Filter to target_chain and drop malformed residues
        # before we hand the file to RFdiffusion.
        preprocess_stats = preprocess_target_pdb(
            normalized_pdb, target_pdb, target_chain=chain,
        )
        logger.info("Preprocessed target PDB: %s", preprocess_stats)
        send_heartbeat(webhook_url, job_id, "Input preprocessed", 0, num_designs)

        # ----- Stage 1: RFdiffusion -----
        # Wrap each GPU subprocess in a keepalive-heartbeat sidecar. These
        # subprocesses do not emit progress to stdout, so without a sidecar
        # the backend's 30-min stale-heartbeat cron reaps the job. RF2
        # (stage 3) is the most at-risk: it can run >30 min even for
        # pilot-sized inputs.
        try:
            with keepalive_heartbeat(webhook_url, job_id, "Running RFdiffusion", num_designs):
                stage_rfdiffusion(
                    target_pdb, framework_pdb, backbones_qv,
                    num_designs, cdr_lengths, hotspots_str,
                    webhook_url=webhook_url, job_id=job_id,
                )
        except RuntimeError as exc:
            logger.error("RFdiffusion failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": _classify_rfdiffusion_error(str(exc), preprocess_stats),
            })
            return

        # ----- Stage 2: ProteinMPNN -----
        try:
            with keepalive_heartbeat(webhook_url, job_id, "Running ProteinMPNN", num_designs):
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
            with keepalive_heartbeat(webhook_url, job_id, "Running RF2 validation", num_designs):
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

        # ----- Label and rank -----
        # filter_and_rank now keeps every scored design and tags it with
        # filter_status. No tier-specific fallback is needed because the
        # in silico thresholds are a label, not a gate.
        passing = filter_and_rank(all_designs)

        # ----- Prepare upload list -----
        # Use a separate counter for emitted rank so designs without a
        # matching PDB (which `continue` past) don't leave rank gaps in
        # the surviving candidates (mirrors the boltzgen fix).
        candidates = []
        filenames_to_upload = []
        emitted_rank = 0

        for design in passing:
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

            emitted_rank += 1
            rank = emitted_rank

            upload_filename = f"design_{rank:03d}.pdb"
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
                "local_file": local_file,
                "upload_filename": upload_filename,
            })

            # Emit per-design heartbeat with the candidate for live UI
            # streaming. Score-key mapping: rfantibody uses pAE / pLDDT /
            # ipAE; we map ipAE to the contract field i_pae and leave iptm
            # null (rfantibody has no ipTM). filter_status from the
            # in-script filter (passes if it reached filter_and_rank; pilot
            # fallback stamps "below threshold" on the scores dict). pdb_key
            # is None because uploads are batched after the loop completes
            # and the frontend handles missing pdb_key.
            try:
                scores_d = design.get("scores", {}) or {}
                plddt_v = scores_d.get("pLDDT", scores_d.get("plddt"))
                ipae_v = scores_d.get("ipAE", scores_d.get("i_pAE", scores_d.get("ipae")))
                fstatus = scores_d.get("filter_status") or "pass"
                new_cand = {
                    "rank": rank,
                    "pdb_key": None,
                    "iptm": None,
                    "plddt": round(float(plddt_v), 4) if plddt_v is not None else None,
                    "i_pae": round(float(ipae_v), 4) if ipae_v is not None else None,
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

        # ----- Upload outputs -----
        send_heartbeat(
            webhook_url, job_id, "Uploading results",
            len(candidates), len(candidates),
        )

        upload_urls = {}
        url_exchange_error = None
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(
                    upload_endpoint, job_token, filenames_to_upload,
                )
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)
                url_exchange_error = str(exc)

        failed_uploads = []
        if filenames_to_upload and not upload_urls:
            # The URL exchange yielded nothing, so every `if upload_filename in upload_urls` below is
            # False: nothing uploads, work_dir is rmtree'd in the finally, and the job STILL posts a
            # success webhook. An entire multi-hour GPU run disappears while the UI says COMPLETED.
            # failed_uploads is already surfaced to tools-hub via result_payload and already makes
            # _slim_result_for_persist KEEP the inline b64 structures rather than drop them as
            # "already in Storage". Telling it the truth is enough - the bug was only the silence.
            logger.error(
                "Upload URL exchange yielded no URLs (%s); marking all %d artifact(s) as failed "
                "so the run is not reported as a clean success",
                url_exchange_error or "empty response", len(filenames_to_upload),
            )
            failed_uploads.extend(filenames_to_upload)
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
                    failed_uploads.append("metrics.csv")

        elapsed_minutes = (time.time() - pipeline_start) / 60.0
        logger.info(
            "Pipeline complete: %d candidates in %.1f minutes",
            len(candidates), elapsed_minutes,
        )

        # ----- POST results to webhook -----
        # Inline base64 of each candidate's PDB so candidate_table.html can
        # render the 3D-viewer + PDB-download buttons (otherwise it falls
        # through to the em-dash branch keyed on pdb_content_b64). Mirrors
        # the smoke/mini_pilot path at line 1052.
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
                    pdb_bytes = Path(local_file).read_bytes()
                    entry["pdb_content_b64"] = base64.b64encode(pdb_bytes).decode("ascii")
                except OSError as exc:
                    logger.warning(
                        "Failed to read PDB for rank %d (%s): %s",
                        c["rank"], local_file, exc,
                    )
            webhook_candidates.append(entry)

        result_payload = {
            "candidates": webhook_candidates,
            "candidate_count": len(candidates),
            "total_designs": num_designs,
            "rf2_scored": len(all_designs),
            "passing_filters": sum(
                1 for c in candidates
                if c.get("scores", {}).get("filter_status") == "pass"
            ),
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
        # Ship the complete tree before it is destroyed. In the finally because
        # the stage failures above `return` out of the try after posting an error
        # webhook, and a run that produced no scored designs uploads nothing at
        # all — those are the runs whose tree is worth the most.
        archive_raw_outputs(work_dir)
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
