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

import requests

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


def run_command(cmd: list[str], timeout: int = 3600, cwd: str | None = None) -> str:
    """Run a subprocess command with timeout and logging."""
    logger.info("Running: %s", " ".join(cmd[:6]) + ("..." if len(cmd) > 6 else ""))
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    elapsed = time.time() - start
    logger.info("Command finished in %.1fs (exit code %d)", elapsed, result.returncode)
    if result.returncode != 0:
        error_tail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(f"Command failed (exit {result.returncode}): {error_tail}")
    return result.stdout + result.stderr


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
# AF2 score parsing
# ===========================================================================

def parse_af2_scores(result_dir: str, design_name: str) -> dict | None:
    """Extract ipTM, pLDDT, and i_pAE from ColabFold AF2 prediction output."""
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
        mean_plddt = sum(plddt_values) / len(plddt_values) if plddt_values else 0.0

        pae_matrix = data.get("pae", [])
        ipae = _compute_interface_pae(pae_matrix, data) if pae_matrix else 99.0

        return {
            "ipTM": round(iptm, 4),
            "pLDDT": round(mean_plddt, 2),
            "i_pAE": round(ipae, 2),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse AF2 scores for %s: %s", design_name, exc)
        return None


def _compute_interface_pae(pae_matrix: list, score_data: dict) -> float:
    """Compute mean interface PAE from the PAE matrix."""
    total_res = len(pae_matrix)
    if total_res == 0:
        return 99.0

    chain_lengths = score_data.get("chain_lengths", None)
    if chain_lengths and len(chain_lengths) >= 2:
        boundary = chain_lengths[0]
    else:
        boundary = total_res // 2

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
    try:
        from Bio.PDB import PDBParser
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("target", pdb_path)

        for model in structure:
            for chain in model:
                if chain.id == chain_id:
                    residue_nums = [
                        r.id[1] for r in chain
                        if r.id[0] == " "  # Standard residues only (skip HETATM)
                    ]
                    if residue_nums:
                        return min(residue_nums), max(residue_nums)

        raise RuntimeError(f"Chain {chain_id} not found in {pdb_path}")
    except ImportError:
        raise RuntimeError("Biopython is required to parse PDB residue ranges")


def build_hydra_args(job_spec: dict, target_pdb_path: str) -> list[str]:
    """Build RFdiffusion Hydra CLI override args from JobSpec parameters."""
    params = job_spec.get("parameters", {})
    chain = job_spec.get("target_chain", "A")
    hotspots = job_spec.get("hotspot_residues", [])

    binder_length = params.get("binder_length", {"min": 50, "max": 100})
    if isinstance(binder_length, dict):
        binder_min = binder_length.get("min", 50)
        binder_max = binder_length.get("max", 100)
    else:
        binder_min, binder_max = 50, 100

    num_designs = params.get("num_designs", 10)

    # Read actual residue range from PDB instead of hardcoding
    first_res, last_res = _get_chain_residue_range(target_pdb_path, chain)
    logger.info("Chain %s residue range: %d-%d", chain, first_res, last_res)
    contig_str = f"[{chain}{first_res}-{last_res}/0 {binder_min}-{binder_max}]"

    checkpoint = params.get("checkpoint", "Complex_base_ckpt.pt")
    ckpt_path = os.path.join(MODELS_DIR, checkpoint)

    hydra_args = [
        f"inference.input_pdb={target_pdb_path}",
        f"contigmap.contigs={contig_str}",
        f"inference.num_designs={num_designs}",
        f"inference.ckpt_override_path={ckpt_path}",
    ]

    if hotspots:
        hotspot_str = "[" + ",".join(f"{chain}{res}" for res in hotspots) + "]"
        hydra_args.append(f"ppi.hotspot_res={hotspot_str}")

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
    run_command(cmd, timeout=1800)

    generated = sorted(glob(os.path.join(output_dir, "design_*.pdb")))
    logger.info("RFdiffusion generated %d backbone PDBs", len(generated))
    if not generated:
        raise RuntimeError("RFdiffusion produced no output PDB files")
    return generated


def stage_proteinmpnn(
    backbone_pdbs: list[str],
    target_chain: str,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
) -> list[str]:
    """Stage 2: Run ProteinMPNN sequence design on each backbone."""
    logger.info("=== Stage 2: ProteinMPNN sequence design ===")
    os.makedirs(output_dir, exist_ok=True)
    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running ProteinMPNN", 0, len(backbone_pdbs))

    binder_chain = "B" if target_chain == "A" else "A"

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


def stage_af2_validation(
    designed_fastas: list[str],
    target_pdb: str,
    target_chain: str,
    output_dir: str,
    webhook_url: str = "",
    job_id: str = "",
) -> list[dict]:
    """Stage 3: AF2 multimer validation of designed binder-target complexes."""
    logger.info("=== Stage 3: AF2 multimer validation ===")
    os.makedirs(output_dir, exist_ok=True)
    if webhook_url and job_id:
        send_heartbeat(webhook_url, job_id, "Running AF2 validation", 0, len(designed_fastas))

    target_sequence = _extract_target_sequence(target_pdb, target_chain)
    if not target_sequence:
        raise RuntimeError(
            f"Could not extract target sequence from {target_pdb} chain {target_chain}"
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

        # Vanilla ProteinMPNN does NOT insert '/' between chains.
        # We must split the continuous string using the exact chain lengths
        # from the parsed PDB JSONL file produced in Stage 2.
        jsonl_path = os.path.join(
            os.path.dirname(os.path.dirname(fasta_path)), "parsed_pdbs.jsonl"
        )

        target_len = 0
        binder_len = 0
        binder_chain = "B" if target_chain == "A" else "A"

        try:
            with open(jsonl_path) as f:
                for line in f:
                    data = json.loads(line)
                    if data.get("name") == design_name:
                        # parse_multiple_chains.py uses "seq_chain_X" keys,
                        # but some versions use bare chain ID. Try both.
                        target_len = len(
                            data.get(f"seq_chain_{target_chain}",
                                     data.get(target_chain, ""))
                        )
                        binder_len = len(
                            data.get(f"seq_chain_{binder_chain}",
                                     data.get(binder_chain, ""))
                        )
                        break
        except Exception as exc:
            logger.warning("Could not read JSONL for chain lengths: %s", exc)
            continue

        if target_len == 0 or binder_len == 0:
            logger.warning("Could not determine chain lengths for %s", design_name)
            continue

        logger.info(
            "Chain lengths for %s: target(%s)=%d, binder(%s)=%d, designed_seq=%d",
            design_name, target_chain, target_len, binder_chain, binder_len,
            len(full_designed_seq),
        )

        # Extract just the binder sequence.
        # RFdiffusion outputs chains alphabetically (A then B).
        if target_chain == "A":
            binder_sequence = full_designed_seq[target_len:target_len + binder_len]
        else:
            binder_sequence = full_designed_seq[:binder_len]

        combined_fasta = os.path.join(output_dir, f"{design_name}.fasta")
        with open(combined_fasta, "w") as fh:
            fh.write(f">{design_name}\n")
            fh.write(f"{target_sequence}:{binder_sequence}\n")

        logger.info(
            "AF2 input for %s: target_len=%d, binder_len=%d, fasta=%s",
            design_name, len(target_sequence), len(binder_sequence), combined_fasta,
        )

        per_design_out = os.path.join(output_dir, design_name)
        os.makedirs(per_design_out, exist_ok=True)

        try:
            cmd = [
                "colabfold_batch",
                combined_fasta,
                per_design_out,
                "--model-type", "alphafold2_multimer_v3",
                "--msa-mode", "single_sequence",
                "--num-recycle", "3",
                "--num-models", "1",
                "--rank", "iptm",
            ]
            af2_output_text = run_command(cmd, timeout=600)
            logger.info("ColabFold output for %s:\n%s", design_name, af2_output_text[-2000:])

            # List what ColabFold actually produced
            af2_files = os.listdir(per_design_out) if os.path.isdir(per_design_out) else []
            logger.info("AF2 output files for %s: %s", design_name, af2_files)

            scores = parse_af2_scores(per_design_out, design_name)
            if scores:
                results.append({
                    "design_name": design_name,
                    "scores": scores,
                    "sequence": binder_sequence,
                    "fasta_path": fasta_path,
                })
                logger.info(
                    "AF2 scores for %s: ipTM=%.3f pLDDT=%.1f i_pAE=%.1f",
                    design_name, scores["ipTM"], scores["pLDDT"], scores["i_pAE"],
                )
            if webhook_url and job_id:
                send_heartbeat(webhook_url, job_id, "Running AF2 validation", idx + 1, len(designed_fastas))
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
    download_af2_weights()

    # Read configuration from environment
    job_payload_str = os.environ.get("JOB_PAYLOAD")
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    job_id = os.environ.get("JOB_ID", "unknown")
    pod_id = os.environ.get("RUNPOD_POD_ID", os.environ.get("POD_ID", "unknown"))

    if not job_payload_str:
        logger.error("JOB_PAYLOAD environment variable not set")
        sys.exit(1)

    job_payload = json.loads(job_payload_str)
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
        download_input(input_url, target_pdb)

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

        # ----- Filter and rank -----
        passing = [
            r for r in af2_results
            if (
                r["scores"]["ipTM"] >= IPTM_THRESHOLD
                and r["scores"]["pLDDT"] >= PLDDT_THRESHOLD
                and r["scores"]["i_pAE"] <= IPAE_THRESHOLD
            )
        ]
        passing.sort(key=lambda x: x["scores"]["ipTM"], reverse=True)

        logger.info(
            "Filtering: %d / %d pass (ipTM>=%.2f, pLDDT>=%.0f, i_pAE<=%.0f)",
            len(passing), len(af2_results),
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
        if upload_endpoint and job_token and filenames_to_upload:
            try:
                upload_urls = request_upload_urls(upload_endpoint, job_token, filenames_to_upload)
            except RuntimeError as exc:
                logger.error("Failed to get upload URLs: %s", exc)

        for rank_idx, r in enumerate(passing):
            rank = rank_idx + 1
            design_name = r["design_name"]

            backbone_pdb = os.path.join(rfdiff_output, f"{design_name}.pdb")
            if not os.path.exists(backbone_pdb):
                backbone_pdb = os.path.join(
                    rfdiff_output, f"design_{design_name.split('_')[-1]}.pdb"
                )

            pdb_key = f"designs/{design_name}.pdb"
            candidate = {
                "rank": rank,
                "pdb_key": pdb_key,
                "scores": r["scores"],
                "sequence": r["sequence"],
            }
            candidates.append(candidate)

            upload_filename = f"design_{rank_idx + 1:03d}.pdb"
            if upload_filename in upload_urls and os.path.exists(backbone_pdb):
                try:
                    upload_output(upload_urls[upload_filename], backbone_pdb)
                except RuntimeError as exc:
                    logger.warning("Failed to upload PDB for rank %d: %s", rank, exc)

        # ----- Upload metrics CSV -----
        if candidates:
            csv_path = os.path.join(work_dir, "metrics.csv")
            write_metrics_csv(csv_path, candidates)
            if "metrics.csv" in upload_urls:
                try:
                    upload_output(upload_urls["metrics.csv"], csv_path)
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
                {"rank": c["rank"], "pdb_key": c["pdb_key"], "scores": c["scores"]}
                for c in candidates
            ],
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
        post_webhook(webhook_url, job_id, pod_id, result_payload)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
