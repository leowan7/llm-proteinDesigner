"""Standalone pipeline script for RunPod GPU Pods.

Reads job configuration from the JOB_PAYLOAD environment variable,
runs the 3-stage RFdiffusion pipeline, uploads results via presigned URLs,
POSTs results to the Kendrew webhook, then exits.

Environment variables:
    JOB_PAYLOAD     JSON string with job_spec, upload endpoint, and webhook config
    WEBHOOK_URL     URL to POST results to (Kendrew backend)
    JOB_ID          Kendrew job UUID (for webhook identification)
    JOB_TOKEN       Job-specific auth token for requesting upload URLs on-demand
    POD_ID          RunPod pod ID (so backend can terminate after completion)

Pipeline stages:
  1. RFdiffusion  -- generate poly-Gly backbone PDBs
  2. ProteinMPNN  -- assign sequences to designed backbones (fix target chain)
  3. AF2 multimer -- validate binder-target complex, extract ipTM/pLDDT/i_pAE
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
import time
from glob import glob
from pathlib import Path

# pipeline_normalize.py is mounted alongside this script at /opt by
# infrastructure/modal/rfdiffusion_app.py. Adding /opt to sys.path makes
# the bare module name importable.
sys.path.insert(0, "/opt")

import requests

# ---------------------------------------------------------------------------
# Smoke/mini_pilot constants — see docs/SMOKE-TEST-SPEC.md
# ---------------------------------------------------------------------------
SMOKE_RESULTS_PATH = "/tmp/smoke_results.json"
SMOKE_TARGET_PDB = "/opt/smoke_target.pdb"  # Baked into the Docker image.
SMOKE_TARGET_CHAIN = "A"
# Reasonable PD-1 binding interface residues on PD-L1 chain A.
# (Residues ~54, 56, 115, 123 sit on the IgV sheet that contacts PD-1.)
SMOKE_HOTSPOTS = [54, 56, 115, 123]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("rfdiffusion_pipeline")

# ---------------------------------------------------------------------------
# Paths inside the container (all weights baked into the Docker image)
# ---------------------------------------------------------------------------
MODELS_DIR = os.environ.get("MODELS_DIR", "/opt/rfdiffusion/models")
RFDIFFUSION_DIR = "/opt/rfdiffusion"
RFDIFFUSION_SCRIPT = f"{RFDIFFUSION_DIR}/scripts/run_inference.py"
PROTEINMPNN_SCRIPT = "/opt/ProteinMPNN/protein_mpnn_run.py"
PROTEINMPNN_WEIGHTS = "/opt/ProteinMPNN/vanilla_model_weights"

# Filtering thresholds
IPTM_THRESHOLD = 0.70
PLDDT_THRESHOLD = 80.0
IPAE_THRESHOLD = 10.0


# ===========================================================================
# Startup diagnostics
# ===========================================================================

def download_af2_weights():
    """Download AlphaFold2 multimer weights if not already present.

    Weights are downloaded at startup rather than baked into the Docker image
    to keep the image small (~8GB vs ~25GB). Downloads to COLABFOLD_CACHE_DIR
    or falls back to the default ColabFold cache location.
    """
    cache_dir = os.environ.get("COLABFOLD_CACHE_DIR", "/opt/colabfold_weights")
    params_dir = os.path.join(cache_dir, "params")
    marker_file = os.path.join(params_dir, "params_model_1_multimer_v3.npz")

    if os.path.exists(marker_file):
        logger.info("AF2 weights already present at %s", params_dir)
        return

    logger.info("Downloading AF2 multimer weights to %s (this takes 3-5 min)...", cache_dir)
    os.makedirs(cache_dir, exist_ok=True)

    # Set ColabFold cache env so it downloads to our target directory
    os.environ["COLABFOLD_CACHE"] = cache_dir

    try:
        from colabfold.download import download_alphafold_params
        download_alphafold_params("alphafold2_multimer_v3")
        logger.info("AF2 weights downloaded successfully")
    except Exception as exc:
        logger.error("AF2 weight download failed: %s", exc)
        raise RuntimeError(f"Failed to download AF2 weights: {exc}")


# ===========================================================================
# Smoke/mini_pilot — Layer 2 preflight + result serialization
# ===========================================================================

def _write_smoke_failure(bucket: str, check: str, detail: str) -> None:
    """Write a structured preflight failure to SMOKE_RESULTS_PATH.

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
    """Fail-fast checks before any compute. ≤ 60 s on GPU.

    On any failure writes a structured error to SMOKE_RESULTS_PATH and
    calls sys.exit(1). See docs/SMOKE-TEST-SPEC.md section "Layer 2".
    """
    logger.info("=== Preflight ===")

    # 1. Payload keys.
    tier = payload.get("tier", "")
    if tier not in ("smoke", "mini_pilot"):
        # Legacy webhook mode — preflight is a no-op; main() handles it.
        return
    if "job_spec" not in payload:
        _write_smoke_failure("preflight", "payload", "missing key: job_spec")
        sys.exit(1)

    # 2. Target PDB accessible. In smoke/mini_pilot we use the baked fixture.
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

    # 4. Every CLI we will call responds.
    #    - python3 runs (trivially — we're running in it).
    #    - colabfold_batch --help exits 0 (only needed when skip_af2=False,
    #      but cheap to check always).
    try:
        result = subprocess.run(
            ["colabfold_batch", "--help"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            _write_smoke_failure(
                "preflight", "colabfold_batch --help",
                f"exit={result.returncode} stderr={result.stderr[-500:]}",
            )
            sys.exit(1)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _write_smoke_failure("preflight", "colabfold_batch", str(exc))
        sys.exit(1)

    # RFdiffusion's run_inference.py is Hydra-driven and doesn't have a
    # fast --help (it spins up the whole config stack). Assert the script
    # file exists instead.
    if not os.path.isfile(RFDIFFUSION_SCRIPT):
        _write_smoke_failure(
            "preflight", "rfdiffusion_script",
            f"not found at {RFDIFFUSION_SCRIPT}",
        )
        sys.exit(1)
    if not os.path.isfile(PROTEINMPNN_SCRIPT):
        _write_smoke_failure(
            "preflight", "proteinmpnn_script",
            f"not found at {PROTEINMPNN_SCRIPT}",
        )
        sys.exit(1)

    # 5. /tmp/smoke_results.json is writable.
    try:
        with open(SMOKE_RESULTS_PATH, "w") as fh:
            fh.write("{}")
    except OSError as exc:
        # Can't use _write_smoke_failure since writing is what failed.
        logger.error("Preflight: /tmp not writable: %s", exc)
        sys.exit(1)

    # 6. JAX persistent-cache visibility. We mount a Modal Volume at
    #    /root/.cache/jax so the 10-15 min XLA compile for AF2 multimer_v3
    #    only happens once. Log cache state so cold-vs-warm behaviour is
    #    obvious from the first few lines of the run log.
    jax_cache_dir = Path("/root/.cache/jax")
    try:
        jax_cache_dir.mkdir(parents=True, exist_ok=True)
        n_cached = sum(1 for p in jax_cache_dir.rglob("*") if p.is_file())
        logger.info("JAX persistent cache: %d files at %s", n_cached, jax_cache_dir)
        if n_cached == 0 and tier == "mini_pilot":
            logger.warning(
                "JAX cache cold — first AF2 run may take 10-15 min for XLA "
                "compile. Subsequent runs will reuse the Volume-backed cache."
            )
    except OSError as exc:
        logger.warning("Could not inspect JAX cache dir: %s", exc)

    logger.info("Preflight: OK (tier=%s)", tier)


def _build_smoke_job_spec(tier: str, overrides: dict | None = None) -> dict:
    """Build a job_spec dict for smoke/mini_pilot runs.

    Mirrors backend/pipelines/rfdiffusion.py::smoke_preset / mini_pilot_preset.
    Kept in sync manually because this script ships inside the Docker image
    and can't import from the backend package.

    ``overrides`` is the caller's ``job_spec`` from the payload. The smoke
    tier used to ignore it entirely and hardcode the single-chain PD-L1
    fixture, which made the multi-chain path untestable at this tier — the
    cheapest one, and the only one that returns results inline. Now
    ``target_chain`` and ``hotspot_residues`` are honoured when supplied, so
    a two-chain target can be smoked for the price of one design.

    Everything still defaults to the baked single-chain fixture, so a
    payload with no job_spec behaves exactly as before.
    """
    if tier == "smoke":
        parameters = {
            "num_designs": 1,
            "diffusion_steps": 50,
            "skip_af2": True,
            "binder_length": {"min": 55, "max": 65},
        }
    elif tier == "mini_pilot":
        parameters = {
            "num_designs": 2,
            "diffusion_steps": 50,
            "skip_af2": False,
            "binder_length": {"min": 55, "max": 65},
        }
    else:
        raise ValueError(f"Unknown tier: {tier}")

    overrides = overrides or {}
    target_chain = overrides.get("target_chain") or SMOKE_TARGET_CHAIN
    hotspots = overrides.get("hotspot_residues")
    if hotspots is None:
        hotspots = SMOKE_HOTSPOTS
    # A caller-supplied binder_length range is honoured too, so a large
    # multi-chain target can be smoked against a sensible binder size.
    caller_params = overrides.get("parameters") or {}
    if caller_params.get("binder_length"):
        parameters = {**parameters,
                      "binder_length": caller_params["binder_length"]}

    return {
        "tool": "rfdiffusion",
        "target_chain": target_chain,
        "hotspot_residues": list(hotspots),
        "parameters": parameters,
    }


def _stub_af2_scores(rank: int) -> dict:
    """Return deterministic plausible-looking scores for smoke tier.

    Marked filter_status='stub' so downstream code can tell these apart
    from real AF2 output.
    """
    return {
        "ipTM": round(0.45 + 0.01 * rank, 4),
        "pLDDT": round(70.0 + rank, 2),
        "i_pAE": round(12.0 - 0.1 * rank, 2),
        "filter_status": "stub (smoke)",
    }


def _encode_pdb(pdb_path: str) -> str:
    """Return base64 of the PDB bytes, per SMOKE-TEST-SPEC.md Layer 3."""
    return base64.b64encode(Path(pdb_path).read_bytes()).decode()


def _webhook_candidate_entry(c: dict) -> dict:
    """Project one candidate onto the webhook result schema.

    ``sequence`` is the designed binder's amino-acid sequence -- the
    actual deliverable of the run. It used to be dropped here, and the
    loss was silent and total: all 12 stored candidate-bearing jobs carry
    candidates with no sequence at all. Downstream in tools-hub that
    means the "re-fold with another predictor" button renders on every
    rfdiffusion results page and does nothing when clicked
    (``extract_top_n_sequences`` returns ``[]`` and the route redirects
    with no flash), and ``candidates_to_fasta`` skips every row, so
    ``export.fasta`` is empty on every job. tools-hub's own
    ``shared/refold.py`` lists rfdiffusion in ``SOURCE_TOOLS`` with the
    comment that "each candidate has a sequence" -- which was not true.
    The sequences survived only in ``designs/metrics.csv`` in Storage,
    which the web tier exposes nowhere.

    ``pdb_content_b64`` is inlined so candidate_table.html can render the
    3D-viewer and PDB-download buttons; without it the template falls
    through to the em-dash branch keyed on that field.
    """
    entry = {
        "rank": c["rank"],
        "pdb_key": c["pdb_key"],
        "scores": c["scores"],
        "sequence": c.get("sequence", ""),
    }
    local_file = c.get("local_file")
    if local_file and os.path.exists(local_file):
        try:
            entry["pdb_content_b64"] = _encode_pdb(local_file)
        except OSError as exc:
            logger.warning(
                "Failed to read PDB for rank %d (%s): %s",
                c["rank"], local_file, exc,
            )
    return entry


def _build_smoke_hydra_args(job_spec: dict, target_pdb_path: str) -> list[str]:
    """Like build_hydra_args but adds diffuser.T override for smoke speed."""
    args = build_hydra_args(job_spec, target_pdb_path)
    steps = job_spec.get("parameters", {}).get("diffusion_steps")
    if steps:
        # RFdiffusion's diffusion step-count is ``diffuser.T`` in its Hydra config.
        args.append(f"diffuser.T={int(steps)}")
    return args


def run_smoke_tier(
    tier: str,
    work_dir: str,
    job_spec_override: dict | None = None,
    input_url: str = "",
) -> dict:
    """Execute the RFdiffusion -> ProteinMPNN (-> AF2) pipeline for smoke/mini_pilot.

    Returns a dict shaped per SMOKE-TEST-SPEC.md Layer 3 "output shape".

    ``job_spec_override`` and ``input_url`` come from the payload. Supplying
    both is what makes a multi-chain target testable at this tier: the baked
    fixture is PD-L1 chain A, a single chain, so without a caller-supplied
    structure there is nothing here with two protomers to design against.
    Omit both and the run is byte-for-byte the historical single-chain smoke.
    """
    start = time.time()
    job_spec = _build_smoke_job_spec(tier, job_spec_override)
    params = job_spec["parameters"]
    skip_af2 = bool(params.get("skip_af2", False))
    num_designs = int(params.get("num_designs", 1))
    target_chain = job_spec["target_chain"]

    # Heartbeat config for live UI streaming. Reading env here lets the
    # smoke and mini_pilot path stream per design heartbeats from
    # stage_proteinmpnn and stage_af2_validation, the same way the
    # legacy full pilot path does. Without this both stages were getting
    # passed empty strings and every heartbeat was being suppressed.
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")

    # Resolve the target into the work dir so RFdiffusion can write adjacent.
    # A caller-supplied URL wins; otherwise the baked single-chain fixture.
    target_pdb = os.path.join(work_dir, "target.pdb")
    if input_url:
        raw_target = os.path.join(work_dir, "target_raw.pdb")
        download_input(input_url, raw_target)
        logger.info("Smoke target from payload URL: %s", input_url)
        # The webhook path sanitizes before RFdiffusion sees the structure;
        # a caller-supplied smoke target needs the same treatment, and it is
        # also what filters the structure down to the requested chains.
        from pipeline_normalize import normalize_for_rfdiffusion  # noqa: PLC0415
        norm_report = normalize_for_rfdiffusion(
            raw_target, target_pdb,
            target_chain=parse_target_chains(target_chain),
        )
        logger.info(
            "Normalize: chains_requested=%s chains_kept=%s chains_dropped=%s "
            "residues_kept=%s",
            norm_report.chains_requested, norm_report.chains_kept,
            norm_report.chains_dropped, norm_report.residues_kept_per_chain,
        )
    else:
        shutil.copy(SMOKE_TARGET_PDB, target_pdb)
        logger.info("Smoke target from baked fixture: %s", SMOKE_TARGET_PDB)

    # ---- Stage 1: RFdiffusion ----
    rfdiff_output = os.path.join(work_dir, "rfdiffusion_output")
    os.makedirs(rfdiff_output, exist_ok=True)

    hydra_args = _build_smoke_hydra_args(job_spec, target_pdb)
    rfdiff_cmd = [
        "python", RFDIFFUSION_SCRIPT,
        f"inference.output_prefix={rfdiff_output}/design",
        *hydra_args,
    ]
    try:
        run_command(rfdiff_cmd, timeout=1800)
    except RuntimeError as exc:
        return {
            "status": "FAILED",
            "error": {"bucket": "tool-invocation", "check": "rfdiffusion",
                      "detail": str(exc)[:2000]},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    backbone_pdbs = sorted(glob(os.path.join(rfdiff_output, "design_*.pdb")))
    if not backbone_pdbs:
        return {
            "status": "FAILED",
            "error": {"bucket": "output-parse", "check": "rfdiffusion",
                      "detail": "no design_*.pdb emitted"},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }
    logger.info("RFdiffusion emitted %d backbone PDBs", len(backbone_pdbs))

    # ---- Stage 2: ProteinMPNN ----
    mpnn_output = os.path.join(work_dir, "mpnn_output")
    try:
        designed_fastas = stage_proteinmpnn(
            backbone_pdbs, target_chain, mpnn_output,
            webhook_url=webhook_url, job_id=job_id,
            binder_length=params.get("binder_length"),
        )
    except RuntimeError as exc:
        return {
            "status": "FAILED",
            "error": {"bucket": "tool-invocation", "check": "proteinmpnn",
                      "detail": str(exc)[:2000]},
            "tier": tier,
            "gpu_seconds": int(time.time() - start),
        }

    # ---- Stage 3 (mini_pilot only): AF2 validation ----
    af2_results: list[dict] = []
    if skip_af2:
        # Stub scoring per SMOKE-TEST-SPEC.md: smoke tier scores may be stubbed.
        for idx, fasta_path in enumerate(designed_fastas):
            design_name = Path(fasta_path).stem
            af2_results.append({
                "design_name": design_name,
                "scores": _stub_af2_scores(idx + 1),
                "fasta_path": fasta_path,
            })
    else:
        af2_output = os.path.join(work_dir, "af2_output")
        try:
            af2_results = stage_af2_validation(
                designed_fastas, target_pdb, target_chain, af2_output,
                webhook_url=webhook_url, job_id=job_id, tier=tier,
            )
        except RuntimeError as exc:
            return {
                "status": "FAILED",
                "error": {"bucket": "tool-invocation", "check": "af2",
                          "detail": str(exc)[:2000]},
                "tier": tier,
                "gpu_seconds": int(time.time() - start),
            }
        if not af2_results:
            return {
                "status": "FAILED",
                "error": {"bucket": "output-parse", "check": "af2",
                          "detail": "zero AF2 results parsed"},
                "tier": tier,
                "gpu_seconds": int(time.time() - start),
            }

    # ---- Build candidates ----
    # Rank by ipTM desc (matches production path). For smoke/stubs, ipTM
    # grows with rank by construction, so we sort desc to keep behaviour
    # identical.
    af2_results.sort(key=lambda r: r["scores"].get("ipTM", 0.0), reverse=True)
    af2_results = af2_results[:num_designs]

    # Stamp filter_status on every score dict so the UI shows pass or
    # below threshold instead of a blank dash. Mirrors the labeling
    # semantics that main() applies. Skip rows where filter_status is
    # already set (skip_af2 stub rows carry "stub (smoke)").
    for r in af2_results:
        scores = r["scores"]
        if "filter_status" in scores:
            continue
        iptm = scores.get("ipTM")
        plddt = scores.get("pLDDT")
        ipae = scores.get("i_pAE")
        is_pass = (
            iptm is not None
            and plddt is not None
            and ipae is not None
            and iptm >= IPTM_THRESHOLD
            and plddt >= PLDDT_THRESHOLD
            and ipae <= IPAE_THRESHOLD
        )
        scores["filter_status"] = "pass" if is_pass else "below threshold"

    candidates = []
    for rank_idx, r in enumerate(af2_results):
        design_name = r["design_name"]
        # Ship the structure the scores describe. In mini_pilot, r["scores"] come from AF2
        # rank_001 in r["af2_dir"], so upload that COMPLEX, not the bare RFdiffusion backbone
        # (the backbone would contradict its own ipTM/pLDDT/i_pAE). In smoke the scores are
        # stubbed and no af2_dir exists, so the backbone is the right structure to ship. This
        # mirrors the fix in main()'s upload loop; the smoke path was the last place the
        # structure/score mismatch survived.
        backbone_pdb = None
        af2_dir = r.get("af2_dir")
        if af2_dir and os.path.isdir(af2_dir):
            for pat in ("*_relaxed_rank_001_*.pdb", "*_unrelaxed_rank_001_*.pdb",
                        "*rank_001*.pdb"):
                hits = sorted(glob(os.path.join(af2_dir, pat)))
                if hits:
                    backbone_pdb = hits[0]
                    break
            if not backbone_pdb:
                logger.warning(
                    "No AF2 complex for %s; falling back to the RFdiffusion backbone. The uploaded "
                    "structure will NOT correspond to its ipTM/pLDDT/i_pAE.", design_name,
                )
        if not backbone_pdb:
            # smoke tier (stubbed scores), or AF2-complex-missing fallback: use the backbone.
            backbone_pdb = os.path.join(rfdiff_output, f"{design_name}.pdb")
            if not os.path.exists(backbone_pdb) and backbone_pdbs:
                # Fallback: just take the i-th backbone.
                backbone_pdb = backbone_pdbs[min(rank_idx, len(backbone_pdbs) - 1)]
        if not os.path.exists(backbone_pdb):
            return {
                "status": "FAILED",
                "error": {"bucket": "output-parse", "check": "backbone_pdb",
                          "detail": f"no PDB found for {design_name}"},
                "tier": tier,
                "gpu_seconds": int(time.time() - start),
            }
        candidates.append({
            "rank": rank_idx + 1,
            "pdb_key": f"design_{rank_idx + 1:03d}.pdb",
            "pdb_content_b64": _encode_pdb(backbone_pdb),
            "scores": r["scores"],
            # Same deliverable, same omission as the production path had.
            # Empty when AF2 was stubbed, since no sequence was designed.
            "sequence": r.get("sequence", ""),
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
# Startup diagnostics
# ===========================================================================

def startup_check():
    """Log environment and dependency status at startup."""
    checks = {}
    try:
        import torch
        checks["torch"] = torch.__version__
        checks["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            checks["gpu"] = torch.cuda.get_device_name(0)
    except Exception as exc:
        checks["torch_error"] = str(exc)

    try:
        from Bio.PDB import PDBParser
        checks["biopython"] = "ok"
    except Exception as exc:
        checks["biopython_error"] = str(exc)

    for label, path in [
        ("rfdiffusion_script", RFDIFFUSION_SCRIPT),
        ("proteinmpnn_script", PROTEINMPNN_SCRIPT),
        ("proteinmpnn_weights", f"{PROTEINMPNN_WEIGHTS}/v_48_020.pt"),
        ("models_dir", MODELS_DIR),
    ]:
        checks[label] = os.path.exists(path)

    # Check for RFdiffusion weights on network volume
    for weight_file in ["Base_ckpt.pt", "Complex_base_ckpt.pt"]:
        checks[f"weight_{weight_file}"] = os.path.exists(
            os.path.join(MODELS_DIR, weight_file)
        )

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

    Derives the heartbeat URL from the main webhook URL by replacing
    the /webhooks/runpod path with /webhooks/heartbeat.

    Args:
        webhook_url: The main RunPod webhook URL.
        job_id: Kendrew job UUID.
        stage: Current pipeline stage description.
        designs_completed: Number of designs finished so far.
        designs_total: Total designs requested.
        new_candidate: Optional per-design candidate dict. When supplied, the
            heartbeat carries it through tools-hub's /webhooks/heartbeat for
            live UI streaming. tools-hub validates it server-side via
            JOB_TOKEN and projects it to a fixed schema, so a malformed
            candidate is dropped silently. We add a try/except around the
            assignment so a bad shape cannot crash the pipeline.
    """
    from urllib.parse import urlparse, urlunparse
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


class _HeartbeatThread:
    """Background thread that emits heartbeats during long subprocess runs.

    The subprocesses in this pipeline (RFdiffusion inference, ProteinMPNN,
    and especially colabfold_batch AF2 prediction) can block Python for
    10-25+ minutes with zero stdout. ``run_command`` uses
    ``subprocess.run(capture_output=True)`` which means nothing streams
    while the child is alive — so the backend's last_heartbeat_at never
    updates and the stale-detection cron kills a perfectly healthy job.

    Use as a context manager around each long-running subprocess:

        with _HeartbeatThread(webhook_url, job_id, stage="..."):
            run_command(cmd, timeout=1800)

    The thread is a daemon so it cannot prevent process exit. See
    cleanup.py:STALE_HEARTBEAT_SECONDS for the corresponding backend
    threshold.
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
        # Fire one immediately so the backend's last_heartbeat_at updates
        # right away, then keep pinging every ``interval_seconds`` until
        # stopped.
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
        url, data=data, headers={"Content-Type": content_type}, timeout=120
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Upload failed for {file_path}: HTTP {resp.status_code}")
    logger.info("Uploaded %s (%d bytes)", file_path, len(data))


def run_command(
    cmd: list[str],
    timeout: int = 3600,
    cwd: str | None = None,
    env: dict | None = None,
) -> str:
    """Run a subprocess command with timeout and logging."""
    logger.info("Running: %s", " ".join(cmd[:6]) + ("..." if len(cmd) > 6 else ""))
    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
    )
    elapsed = time.time() - start
    logger.info("Command finished in %.1fs (exit code %d)", elapsed, result.returncode)
    if result.returncode != 0:
        error_tail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(f"Command failed (exit {result.returncode}): {error_tail}")
    return result.stdout + result.stderr


# ===========================================================================
# Raw output capture
# ===========================================================================

# Fixed path the Modal wrapper (infrastructure/modal/rfdiffusion_app.py) reads
# after this subprocess exits, in the same read-a-file-from-/tmp style as
# SMOKE_RESULTS_PATH and _WEBHOOK_OUTCOME_PATH. The pipeline runs as a
# subprocess and so cannot mount a Volume itself; the wrapper moves this file
# onto one. It sits OUTSIDE every work dir we create (all of them
# tempfile.mkdtemp() dirs of the form /tmp/<prefix><random>/), so the tar can
# never land inside the tree it is archiving.
RAW_ARCHIVE_PATH = "/tmp/raw_archive.tgz"


def ship_raw_archive(work_dir: str, dest: str = RAW_ARCHIVE_PATH) -> None:
    """Tar the COMPLETE work dir to ``dest`` immediately before it is deleted.

    A container must never decide which fields are worth keeping. This pipeline
    keeps 3 numbers per design (ipTM, pLDDT, i_pAE) out of ColabFold's full
    score JSON, discards every RFdiffusion trajectory, every ProteinMPNN FASTA
    and every AF2 model that is not rank_001, and then rmtree's the lot. A
    sibling pipeline lost 460 designs exactly this way: it reported ``iptm``
    (interface-pTM averaged over EVERY chain pair) where it meant
    ``design_iptm`` (binder -> target), read ~2x high, and the evidence needed
    to catch it had already died with the container. Ship the tree home and
    decide LOCALLY, where re-parsing is free.

    Unconditional by design: not gated on candidates, on success, or on
    anything being uploaded. A run that produced zero candidates or crashed
    outright is precisely the run whose tree you need.

    Best-effort: capture must NEVER fail the run, so every problem is logged
    and nothing is raised.
    """
    import tarfile

    try:
        if not work_dir or not os.path.isdir(work_dir):
            logger.warning(
                "Raw capture: no work dir to archive (%s); nothing to ship", work_dir,
            )
            return

        # The tar MUST NOT be written inside the tree it archives, or it tars
        # itself. RAW_ARCHIVE_PATH is outside every mkdtemp work dir we make,
        # but assert it rather than trust it: a future caller passing a
        # work_dir of "/tmp" would otherwise produce a self-referencing tar.
        abs_dest = os.path.abspath(dest)
        abs_work = os.path.abspath(work_dir)
        if os.path.commonpath([abs_dest, abs_work]) == abs_work:
            logger.error(
                "Raw capture: refusing to write %s inside the tree it archives (%s)",
                abs_dest, abs_work,
            )
            return

        # Stream straight to a file. Never io.BytesIO: an in-memory tar costs
        # ~3-4x the archive size in peak RSS, and an AF2 output tree runs to
        # hundreds of MB.
        with tarfile.open(abs_dest, "w:gz") as tf:
            tf.add(abs_work, arcname=os.path.basename(abs_work.rstrip("/")) or "work")
        logger.info(
            "Raw capture: archived %s -> %s (%.1f MB)",
            abs_work, abs_dest, os.path.getsize(abs_dest) / 1e6,
        )
    except Exception as exc:
        # Capture is best-effort by design. A job that crashed before writing
        # output is exactly when the diagnostics matter most, so a failure here
        # must never mask the real error or fail the run.
        logger.error(
            "Raw capture failed (non-fatal): %s: %s", type(exc).__name__, exc,
        )
        # A crash mid-write (e.g. ENOSPC) can leave a truncated but still-openable .tgz at
        # the destination; the wrapper parks whatever exists. Remove the partial so a failed
        # capture parks NOTHING rather than a tar that reports success but cannot be read.
        try:
            if os.path.exists(abs_dest):
                os.remove(abs_dest)
        except OSError:
            pass


_WEBHOOK_OUTCOME_PATH = "/tmp/webhook_outcome.json"


def _record_webhook_outcome(delivered: bool, detail: str) -> None:
    """Persist webhook delivery status so the Modal wrapper can surface
    it to the consuming web service even when the POST silently fails. Read by run_tool()
    in infrastructure/modal/rfdiffusion_app.py and merged into the function
    return value, where the web service's poller inspects it."""
    try:
        with open(_WEBHOOK_OUTCOME_PATH, "w") as fh:
            json.dump({"delivered": delivered, "detail": detail}, fh)
    except OSError as exc:
        logger.error("Failed to write webhook outcome file: %s", exc)


def post_webhook(webhook_url: str, job_id: str, pod_id: str, payload: dict) -> None:
    """POST results to the Kendrew backend webhook.

    Signs the body with HMAC-SHA256 against WEBHOOK_HMAC_SECRET (injected
    by the Modal app via a Modal Secret). The backend
    (webhooks/router.py:validate_webhook_signature) validates against the
    SAME secret (Phase 11 D-10 dual-secret rotation supports a _PREV value
    during rotation windows). Without the signature, the backend returns 401
    and the completion notification never lands. Discovered live during the
    2026-06-03 SC 6 close-out attempt.

    Args:
        webhook_url: Backend webhook endpoint URL.
        job_id: Kendrew job UUID.
        pod_id: RunPod pod ID (for backend to terminate).
        payload: Results dict (candidates, counts, etc.).
    """
    import hashlib
    import hmac

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
        body["error"] = {"category": "Pipeline error", "message": payload["error"]}

    # Serialize body to bytes BEFORE signing, then post the same bytes so
    # the HMAC matches what the backend re-hashes. Using `json=body` would
    # let requests apply its own serialization (different whitespace,
    # different sort order) and the signature would mismatch.
    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")

    secret = os.environ.get("WEBHOOK_HMAC_SECRET", "")
    headers = {"Content-Type": "application/json"}
    if secret:
        signature = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Modal-Signature"] = signature
    else:
        # No secret in env means Modal Secret isn't attached -- backend will
        # 401 in prod (where webhook_hmac_secret is set). Log loudly so the
        # next deploy notices.
        logger.warning(
            "post_webhook: WEBHOOK_HMAC_SECRET not in env; backend will 401. "
            "Modal Secret 'ranomics-webhook' must be attached to the app."
        )

    logger.info("Posting webhook to %s", webhook_url)
    try:
        resp = requests.post(webhook_url, data=body_bytes, headers=headers, timeout=30)
        logger.info("Webhook response: %d", resp.status_code)
        resp.raise_for_status()
        _record_webhook_outcome(True, f"http {resp.status_code}")
    except Exception as exc:
        logger.error("Webhook POST failed: %s", exc)
        _record_webhook_outcome(False, f"{type(exc).__name__}: {exc}")


# ===========================================================================
# AF2 score parsing
# ===========================================================================

def parse_af2_scores(
    result_dir: str, design_name: str, n_target_chains: int = 1,
    target_residues: int | None = None,
) -> dict | None:
    """Extract ipTM, pLDDT, and i_pAE from ColabFold AF2 prediction output.

    ``n_target_chains`` tells the interface-PAE calculation how many leading
    chains of the complex are target. The AF2 FASTA is written target
    chain(s) first, binder last, so the interface boundary sits after the
    target block. Defaults to 1 — the historical single-chain shape.

    ``target_residues`` is the authoritative residue count of that target
    block, taken from the sequences we actually wrote into the FASTA. Prefer
    it: ColabFold's scores JSON does NOT carry per-chain lengths, so a
    boundary derived from the JSON alone silently degrades to a guess. It is
    also what splits ``pLDDT`` (the binder's) from ``complex_pLDDT``.

    ``pLDDT`` is the mean over the BINDER residues only; ``complex_pLDDT``
    is the whole-complex mean this used to report under the ``pLDDT`` name.
    See the comment in the body for the production measurement.
    """
    score_files = glob(os.path.join(result_dir, f"{design_name}*scores*.json"))
    if not score_files:
        score_files = glob(os.path.join(result_dir, "*scores*.json"))
    if not score_files:
        logger.warning("No AF2 score files found for %s", design_name)
        return None

    try:
        with open(score_files[0]) as fh:
            data = json.load(fh)

        iptm = float(data.get("iptm", 0.0))
        plddt_values = data.get("plddt", [])
        complex_plddt = (
            sum(plddt_values) / len(plddt_values) if plddt_values else 0.0
        )

        # ``pLDDT`` is the BINDER's, not the complex's. ColabFold's
        # per-residue plddt list is in FASTA order, so it carries the same
        # target-first layout the interface-PAE boundary below already
        # relies on.
        #
        # The complex mean is not a design-quality metric here: the target
        # block outnumbers the binder several to one, so it sets the number
        # and the binder only nudges it. Measured on production run
        # d44865ae (SARS-CoV-2 RBD, 194 target residues + 61 binder
        # residues in design_001; the job's binder mean is 59.4, and 61 is
        # the shape the arithmetic below uses): target pLDDT held 26.8-28.0
        # across every design while binder pLDDT ranged 44.0-65.7, and the
        # reported complex mean compressed that 22-point spread into 5
        # points (31.1-36.7). Across three jobs the target never left
        # 26.6-28.6 and the binder spanned 44.0-82.0 -- the whole signal,
        # averaged away.
        #
        # The compression also puts PLDDT_THRESHOLD out of arithmetic
        # reach: with 194 of 255 residues pinned near 28, a binder at a
        # perfect 100 still only reports (194*28 + 61*100)/255 = 45.2, so
        # the pLDDT leg of filter_status could never be cleared on this
        # shape.
        #
        # That leg is not why designs were labelled "below threshold",
        # though. filter_status is an AND of three legs, and on the same
        # 115 stored candidates the ipTM leg (0.06-0.13 vs a 0.70 bar) and
        # the i_pAE leg (21.45-27.20 vs a 10.0 bar) each fail on their own,
        # independently of pLDDT. Reporting the binder mean restores the
        # metric's information content; it flips no verdict. Applied to the
        # 28 scored designs it moves 0 of them to "pass", and only 2 of the
        # 28 clear even the pLDDT leg (80.77 and 81.98). The label changes
        # only once the ipTM/i_pAE causes are addressed too.
        binder_plddt = None
        if target_residues and 0 < int(target_residues) < len(plddt_values):
            tail = plddt_values[int(target_residues):]
            binder_plddt = sum(tail) / len(tail)
        else:
            logger.warning(
                "pLDDT: no usable target/binder boundary (target_residues=%r, "
                "n_residues=%d); falling back to the COMPLEX mean, which is "
                "dominated by the target and is not a design-quality metric.",
                target_residues, len(plddt_values),
            )

        pae_matrix = data.get("pae", [])
        ipae = (
            _compute_interface_pae(
                pae_matrix, data, n_target_chains, target_residues,
            )
            if pae_matrix else 99.0
        )

        return {
            "ipTM": round(iptm, 4),
            "pLDDT": round(
                complex_plddt if binder_plddt is None else binder_plddt, 2
            ),
            "i_pAE": round(ipae, 2),
            # Appended last so a reader diffing this dict sees an added
            # key rather than a reordered one. Nothing depends on the
            # position: every consumer reads these by name (ranking.py
            # flattens with pd.json_normalize and indexes by column name),
            # so moving it would be harmless and no test enforces it.
            "complex_pLDDT": round(complex_plddt, 2),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse AF2 scores for %s: %s", design_name, exc)
        return None


def _compute_interface_pae(
    pae_matrix: list, score_data: dict, n_target_chains: int = 1,
    target_residues: int | None = None,
) -> float:
    """Compute mean interface PAE from the PAE matrix.

    The complex is laid out target chain(s) first, binder last, so the
    target/binder boundary is the total length of the target block. Getting
    it wrong is silent: on a two-chain target a boundary of
    ``chain_lengths[0]`` reclassifies the second protomer as binder-side and
    averages PAE over the wrong interface, with no visible symptom.

    Boundary sources, in order of trust:

    1. ``target_residues`` — the summed length of the target sequences this
       pipeline wrote into the AF2 FASTA. Authoritative.
    2. ``score_data["chain_lengths"]`` — accepted if present, but ColabFold
       does NOT emit this key (its scores JSON carries plddt/pae/ptm/iptm/
       max_pae only), so in practice this never fires.
    3. ``total_res // 2`` — a guess, correct only when the binder happens to
       be the same size as the whole target. It is wrong on the historical
       single-chain path too: a 116-residue target plus a 60-residue binder
       gives 88 instead of 116. Logged loudly, because an i_pAE computed
       across a fabricated boundary still looks like a normal number and
       still gates ``filter_status`` against ``IPAE_THRESHOLD``.
    """
    total_res = len(pae_matrix)
    if total_res == 0:
        return 99.0

    n_target_chains = max(1, int(n_target_chains))
    chain_lengths = score_data.get("chain_lengths", None)
    if target_residues and 0 < int(target_residues) < total_res:
        boundary = int(target_residues)
    elif chain_lengths and len(chain_lengths) >= n_target_chains + 1:
        boundary = sum(chain_lengths[:n_target_chains])
    else:
        boundary = total_res // 2
        logger.warning(
            "i_pAE: no authoritative target length (target_residues=%r, "
            "chain_lengths=%r) for a %d-residue complex; falling back to a "
            "midpoint boundary of %d. i_pAE is computed across a GUESSED "
            "interface and should not be trusted for filtering.",
            target_residues, chain_lengths, total_res, boundary,
        )

    ipae_values = []
    for row_idx in range(total_res):
        for col_idx in range(total_res):
            row_is_target = row_idx < boundary
            col_is_target = col_idx < boundary
            if row_is_target != col_is_target:
                ipae_values.append(pae_matrix[row_idx][col_idx])

    return sum(ipae_values) / len(ipae_values) if ipae_values else 99.0


# ===========================================================================
# Pipeline stage functions
# ===========================================================================

def _get_chain_residue_range(pdb_path: str, chain_id: str) -> tuple[int, int]:
    """Get the first and last residue numbers for a chain in a PDB file.

    Args:
        pdb_path: Path to the PDB file.
        chain_id: Chain identifier (e.g., "A").

    Returns:
        Tuple of (first_resnum, last_resnum).

    Raises:
        RuntimeError: If the chain is not found or has no residues.
    """
    residue_nums = _get_chain_residue_numbers(pdb_path, chain_id)
    return residue_nums[0], residue_nums[-1]


def _get_chain_residue_numbers(pdb_path: str, chain_id: str) -> list:
    """Sorted, de-duplicated standard-residue numbers for one chain.

    Insertion codes are collapsed onto their base number, matching how
    RFdiffusion keys its own ``parsed_pdb["pdb_idx"]``.

    Raises:
        RuntimeError: If the chain is not found or has no residues.
    """
    try:
        from Bio.PDB import PDBParser
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("target", pdb_path)

        for model in structure:
            for chain in model:
                if chain.id == chain_id:
                    residue_nums = sorted({
                        r.id[1] for r in chain
                        if r.id[0] == " "  # Standard residues only (skip HETATM)
                    })
                    if residue_nums:
                        return residue_nums

        raise RuntimeError(f"Chain {chain_id} not found in {pdb_path}")
    except ImportError:
        raise RuntimeError("Biopython is required to parse PDB residue ranges")


def _contiguous_runs(residue_nums: list) -> list:
    """Group ascending residue numbers into contiguous ``(first, last)`` runs.

    ``[18, 19, 20, 45, 46]`` -> ``[(18, 20), (45, 46)]``. A gap-free chain
    yields exactly one run, which is what keeps the single-chain contig
    byte-identical to the historical output.
    """
    runs = []
    start = prev = residue_nums[0]
    for num in residue_nums[1:]:
        if num == prev + 1:
            prev = num
            continue
        runs.append((start, prev))
        start = prev = num
    runs.append((start, prev))
    return runs


def parse_target_chains(target_chain) -> list:
    """Parse ``job_spec["target_chain"]`` into an ordered chain list.

    Thin wrapper over ``pipeline_normalize.parse_target_chains`` with a
    guaranteed-non-empty result — RFdiffusion always needs at least one
    named target chain to build a contig from.
    """
    from pipeline_normalize import parse_target_chains as _parse  # noqa: PLC0415

    chains = _parse(target_chain)
    if not chains:
        raise ValueError(
            f"target_chain must name at least one chain; got {target_chain!r}"
        )
    return chains


def build_contig_string(
    target_pdb_path: str, target_chains: list, binder_min: int, binder_max: int,
) -> str:
    """Build ``contigmap.contigs`` for a fixed target plus a diffused binder.

    Multi-chain fixed targets are separated by a ``/0 `` chain break —
    upstream is explicit that THE SPACE AFTER /0 IS REQUIRED; it tells the
    model to insert a large residue gap so the chains are treated as
    separate entities rather than one continuous polymer.

        one chain : [A18-132/0 50-100]
        two chains: [A1-223/0 B1-223/0 50-70]
        gapped    : [A18-132/0 B33-84/B86-146/0 55-65]

    Residue ranges are read from the PDB per chain rather than assumed, and
    a chain is emitted as one segment PER CONTIGUOUS RUN, joined by ``/``
    within the chain. A dense ``{chain}{min}-{max}`` span is wrong whenever
    the chain has a numbering gap: RFdiffusion expands the span into every
    integer in the range and asserts each one exists in the input PDB, so a
    single unmodelled loop residue aborts the run with
    ``AssertionError: ('B', 85) is not in pdb file!``. Crystal structures of
    oligomers almost always have disordered loops, and the normalizer can
    open a gap itself by dropping a residue with an incomplete backbone.
    """
    segments = []
    for chain in target_chains:
        residue_nums = _get_chain_residue_numbers(target_pdb_path, chain)
        runs = _contiguous_runs(residue_nums)
        logger.info(
            "Chain %s: %d residues spanning %d-%d in %d contiguous run(s)",
            chain, len(residue_nums), residue_nums[0], residue_nums[-1],
            len(runs),
        )
        if len(runs) > 1:
            logger.info(
                "Chain %s has %d numbering gap(s); emitting %d separate contig "
                "segments so RFdiffusion is never asked to fix a residue the "
                "PDB does not contain: %s",
                chain, len(runs) - 1, len(runs),
                ", ".join(f"{a}-{b}" for a, b in runs),
            )
        segments.append("/".join(f"{chain}{a}-{b}" for a, b in runs))
    segments.append(f"{binder_min}-{binder_max}")
    return "[" + "/0 ".join(segments) + "]"


def build_hotspot_string(
    hotspots, target_chains: list, target_pdb_path: str | None = None,
) -> str:
    """Build ``ppi.hotspot_res``, e.g. ``[A30,A33,B264]``.

    Accepts already-chain-qualified tokens ("A296", "B264") and bare ints
    (attributed to the first target chain, the historical shape). Hotspots
    may span chains — that is the point for an oligomeric target, where a
    binder grips both protomers.

    When ``target_pdb_path`` is given, every hotspot is checked against the
    residues actually present after cleanup and an unmatched one is a hard
    error. Upstream RFdiffusion intersects hotspot tokens against the parsed
    PDB index and DROPS non-matching ones with no warning and no assert, so
    a mistyped or dropped residue yields an all-zero hotspot tensor: the run
    completes, ProteinMPNN designs sequences, AF2 returns normal-looking
    scores, and the binder is aimed at an arbitrary surface patch. PXDesign
    and BoltzGen already refuse this input; RFdiffusion did not.
    """
    from pipeline_normalize import parse_hotspots  # noqa: PLC0415

    parsed = parse_hotspots(hotspots, target_chains)

    if target_pdb_path and parsed:
        present = {
            chain: set(_get_chain_residue_numbers(target_pdb_path, chain))
            for chain in target_chains
        }
        missing = [
            f"{chain}{res}" for chain, res in parsed
            if res not in present.get(chain, ())
        ]
        if missing:
            raise ValueError(
                f"Hotspot residue(s) {missing} are not present in the target "
                f"structure after cleanup. Requested: {list(hotspots)!r}; "
                f"target chains: {target_chains}. RFdiffusion silently "
                f"discards hotspot tokens it cannot match, so continuing "
                f"would run an untargeted design that still completes and "
                f"still scores — wrong only at the bench. Refusing to "
                f"continue."
            )

    return "[" + ",".join(f"{chain}{res}" for chain, res in parsed) + "]"


def _resolve_binder_range(binder_length) -> tuple:
    """Resolve ``parameters["binder_length"]`` to a ``(min, max)`` pair.

    The field arrives in two shapes and both are load-bearing: a
    ``{"min": .., "max": ..}`` dict from the tools-hub campaign path, and a
    BARE INT from the agent wizard, which declares it
    ``param_type="int", default=80`` (backend/agent/wizard.py). Anything
    reading this field must tolerate both.

    A non-dict resolves to the historical ``(50, 100)`` rather than
    ``(n, n)``: ``build_hydra_args`` has always built the contig from that
    default when handed an int, so pinning the range to the int here would
    reject a legitimately sampled binder as a chain swap. That the wizard's
    requested length is ignored by the contig builder is a separate,
    pre-existing bug — see docs/MULTI-CHAIN-TARGETS.md.

    Both the request side (``build_hydra_args``) and the validation side
    (``infer_binder_chain``) go through this one function so they cannot
    disagree about what was asked for.
    """
    if isinstance(binder_length, dict):
        return (
            int(binder_length.get("min", 50)),
            int(binder_length.get("max", 100)),
        )
    return 50, 100


def build_hydra_args(job_spec: dict, target_pdb_path: str) -> list[str]:
    """Build RFdiffusion Hydra CLI override args from JobSpec parameters."""
    params = job_spec.get("parameters", {})
    target_chains = parse_target_chains(job_spec.get("target_chain", "A"))
    hotspots = job_spec.get("hotspot_residues", [])

    binder_length = params.get("binder_length", {"min": 50, "max": 100})
    binder_min, binder_max = _resolve_binder_range(binder_length)

    num_designs = params.get("num_designs", 10)

    # Read actual residue range from PDB instead of hardcoding
    contig_str = build_contig_string(
        target_pdb_path, target_chains, binder_min, binder_max,
    )

    # Same null hazard as noise_scale below: os.path.join rejects None.
    checkpoint = params.get("checkpoint") or "Complex_base_ckpt.pt"
    ckpt_path = os.path.join(MODELS_DIR, checkpoint)

    # RFdiffusion injects noise at every denoising step. The default of
    # 1.0 is tuned for unconditional monomer generation; for BINDER design
    # the authors' own recipe sets both scales to 0, on the grounds that
    # less noise yields more designable backbones. This wrapper never set
    # them at all, so every production binder was diffused at full
    # inference noise.
    #
    # Circumstantial support from the 11 stored production jobs: binders
    # come out 80-93% helical with Rg 13.8-18.9 A at 55-65 residues --
    # loose helical bundles -- and the sequences ProteinMPNN then puts on
    # them are degenerate. Measured inside the delivered structures, the
    # binder chain uses 12-14 of the 20 residue types with one type at
    # 25-30%, while the natural target chain in the very same file uses 19
    # and tops out at 9%. One design is 32 consecutive alanines.
    #
    # Overridable so the A/B runs from ONE deploy instead of two:
    # noise_scale=1.0 reproduces the previous behaviour exactly.
    # .get(..., 0.0) is not enough: an agent override can carry an
    # explicit JSON null, which .get returns as None and float()
    # rejects -- killing the run inside an already-billed GPU
    # container. A null means "unspecified", so treat it as absent.
    _raw_noise = params.get("noise_scale")
    noise_scale = 0.0 if _raw_noise is None else float(_raw_noise)

    hydra_args = [
        f"inference.input_pdb={target_pdb_path}",
        f"contigmap.contigs={contig_str}",
        f"inference.num_designs={num_designs}",
        f"inference.ckpt_override_path={ckpt_path}",
        f"denoiser.noise_scale_ca={noise_scale}",
        f"denoiser.noise_scale_frame={noise_scale}",
    ]

    if hotspots:
        hydra_args.append(
            "ppi.hotspot_res="
            + build_hotspot_string(hotspots, target_chains, target_pdb_path)
        )

    logger.info(
        "Hydra args: target_chains=%s contigs=%s hotspots=%s",
        target_chains, contig_str, hotspots,
    )
    return hydra_args


def stage_rfdiffusion(
    target_pdb: str,
    job_spec: dict,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
) -> list[str]:
    """Stage 1: Run RFdiffusion backbone generation."""
    logger.info("=== Stage 1: RFdiffusion backbone generation ===")
    num_designs = job_spec.get("parameters", {}).get("num_designs", 10)
    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running RFdiffusion", 0, num_designs)
    hydra_args = build_hydra_args(job_spec, target_pdb)

    cmd = [
        "python", RFDIFFUSION_SCRIPT,
        f"inference.output_prefix={output_dir}/design",
        *hydra_args,
    ]
    # RFdiffusion inference for 10 designs on A10G takes ~15 min with no
    # stdout streamed (run_command captures output). Wrap in a heartbeat
    # thread so the stale-detection cron doesn't kill a healthy job.
    with _HeartbeatThread(
        webhook_url, job_id,
        stage="Running RFdiffusion",
        designs_completed=0, designs_total=num_designs,
        interval_seconds=60,
    ):
        run_command(cmd, timeout=1800)

    generated = sorted(glob(os.path.join(output_dir, "design_*.pdb")))
    logger.info("RFdiffusion generated %d backbone PDBs", len(generated))
    if not generated:
        raise RuntimeError("RFdiffusion produced no output PDB files")
    return generated


def _chains_in_pdb(pdb_path: str) -> list:
    """Chain ids present in a PDB's ATOM records, in file order."""
    seen: list = []
    with open(pdb_path) as fh:
        for line in fh:
            if line.startswith(("ATOM", "HETATM")) and len(line) > 21:
                cid = line[21]
                if cid not in seen:
                    seen.append(cid)
    return seen


def _residue_counts_per_chain(pdb_path: str) -> dict:
    """{chain_id: residue count} from a PDB's ATOM records."""
    seen: dict = {}
    per_chain: dict = {}
    with open(pdb_path) as fh:
        for line in fh:
            if not line.startswith("ATOM") or len(line) <= 26:
                continue
            cid = line[21]
            key = (cid, line[22:27])
            if key in seen:
                continue
            seen[key] = True
            per_chain[cid] = per_chain.get(cid, 0) + 1
    return per_chain


def infer_binder_chain(
    backbone_pdb: str,
    target_chains: list,
    binder_length: dict | None = None,
) -> str:
    """Identify the diffused binder chain in an RFdiffusion output backbone.

    Read from the file rather than assumed. The previous
    ``"B" if target_chain == "A" else "A"`` hardcoded exactly two chains,
    so any N-chain target silently fixed the wrong chain in ProteinMPNN and
    still produced sequences — designs against a target the model was told
    to redesign.

    Two independent signals, because a swap here is invisible in every
    downstream score:

    1. Exactly one chain in the output must not be a target chain.
    2. If ``binder_length`` is supplied, that chain's residue count must
       fall inside the requested range. This is what catches a swap: the
       target protomers are hundreds of residues, the binder tens, so a
       letter-level mix-up shows up immediately as an out-of-range length
       rather than as a plausible design against the wrong molecule.

    Raises on either failure rather than guessing.
    """
    counts = _residue_counts_per_chain(backbone_pdb)
    present = _chains_in_pdb(backbone_pdb)
    candidates = [c for c in present if c not in set(target_chains)]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Cannot identify the binder chain in {backbone_pdb}: chains "
            f"present are {present} (residues {counts}), target chains are "
            f"{target_chains}, leaving {candidates} as binder candidate(s). "
            f"Expected exactly one. Refusing to guess — fixing the wrong "
            f"chain in ProteinMPNN still yields plausible sequences."
        )

    binder_chain = candidates[0]
    if binder_length:
        # Validate against the SAME range build_hydra_args asked RFdiffusion
        # for. Calling .get() directly here crashed every agent/wizard job,
        # which passes binder_length as a bare int, after Stage 1 had already
        # been paid for.
        lo, hi = _resolve_binder_range(binder_length)
        got = counts.get(binder_chain, 0)
        # RFdiffusion samples a length in [lo, hi]; allow a 1-residue slack
        # for off-by-one differences in how the contig range is realized.
        if not (lo - 1 <= got <= hi + 1):
            raise RuntimeError(
                f"Chain {binder_chain!r} was identified as the binder in "
                f"{backbone_pdb} but has {got} residues, outside the "
                f"requested binder length range {lo}-{hi}. Per-chain residue "
                f"counts: {counts}; target chains: {target_chains}. This is "
                f"what a target/binder chain swap looks like — refusing to "
                f"continue rather than design against the wrong chain."
            )

    logger.info(
        "Binder chain inferred as %r (backbone chains=%s, residues=%s, "
        "targets=%s)",
        binder_chain, present, counts, target_chains,
    )
    return binder_chain


def stage_proteinmpnn(
    backbone_pdbs: list[str],
    target_chain,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
    binder_length: dict | None = None,
) -> list[str]:
    """Stage 2: Run ProteinMPNN sequence design on each backbone.

    ``target_chain`` accepts one chain ("A") or several ("A,B").
    ``binder_length`` is the requested ``{"min": .., "max": ..}`` range; when
    given it cross-checks the inferred binder chain against its residue
    count, which is what catches a target/binder chain swap.
    """
    logger.info("=== Stage 2: ProteinMPNN sequence design ===")
    os.makedirs(output_dir, exist_ok=True)
    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running ProteinMPNN", 0, len(backbone_pdbs))

    target_chains = parse_target_chains(target_chain)
    binder_chain = infer_binder_chain(
        backbone_pdbs[0], target_chains, binder_length=binder_length,
    )

    # Step 1: Parse all backbone PDBs into JSONL format
    parsed_jsonl = os.path.join(output_dir, "parsed_pdbs.jsonl")
    parse_cmd = [
        "python", "/opt/ProteinMPNN/helper_scripts/parse_multiple_chains.py",
        "--input_path", os.path.dirname(backbone_pdbs[0]),
        "--output_path", parsed_jsonl,
    ]
    run_command(parse_cmd, timeout=120)

    # Step 2: Assign chains — design binder chain, fix target chain
    assigned_jsonl = os.path.join(output_dir, "assigned_pdbs.jsonl")
    assign_cmd = [
        "python", "/opt/ProteinMPNN/helper_scripts/assign_fixed_chains.py",
        "--input_path", parsed_jsonl,
        "--output_path", assigned_jsonl,
        "--chain_list", binder_chain,
    ]
    run_command(assign_cmd, timeout=120)

    # Step 3: Run ProteinMPNN on all backbones in one batch
    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running ProteinMPNN", 0, len(backbone_pdbs))

    cmd = [
        "python", PROTEINMPNN_SCRIPT,
        "--jsonl_path", parsed_jsonl,
        "--chain_id_jsonl", assigned_jsonl,
        "--out_folder", output_dir,
        "--num_seq_per_target", "2",
        "--sampling_temp", "0.1",
        "--batch_size", "1",
    ]

    # ProteinMPNN is typically fast (~20-60s) but wrap defensively: large
    # backbone batches or cold GPU init could push past 10 min with no
    # streamed stdout.
    with _HeartbeatThread(
        webhook_url, job_id,
        stage="Running ProteinMPNN",
        designs_completed=0, designs_total=len(backbone_pdbs),
        interval_seconds=60,
    ):
        run_command(cmd, timeout=600, cwd="/opt/ProteinMPNN")

    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "ProteinMPNN complete", len(backbone_pdbs), len(backbone_pdbs))

    # Collect all output FASTAs
    fasta_files = glob(os.path.join(output_dir, "seqs", "*.fa"))
    if not fasta_files:
        raise RuntimeError("ProteinMPNN produced no FASTA output")

    logger.info("ProteinMPNN produced %d designed sequences", len(fasta_files))
    return fasta_files


def _extract_sequences_from_fasta(fasta_path: str) -> dict[str, str]:
    """Parse a FASTA file and return {header: sequence} dict."""
    sequences = {}
    current_header = None
    current_seq = []

    with open(fasta_path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(">"):
                if current_header is not None:
                    sequences[current_header] = "".join(current_seq)
                current_header = line[1:]
                current_seq = []
            else:
                current_seq.append(line)

    if current_header is not None:
        sequences[current_header] = "".join(current_seq)
    return sequences


def _extract_target_sequence(pdb_path: str, chain_id: str) -> str | None:
    """Extract the amino acid sequence for a specific chain from a PDB file."""
    try:
        from Bio.PDB import PDBParser
        from Bio.PDB.Polypeptide import protein_letters_3to1

        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("target", pdb_path)

        for model in structure:
            for chain in model:
                if chain.id == chain_id:
                    residues = []
                    for residue in chain:
                        resname = residue.get_resname().strip()
                        if resname in protein_letters_3to1:
                            residues.append(protein_letters_3to1[resname])
                    if residues:
                        return "".join(residues)

        logger.warning("Chain %s not found in %s", chain_id, pdb_path)
        return None
    except Exception as exc:
        logger.warning("Failed to extract target sequence: %s", exc)
        return None


def _af2_env_with_jax_cache() -> dict:
    """Return an env dict with JAX persistent cache + LocalColabFold VRAM
    flags enabled for the AF2 (colabfold_batch) subprocess.

    ColabFold drives JAX, and JAX's persistent compilation cache is controlled
    by env vars. The Modal function mounts a named Volume at /root/.cache/jax
    so compiled HLO survives container eviction. First cold run populates the
    cache (~10-15 min XLA compile for AF2 multimer_v3); subsequent runs reuse
    the compiled graph and skip the JIT step.

    The LocalColabFold env-var set is the standard runtime fix for TF/JAX
    co-tenancy on a single GPU: TF (pulled in for AF2's tf.data feature
    pipeline) defaults to claiming nearly all VRAM at import time; JAX
    then can't allocate during XLA JIT and silently hangs. These flags
    force both frameworks into growth-allocation mode (set 6 LocalColabFold
    env vars to unblock TF/JAX VRAM allocation).

    See infrastructure/modal/rfdiffusion_app.py::xla_cache_volume.
    """
    cache_dir = "/root/.cache/jax"
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["JAX_COMPILATION_CACHE_DIR"] = cache_dir
    env["JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS"] = "0"
    env["JAX_PERSISTENT_CACHE_MIN_ENTRY_SIZE_BYTES"] = "0"
    # LocalColabFold prescribed VRAM/allocator flags for TF/JAX co-tenancy
    # (Bug 8 root cause: TF preallocates ~all VRAM at import → JAX hangs).
    env.setdefault("TF_FORCE_GPU_ALLOW_GROWTH", "true")
    env.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    env.setdefault("XLA_PYTHON_CLIENT_ALLOCATOR", "platform")
    env.setdefault("XLA_PYTHON_CLIENT_MEM_FRACTION", "4.0")
    env.setdefault("TF_FORCE_UNIFIED_MEMORY", "1")
    env.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
    return env


def _resolve_binder_sequence(
    full_designed_seq: str, target_sequences: list, design_name: str,
) -> str:
    """Pull the binder sequence out of a ProteinMPNN FASTA entry.

    Vanilla ProteinMPNN with assign_fixed_chains emits ONLY the designed
    chain, which is the common path. Some forks emit the whole complex with
    chains joined by '/'.

    In the joined case the binder is identified by IDENTITY — every target
    chain's own sequence is struck off the segment list and what remains is
    the binder. The previous positional pick (``chain_seqs[1] if
    target_chain == "A" else chain_seqs[0]``) hardcoded two chains and
    depended on an unverified ordering convention; picking the wrong
    segment feeds AF2 a target chain as though it were the binder and
    returns a fully plausible ipTM for the wrong molecule.

    Raises rather than guessing if the segments cannot be resolved.
    """
    if "/" not in full_designed_seq:
        return full_designed_seq

    segments = full_designed_seq.split("/")
    remaining_targets = list(target_sequences)
    leftovers: list = []
    for seg in segments:
        if seg in remaining_targets:
            remaining_targets.remove(seg)
        else:
            leftovers.append(seg)

    if len(leftovers) != 1:
        raise RuntimeError(
            f"Cannot identify the binder sequence for {design_name}: the "
            f"ProteinMPNN entry has {len(segments)} '/'-joined chain(s) with "
            f"lengths {[len(s) for s in segments]}; after removing the "
            f"{len(target_sequences)} known target chain sequence(s) "
            f"(lengths {[len(s) for s in target_sequences]}), "
            f"{len(leftovers)} segment(s) remain, expected exactly 1. "
            f"Refusing to guess positionally — the wrong pick scores a "
            f"target chain as the binder and looks entirely normal."
        )
    return leftovers[0]


def _build_af2_cmd(combined_fasta: str, out_dir: str, tier: str) -> list[str]:
    """Argv for the colabfold_batch AF2 validation run.

    Deliberately passes no ``--msa-mode``. ColabFold's default
    (``mmseqs2_uniref_env``) is what makes these scores mean anything.

    This used to force single-sequence MSA mode. That is the right setting
    for a de novo binder, which has no homologs to find -- and the wrong
    one for the natural target sharing the complex with it. Run
    single-sequence, and AF2 folds the target from its sequence alone and
    hands back a random coil. Across 16 stored production jobs the
    target's pLDDT never left 26.6-28.6 whatever the input, which pinned
    ipTM at 0.06-0.13 and i_pAE at 21.5-27.2 across all 115 candidates.
    Those were constants, not measurements.

    What this fixes is that the numbers can vary at all. Whether they then
    clear ``IPTM_THRESHOLD`` is expectation, not measurement -- unverified
    until an A100 run says so. Nor is 0.70 itself calibrated for this
    protocol: it is the conventional bar for a cofold, and this pipeline
    scores with vanilla AF2-multimer, no template and no initial guess. The
    sibling BoltzGen pipeline drops that leg entirely for the same reason,
    but that reasoning lives on the unmerged ``fix/boltzgen-unreachable-gate``
    branch, not on master -- do not go looking for it here.

    The default already does the right thing per chain, with no flag. The
    load-bearing half is that the target gets the MSA it needs -- standard
    AF2 behaviour, and the whole reason the scores can vary at all. The
    binder half is expectation, not measurement: a de novo design should
    find no natural homologs and so degrade to an empty MSA on its own. If
    it instead picks up spurious hits, that is no worse than the
    single-sequence it was already getting, so the trade holds either way.
    Worth confirming from the a3m depths on the first GPU run.

    Either way the old flag forced globally what the default does
    selectively, and in doing so broke the only chain that needed an MSA.

    Note this makes the MSA server a hard dependency, but not a fail-fast
    one. ColabFold's ``run_mmseqs2`` retries RATELIMIT and polls
    PENDING/RUNNING in uncapped ``while True`` loops; only ERROR and
    MAINTENANCE raise. So an unreachable server raises and fails the
    design, while a rate-limited or backed-up one instead spins until the
    3600s ``run_command`` timeout kills it. Either way the failure is
    visible, which beats billing for a score that cannot vary -- but budget
    for the slow shape, not just the fast one.

    The argv below is pinned EXACTLY by test_rfdiffusion_af2_cmd.py, both
    tiers. That is deliberate: a test that only forbids known-bad flags is
    blind to added ones, and additions here are not cheap. ``--templates``
    reaches ColabFold's ``mk_template()``, which shells out to ``hhsearch``,
    which Dockerfile.modal does not install; ``--host-url`` would redirect
    the MSA lookup to an arbitrary third-party server. Adding a flag means
    updating the allowlist on purpose.
    """
    cmd = [
        "colabfold_batch",
        combined_fasta,
        out_dir,
        "--model-type", "alphafold2_multimer_v3",
        "--num-recycle", "3",
        "--num-models", "1",
        "--rank", "iptm",
    ]
    # Smoke/mini_pilot: cut recycles hard + allow early-exit once a
    # reasonable ipTM is reached. Legacy production tier keeps the
    # default 3 recycles, 5 models (see pilot_preset()).
    if tier in ("smoke", "mini_pilot"):
        # Replace --num-recycle 3 with --num-recycle 1 and append
        # early-stop flags. We know the list shape above.
        recycle_idx = cmd.index("--num-recycle")
        cmd[recycle_idx + 1] = "1"
        cmd += [
            "--stop-at-score", "85",
            "--recycle-early-stop-tolerance", "0.5",
        ]
    return cmd


def stage_af2_validation(
    designed_fastas: list[str],
    target_pdb: str,
    target_chain,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
    tier: str = "",
) -> list[dict]:
    """Stage 3: AF2 multimer validation of designed binder-target complexes.

    ``target_chain`` accepts one chain ("A") or several ("A,B"). Every
    target chain goes on the target side of the AF2 complex FASTA, in the
    caller-supplied order, with the binder last:
    ``targetA:targetB:binder``.
    """
    logger.info("=== Stage 3: AF2 multimer validation ===")
    os.makedirs(output_dir, exist_ok=True)
    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running AF2 validation", 0, len(designed_fastas))

    # Log JAX persistent cache state for visibility. Cold cache implies
    # the first AF2 run will pay the 10-15 min XLA compile cost.
    cache_dir = Path("/root/.cache/jax")
    cache_dir.mkdir(parents=True, exist_ok=True)
    n_cached = sum(1 for p in cache_dir.rglob("*") if p.is_file())
    logger.info("JAX persistent cache: %d files at %s", n_cached, cache_dir)
    if n_cached == 0:
        logger.warning(
            "JAX cache cold — first AF2 run may take 10-15 min for XLA compile"
        )

    target_chains = parse_target_chains(target_chain)
    target_sequences: list = []
    for chain in target_chains:
        seq = _extract_target_sequence(target_pdb, chain)
        if not seq:
            raise RuntimeError(
                f"Could not extract target sequence from {target_pdb} "
                f"chain {chain} (requested target chains: {target_chains})"
            )
        target_sequences.append(seq)
    target_side = ":".join(target_sequences)
    # Residue count, NOT len(target_side) — the latter counts the ":" chain
    # separators too and is what the i_pAE boundary is measured against.
    target_residues = sum(len(s) for s in target_sequences)
    logger.info(
        "AF2 target side: chains=%s lengths=%s total_residues=%d",
        target_chains, [len(s) for s in target_sequences], target_residues,
    )

    results = []
    for idx, fasta_path in enumerate(designed_fastas):
        design_name = Path(fasta_path).stem
        sequences = _extract_sequences_from_fasta(fasta_path)
        seq_list = list(sequences.values())

        logger.info(
            "FASTA %s: %d entries, lengths=%s",
            fasta_path, len(seq_list),
            [len(s) for s in seq_list],
        )
        if len(seq_list) < 2:
            logger.warning("No designed sequences in %s, skipping", fasta_path)
            continue

        # Index 0 is the input poly-Gly backbone — skip it.
        # Index 1 is the first designed sequence from ProteinMPNN.
        full_designed_seq = seq_list[1]

        # Extract binder sequence (by identity, not position — see
        # _resolve_binder_sequence).
        binder_sequence = _resolve_binder_sequence(
            full_designed_seq, target_sequences, design_name,
        )

        combined_fasta = os.path.join(output_dir, f"{design_name}.fasta")
        with open(combined_fasta, "w") as fh:
            fh.write(f">{design_name}\n")
            fh.write(f"{target_side}:{binder_sequence}\n")

        logger.info(
            "AF2 input for %s: target_chains=%s target_len=%d, binder_len=%d, "
            "fasta=%s",
            design_name, target_chains, len(target_side), len(binder_sequence),
            combined_fasta,
        )

        per_design_out = os.path.join(output_dir, design_name)
        os.makedirs(per_design_out, exist_ok=True)

        try:
            # ponytail: TWO MSA server jobs per design, not one --
            # ColabFold's pair_mode defaults to unpaired_paired, so a
            # complex with 2 unique sequences submits an unpaired
            # ``ticket/msa`` AND a paired ``ticket/pair``. One run therefore
            # re-queries the SAME target 2N times against the public
            # api.colabfold.com. Tolerable at the design counts actually in
            # use (the pilot preset clamps to 2, the tools-hub form defaults
            # to 4, this module's own default is 10) and rude at the
            # num_designs=1000 the form still accepts. Cache the target a3m
            # and pass it back in if the design count ever grows.
            cmd = _build_af2_cmd(combined_fasta, per_design_out, tier)
            # colabfold_batch for a 280+ residue multimer on A10G runs 15-25 min
            # and emits nothing to stdout until done. Without a background
            # heartbeat the stale-detection cron (STALE_HEARTBEAT_SECONDS,
            # 30 min) kills the job. Update the stage string each iteration
            # so the UI shows live per-design progress.
            with _HeartbeatThread(
                webhook_url, job_id,
                stage=f"Running AF2 validation - {idx + 1}/{len(designed_fastas)} designs",
                designs_completed=idx,
                designs_total=len(designed_fastas),
                interval_seconds=60,
            ):
                # colabfold_batch is slow on first call (weights load + JIT),
                # and on A10G A100 multimer can take >30 min for a 272-residue
                # complex. 60 min timeout gives enough headroom for the first
                # design; subsequent designs reuse cached weights and are fast.
                # Pass JAX persistent-cache env so compiled HLO is written to
                # the Modal Volume-backed /root/.cache/jax directory.
                af2_output_text = run_command(
                    cmd, timeout=3600, env=_af2_env_with_jax_cache(),
                )
            logger.info("ColabFold output for %s:\n%s", design_name, af2_output_text[-2000:])

            # List what ColabFold actually produced
            af2_files = os.listdir(per_design_out) if os.path.isdir(per_design_out) else []
            logger.info("AF2 output files for %s: %s", design_name, af2_files)

            scores = parse_af2_scores(
                per_design_out, design_name,
                n_target_chains=len(target_chains),
                target_residues=target_residues,
            )
            if scores:
                results.append({
                    "design_name": design_name,
                    "scores": scores,
                    "sequence": binder_sequence,
                    "fasta_path": fasta_path,
                    # The dir the scores above were parsed from. Carried so the upload step can ship
                    # the structure those scores actually describe; without it, the only path on hand
                    # at upload time is the bare RFdiffusion backbone, which they do NOT describe.
                    "af2_dir": per_design_out,
                })
                logger.info(
                    "AF2 scores for %s: ipTM=%.3f pLDDT=%.1f i_pAE=%.1f",
                    design_name, scores["ipTM"], scores["pLDDT"], scores["i_pAE"],
                )
            # Build a per-design candidate for live UI streaming. tools-hub
            # gates this server-side via JOB_TOKEN and projects it to a fixed
            # schema; a malformed candidate is dropped silently. We swallow
            # any construction error here so the pipeline cannot crash on a
            # surprise score shape.
            candidate = None
            if scores:
                try:
                    iptm_v = scores.get("ipTM")
                    plddt_v = scores.get("pLDDT")
                    ipae_v = scores.get("i_pAE")
                    if (
                        iptm_v is not None
                        and plddt_v is not None
                        and ipae_v is not None
                        and iptm_v >= IPTM_THRESHOLD
                        and plddt_v >= PLDDT_THRESHOLD
                        and ipae_v <= IPAE_THRESHOLD
                    ):
                        filter_status = "pass"
                    else:
                        filter_status = "below threshold"
                    candidate = {
                        "rank": idx + 1,
                        "pdb_key": None,
                        "iptm": round(float(iptm_v), 4) if iptm_v is not None else None,
                        "plddt": round(float(plddt_v), 4) if plddt_v is not None else None,
                        "i_pae": round(float(ipae_v), 4) if ipae_v is not None else None,
                        "filter_status": filter_status,
                    }
                except Exception as exc:
                    logger.debug("Failed to build new_candidate: %s", exc)
                    candidate = None
            if webhook_url and job_id:
                send_heartbeat(
                    webhook_url, job_id, "Running AF2 validation",
                    idx + 1, len(designed_fastas),
                    new_candidate=candidate,
                )
        except RuntimeError as exc:
            logger.warning("AF2 validation failed for %s: %s", design_name, exc)
            continue

    logger.info("AF2 validated %d / %d designs", len(results), len(designed_fastas))
    return results


def write_metrics_csv(csv_path: str, candidates: list[dict]) -> None:
    """Write a metrics CSV summarizing all passing candidates."""
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "design_name", "ipTM", "pLDDT", "i_pAE", "sequence"])
        for c in candidates:
            design_name = Path(c["pdb_key"]).stem
            scores = c["scores"]
            writer.writerow([
                c["rank"], design_name,
                scores.get("ipTM", ""), scores.get("pLDDT", ""),
                scores.get("i_pAE", ""), c.get("sequence", ""),
            ])


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    """Run the full pipeline: download -> RFdiffusion -> MPNN -> AF2 -> upload -> webhook."""
    startup_check()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
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
        # AF2 weights only needed for mini_pilot; for smoke we skip AF2.
        if tier == "mini_pilot":
            try:
                download_af2_weights()
            except Exception as exc:
                _write_smoke_failure("preflight", "af2_weight_download", str(exc))
                sys.exit(1)

        work_dir = tempfile.mkdtemp(prefix="rfdiffusion_smoke_")
        try:
            result = run_smoke_tier(
                tier, work_dir,
                job_spec_override=job_payload.get("job_spec") or {},
                input_url=(job_payload.get("input_pdb_url") or ""),
            )
            with open(SMOKE_RESULTS_PATH, "w") as fh:
                json.dump(result, fh)
            logger.info(
                "Smoke tier %s: status=%s gpu_seconds=%s",
                tier, result.get("status"), result.get("gpu_seconds"),
            )
            if result.get("status") != "COMPLETED":
                sys.exit(1)
            return
        finally:
            # In the finally, immediately before the rmtree: this covers the
            # clean return, the sys.exit(1) above (SystemExit still unwinds
            # through here) and any exception out of run_smoke_tier.
            ship_raw_archive(work_dir)
            shutil.rmtree(work_dir, ignore_errors=True)

    # ---- Legacy webhook path ----
    download_af2_weights()

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))

    job_spec = job_payload["job_spec"]
    input_url = job_payload["input_presigned_url"]
    upload_endpoint = job_payload.get("upload_urls_endpoint", "")
    job_token = os.environ.get("JOB_TOKEN", "")

    target_chain = job_spec.get("target_chain", "A")
    pipeline_start = time.time()

    work_dir = tempfile.mkdtemp(prefix="rfdiffusion_job_")
    target_pdb = os.path.join(work_dir, "target.pdb")

    try:
        # ----- Download input PDB -----
        raw_target_pdb = os.path.join(work_dir, "target_raw.pdb")
        download_input(input_url, raw_target_pdb)

        # ----- Sanitize input PDB (Bug 9 fix) -----
        # Strip waters/HETATM/altlocs/multi-model + drop residues with bad
        # backbones, so RFdiffusion's frame builder doesn't crash on
        # zero-coord placeholder atoms or non-protein chains. Original
        # numbering preserved (RFdiffusion hotspot strings reference it).
        try:
            from pipeline_normalize import normalize_for_rfdiffusion
            norm_report = normalize_for_rfdiffusion(
                raw_target_pdb, target_pdb,
                target_chain=parse_target_chains(target_chain),
            )
            logger.info(
                "Normalize: chains_requested=%s chains_kept=%s "
                "chains_dropped=%s residues_kept=%s "
                "residues_dropped=%s changes=%s",
                norm_report.chains_requested,
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

        # ----- Stage 1: RFdiffusion -----
        rfdiff_output = os.path.join(work_dir, "rfdiffusion_output")
        os.makedirs(rfdiff_output, exist_ok=True)

        try:
            backbone_pdbs = stage_rfdiffusion(
                target_pdb, job_spec, rfdiff_output,
                webhook_url=webhook_url, job_id=job_id,
            )
        except RuntimeError as exc:
            logger.error("RFdiffusion failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {"error": f"RFdiffusion failed: {exc}"})
            return

        # ----- Stage 2: ProteinMPNN -----
        mpnn_output = os.path.join(work_dir, "mpnn_output")
        try:
            designed_fastas = stage_proteinmpnn(
                backbone_pdbs, target_chain, mpnn_output,
                webhook_url=webhook_url, job_id=job_id,
                binder_length=job_spec.get("parameters", {}).get("binder_length"),
            )
        except RuntimeError as exc:
            logger.error("ProteinMPNN failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"ProteinMPNN failed: {exc}",
                "partial": True,
                "backbone_count": len(backbone_pdbs),
            })
            return

        # ----- Stage 3: AF2 validation -----
        af2_output = os.path.join(work_dir, "af2_output")
        try:
            af2_results = stage_af2_validation(
                designed_fastas, target_pdb, target_chain, af2_output,
                webhook_url=webhook_url, job_id=job_id,
            )
        except RuntimeError as exc:
            logger.error("AF2 validation failed: %s", exc)
            post_webhook(webhook_url, job_id, pod_id, {
                "error": f"AF2 validation failed: {exc}",
                "partial": True,
                "backbone_count": len(backbone_pdbs),
                "designed_count": len(designed_fastas),
            })
            return

        # ----- Label and rank -----
        # A bad result is still a result. Every AF2 scored design is kept
        # and tagged with filter_status so the UI can show all of them.
        # The in silico thresholds (ipTM, pLDDT, i_pAE) now drive a label,
        # not a gate.
        pass_count = 0
        for r in af2_results:
            iptm_v = r["scores"].get("ipTM")
            plddt_v = r["scores"].get("pLDDT")
            ipae_v = r["scores"].get("i_pAE")
            is_pass = (
                iptm_v is not None
                and plddt_v is not None
                and ipae_v is not None
                and iptm_v >= IPTM_THRESHOLD
                and plddt_v >= PLDDT_THRESHOLD
                and ipae_v <= IPAE_THRESHOLD
            )
            r["scores"]["filter_status"] = "pass" if is_pass else "below threshold"
            if is_pass:
                pass_count += 1
        passing = list(af2_results)
        passing.sort(key=lambda x: x["scores"].get("ipTM", 0.0), reverse=True)

        logger.info(
            "Labeling: %d / %d pass (ipTM>=%.2f, pLDDT>=%.0f, i_pAE<=%.0f); "
            "all designs emitted with filter_status label",
            pass_count, len(af2_results),
            IPTM_THRESHOLD, PLDDT_THRESHOLD, IPAE_THRESHOLD,
        )

        # ----- Upload outputs (on-demand URLs) -----
        candidates = []
        filenames_to_upload = []
        for rank_idx, r in enumerate(passing):
            filenames_to_upload.append(f"design_{rank_idx + 1:03d}.pdb")
        if filenames_to_upload:
            filenames_to_upload.append("metrics.csv")

        # Request fresh presigned upload URLs from the backend
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

        for rank_idx, r in enumerate(passing):
            rank = rank_idx + 1
            design_name = r["design_name"]

            # Ship the structure the scores describe: the AF2-predicted COMPLEX, not the bare
            # RFdiffusion backbone. r["scores"] is parsed from rank_001 in r["af2_dir"], so the
            # rank_001 model there is the object ipTM/pLDDT/i_pAE were actually measured on.
            # Uploading the backbone hands the user a structure that contradicts its own scores.
            backbone_pdb = None
            af2_dir = r.get("af2_dir")
            if af2_dir and os.path.isdir(af2_dir):
                for pat in ("*_relaxed_rank_001_*.pdb", "*_unrelaxed_rank_001_*.pdb",
                            "*rank_001*.pdb"):
                    hits = sorted(glob(os.path.join(af2_dir, pat)))
                    if hits:
                        backbone_pdb = hits[0]
                        break
            if not backbone_pdb:
                # AF2 wrote no rank_001 for this design. Fall back to the backbone so the run still
                # produces something, but make the mismatch visible rather than silent.
                logger.warning(
                    "No AF2 complex for %s; falling back to the RFdiffusion backbone. The uploaded "
                    "structure will NOT correspond to its ipTM/pLDDT/i_pAE.", design_name,
                )
                backbone_pdb = os.path.join(rfdiff_output, f"{design_name}.pdb")
                if not os.path.exists(backbone_pdb):
                    backbone_pdb = os.path.join(
                        rfdiff_output, f"design_{design_name.split('_')[-1]}.pdb"
                    )

            upload_filename = f"design_{rank_idx + 1:03d}.pdb"
            # pdb_key MUST share basename with upload_filename so the
            # web service's resolver finds the Storage object at
            # {user}/{job}/designs/<basename>. design_name diverges
            # from upload_filename and would 404 the resolver. The
            # contracts module (/opt/contracts/rpc.py) defines the
            # upload-URL exchange shape consumed by the web service.
            pdb_key = f"designs/{upload_filename}"
            candidate = {
                "rank": rank,
                "pdb_key": pdb_key,
                "scores": r["scores"],
                "sequence": r["sequence"],
                "local_file": backbone_pdb,
            }
            candidates.append(candidate)
            if upload_filename in upload_urls and os.path.exists(backbone_pdb):
                try:
                    upload_output(upload_urls[upload_filename], backbone_pdb)
                except RuntimeError as exc:
                    logger.warning("Failed to upload PDB for rank %d: %s", rank, exc)
                    failed_uploads.append(upload_filename)

        # ----- Upload metrics CSV -----
        if candidates:
            csv_path = os.path.join(work_dir, "metrics.csv")
            write_metrics_csv(csv_path, candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], csv_path)
                except RuntimeError as exc:
                    logger.warning("Failed to upload metrics CSV: %s", exc)
                    failed_uploads.append("metrics.csv")

        elapsed_minutes = (time.time() - pipeline_start) / 60.0
        logger.info(
            "Pipeline complete: %d candidates in %.1f minutes",
            len(candidates), elapsed_minutes,
        )

        # ----- POST results to webhook -----
        webhook_candidates: list[dict] = [
            _webhook_candidate_entry(c) for c in candidates
        ]

        result_payload = {
            "candidates": webhook_candidates,
            "candidate_count": len(candidates),
            "total_designs": len(backbone_pdbs),
            "af2_validated": len(af2_results),
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
        logger.error("Pipeline failed: %s", exc)
        post_webhook(webhook_url, job_id, pod_id, {
            "error": f"Pipeline failed: {exc}",
        })

    finally:
        # In the finally, immediately before the rmtree: this covers the happy
        # path, the four early `return`s inside the try (sanitize / RFdiffusion
        # / ProteinMPNN / AF2 failures) and the except branch above. The
        # zero-candidate run requests no upload URLs and ships nothing today,
        # which is exactly the run whose tree is worth having.
        ship_raw_archive(work_dir)
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
