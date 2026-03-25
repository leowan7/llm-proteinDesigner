# Phase 4: Pipeline Validation - Research

**Researched:** 2026-03-25
**Domain:** GPU pipeline validation (RunPod serverless, Docker images, 5 protein design tools)
**Confidence:** MEDIUM-HIGH

## Summary

Phase 4 validates that every design tool on the Kendrew platform works end-to-end on real GPU hardware. This means building 5 Docker images (one per tool), deploying them as RunPod serverless endpoints, and running pilot jobs that exercise the full loop: PDB input from R2 presigned URL, GPU execution, output upload back to R2, webhook completion, and result display.

The existing backend infrastructure is solid. The RunPod client (`backend/gpu/runpod.py`), webhook handler (`backend/webhooks/router.py`), arq worker (`backend/worker/tasks.py`), and presigned URL generation (`backend/storage/client.py`) are all built and tested. What is missing: (1) the actual RunPod handler scripts inside each Docker image, (2) the Docker images themselves with baked-in model weights, (3) a `pxdesign` endpoint ID in the config, (4) tool-specific input payload builders that translate a JobSpec into the exact CLI commands each tool expects, and (5) result parsers that read each tool's output format and produce the standardized `CandidateResult` list.

The primary risk is Docker image size and cold start time. BindCraft and PXDesign images will be 20-25 GB with baked weights. RunPod FlashBoot mitigates cold starts for warm endpoints, but first-ever cold starts will take 2-5 minutes. Model weight download at runtime is not recommended -- it adds latency every cold start and RunPod does not bill for image pull time but does bill for runtime downloads.

**Primary recommendation:** Build and validate one tool at a time, starting with RFdiffusion (simplest pipeline, fastest runtime, official Docker image exists). Use the same RunPod handler pattern across all 5 images. Bake all model weights into Docker images. Test with small pilot runs (10-100 designs) against a well-characterized target (IL-7Ra, PDB 7S4E).

## Project Constraints (from CLAUDE.md)

- Python primary language; PEP 8; Google-style docstrings
- Descriptive variable names; inline comments for non-obvious logic
- Explicit error handling; fail fast with informative messages
- No hardcoded file paths; use config or CLI arguments
- Pinned dependencies in requirements.txt
- Product is called Kendrew (not Ranomics)
- Each algorithm needs different config/YAML and PDB prep per target chain
- Haiku cannot run the agent; minimum is Sonnet
- RunPod serverless with parallel batching (locked decision from GPU-ARCHITECTURE.md)

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PIPE-01 | RFdiffusion 100-design pilot completes on test target | RFdiffusion Docker image, handler script, ProteinMPNN + AF2 post-processing |
| PIPE-02 | BindCraft 10-design pilot completes with all 4 stages | FreeBindCraft Docker image with --no-pyrosetta, AF2 weights baked in |
| PIPE-03 | RFantibody 100-design VHH pilot completes | RFantibody Docker image, AbMPNN weights, RF2 antibody weights |
| PIPE-04 | BoltzGen 100-design protein-anything pilot completes | BoltzGen Docker image, YAML config, BoltzIF + Boltz-2 refolding |
| PIPE-05 | PXDesign 100-design basic-mode pilot completes | PXDesign Docker image, Protenix + AF2-IG, ProteinMPNN, MSA prep |
| PIPE-06 | Output files correctly uploaded to R2, parsed, and displayed | Result parser per tool, presigned URL flow, webhook completion |
| PIPE-07 | Docker images built, tested, and version-pinned | Dockerfiles, RunPod handler scripts, CI build process |
</phase_requirements>

## Standard Stack

### RunPod Serverless API

| Property | Value | Source |
|----------|-------|--------|
| Submit endpoint | `POST /v2/{endpoint_id}/run` | RunPod docs |
| Status endpoint | `GET /v2/{endpoint_id}/status/{job_id}` | RunPod docs |
| Cancel endpoint | `POST /v2/{endpoint_id}/cancel/{job_id}` | RunPod docs |
| Max execution timeout | 7 days (configurable per-request via `policy.executionTimeout`) | RunPod docs |
| Default execution timeout | 600 seconds (10 minutes) -- **must override** | RunPod docs |
| Job TTL | Default 24 hours; max 7 days | RunPod docs |
| Webhook retry | 2 retries with 10-second delay; must return HTTP 200 | RunPod docs |
| Results availability | 30 minutes after async job completion | RunPod docs |
| Rate limits | 1000 POST /run per 10 sec; 2000 GET /status per 10 sec | RunPod docs |
| Python SDK requirement | Python 3.10+ for `runpod` module | RunPod docs |
| Docker platform | `--platform linux/amd64` required for builds | RunPod docs |

### RunPod Handler Format

Every Docker image must contain an `rp_handler.py`:

```python
import runpod

def handler(job):
    """Process a single design job on GPU."""
    job_input = job["input"]
    # ... run tool ...
    return {"candidates": [...], "candidate_count": N, "next_steps": "..."}

runpod.serverless.start({"handler": handler})
```

**Key rules:**
- Handler receives `job["input"]` (the `input_payload` from `GPUJobSubmission`)
- Return a dict -- it becomes the `output` field in the status response
- Return `{"error": "message"}` on failure -- RunPod marks the job FAILED
- Exceptions are auto-captured and mark the job FAILED
- Generator handlers (`yield`) available for streaming but not needed here

### Docker Image Strategy: Bake Everything

| Strategy | Cold Start | Image Size | Billing | Verdict |
|----------|-----------|------------|---------|---------|
| Bake weights into image | Fast (seconds with FlashBoot) | 15-25 GB per image | Not billed for image pull | **Use this** |
| Download at runtime | Slow (minutes per cold start) | Small | Billed during download | Avoid |
| Network Volume | Medium (network read) | Small | Volume storage cost | Overkill for 5 fixed models |
| HuggingFace model caching | Fast (if cached host available) | Small | Not billed for download | Only for HF-hosted models |

RunPod FlashBoot retains worker state after spin-down, so subsequent cold starts are faster. First-ever cold start for a 25 GB image will take longer but is a one-time cost.

### Docker Images Per Tool

#### 1. kendrew/rfdiffusion (~15 GB)

| Property | Value |
|----------|-------|
| Base image | `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04` |
| Python | 3.10 |
| Key deps | PyTorch (CUDA 11.8), SE3-Transformers, Hydra, ProteinMPNN |
| Model weights | Baked: `Complex_base_ckpt.pt` (binder design), `Base_ckpt.pt`, `ActiveSite_ckpt.pt` |
| Weight URLs | `http://files.ipd.uw.edu/pub/RFdiffusion/{hash}/{name}.pt` (see full list below) |
| Official image | `rosettacommons/rfdiffusion` on Docker Hub (can extend) |
| ProteinMPNN | Clone `dauparas/ProteinMPNN`, include `v_48_020.pt` weights |
| ColabFold AF2 | For post-design validation -- include AF2 multimer weights (~3.5 GB) |

**RFdiffusion weight download URLs:**
```bash
wget http://files.ipd.uw.edu/pub/RFdiffusion/6f5902ac237024bdd0c176cb93063dc4/Base_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/e29311f6f1bf1af907f9ef9f44b8328b/Complex_base_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/60f09a193fb5e5ccdc4980417708dbab/Complex_Fold_base_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/74f51cfb8b440f50d70878e05361d8f0/InpaintSeq_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/76d00716416567174cdb7ca96e208296/InpaintSeq_Fold_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/5532d2e1f3a4738decd58b19d633b3c3/ActiveSite_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/12fc204edeae5b57713c5ad7dcb97d39/Base_epoch8_ckpt.pt
wget http://files.ipd.uw.edu/pub/RFdiffusion/f572d396fae9206628714fb2ce00f72e/Complex_beta_ckpt.pt
```

#### 2. kendrew/bindcraft (~25 GB)

| Property | Value |
|----------|-------|
| Base image | `nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04` |
| Python | 3.10 |
| Key deps | JAX (CUDA 12.1), ColabDesign, OpenMM, FreeSASA, sc-rs, Biopython, FASPR |
| Install | `git clone cytokineking/FreeBindCraft && bash install_bindcraft.sh --cuda '12.1' --no-pyrosetta` |
| AF2 weights | Auto-downloaded on first run (~3.5 GB); bake into image during build |
| Critical flag | `--no-pyrosetta` (MIT license, no PyRosetta dependency) |
| Docker build | `docker build -t freebindcraft:gpu .` (official Dockerfile exists) |
| Runtime cmd | `python bindcraft.py --settings target.json --filters default_filters.json --advanced default_4stage_multimer.json --no-pyrosetta --no-plots --no-animations` |

**BindCraft Docker run pattern:**
```bash
docker run --gpus all --rm \
  --ulimit nofile=65536:65536 \
  -v /outputs:/root/software/outputs \
  freebindcraft:gpu \
  python bindcraft.py \
    --settings /tmp/target.json \
    --filters settings_filters/default_filters.json \
    --advanced settings_advanced/default_4stage_multimer.json \
    --no-pyrosetta --no-plots --no-animations
```

#### 3. kendrew/rfantibody (~15 GB)

| Property | Value |
|----------|-------|
| Base image | `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04` |
| Python | 3.10 |
| Key deps | PyTorch (CUDA 11.8), uv (package manager), SE3-Transformers |
| Model weights | `bash include/download_weights.sh` -- downloads RFdiffusion antibody weights, AbMPNN weights, RF2 antibody weights |
| Official Dockerfile | Exists at `RFantibody/Dockerfile` (uses uv for package management) |
| RF2 weight fix | If RF2 fails, modify `src/rfantibody/rf2/config/base.yml` to set `model_weights: /home/weights/RF2_ab.pt` |

#### 4. kendrew/boltzgen (~20 GB)

| Property | Value |
|----------|-------|
| Base image | Python 3.12 base + CUDA 12.1+ |
| Python | 3.12 |
| Key deps | `pip install boltzgen` (PyPI package available) |
| Model weights | ~6 GB, auto-downloaded to `~/.cache` on first run; bake with `--build-arg DOWNLOAD_WEIGHTS=true` |
| CIF requirement | All residue indices must start at 1 per chain using canonical `label_asym_id` |
| Official Dockerfile | Exists in repo |
| Runtime cmd | `boltzgen run spec.yaml --output /workdir --protocol protein-anything --num_designs N --budget M` |
| Cache dir | `$HF_HOME` or `~/.cache` -- set in Dockerfile |

**BoltzGen Docker run pattern:**
```bash
docker run --rm --gpus all \
  -v "$(realpath workdir)":/workdir \
  -v "$(realpath cache)":/cache \
  boltzgen \
  boltzgen run /workdir/spec.yaml --output /workdir/output \
  --protocol protein-anything --num_designs 100 --budget 20
```

#### 5. kendrew/pxdesign (~25 GB)

| Property | Value |
|----------|-------|
| Base image | CUDA 12.1+ (from PXDesign Dockerfile) |
| Python | 3.10+ |
| Key deps | PyTorch, Protenix, PXDesignBench, ProteinMPNN, custom CUDA kernels |
| Install | `bash install.sh --env pxdesign --pkg_manager conda --cuda-version 12.1` or Docker |
| Model weights | `bash download_tool_weights.sh` -- fetches AF2, MPNN, CCD cache |
| Diffusion weights | Auto-download on first run; bake by running once during build |
| MSA requirement | Required for Extended mode (Protenix filtering); optional for Basic/Preview |
| CUTLASS | Required for DeepSpeed Evo attention: `${CUTLASS_PATH:-$HOME/cutlass}` v3.5.1 |
| Runtime cmd | `pxdesign pipeline --preset basic -i spec.yaml -o /workdir/output --N_sample 100 --dtype bf16` |
| Input validation | `pxdesign check-input --yaml spec.yaml` (run before design) |

## Architecture Patterns

### RunPod Handler Architecture (Shared Across All 5 Images)

Every handler follows the same pattern:

```
rp_handler.py
    1. Receive job["input"] containing:
       - job_spec (tool, target_pdb_path, target_chain, hotspots, parameters)
       - input_presigned_url (GET URL for target PDB from R2)
       - output_presigned_urls (PUT URLs for design PDB uploads)
       - report_presigned_url (PUT URL for metrics CSV)
    2. Download target PDB from R2 via presigned GET URL
    3. Generate tool-specific config (JSON/YAML/CLI args) from job_spec
    4. Execute the design tool
    5. Parse output files (PDBs + metrics)
    6. Upload each design PDB to R2 via presigned PUT URLs
    7. Upload metrics CSV to R2
    8. Return {candidates: [...], candidate_count: N, next_steps: "..."}
```

### Input/Output Flow

```
Kendrew Backend                          RunPod Worker
     |                                       |
     |-- Generate presigned GET URL -------->|
     |   (for target PDB in R2)              |
     |                                       |
     |-- Generate presigned PUT URLs ------->|
     |   (for output PDBs + metrics)         |
     |                                       |
     |-- POST /run with payload ------------>|
     |   {input: {job_spec, urls}}           |
     |                                       |
     |                                  [Download PDB]
     |                                  [Run tool]
     |                                  [Upload results]
     |                                       |
     |<-- Webhook POST (COMPLETED) ---------|
     |   {output: {candidates, count}}       |
```

**Critical change needed in `worker/tasks.py`:** Currently generates presigned PUT URLs only for individual PDB files. Needs to also generate:
- A presigned GET URL for the input target PDB (so the RunPod worker can download it)
- A presigned PUT URL for the metrics CSV
- Tool-specific input payload building (translate JobSpec.parameters into tool CLI args)

### Tool-Specific Config Generation

Each tool needs a different config format derived from the same `JobSpec`:

| Tool | Config Format | Key Translation |
|------|--------------|-----------------|
| RFdiffusion | Hydra CLI args | `contigmap.contigs`, `ppi.hotspot_res`, `inference.num_designs`, `inference.ckpt_override_path` |
| BindCraft | JSON settings file | `starting_pdb`, `chains`, `target_hotspot_residues`, `lengths`, `number_of_final_designs` |
| RFantibody | YAML + CLI | Epitope residues, CDR selection, framework type (VHH/scFv) |
| BoltzGen | YAML design spec | `entities`, `binding_types`, `protocol`, `--num_designs`, `--budget` |
| PXDesign | YAML task file | `target.file`, `target.chains.{id}.hotspots`, `binder_length`, `--preset`, `--N_sample` |

### Result Parsing Per Tool

Each tool outputs results differently. The handler must normalize to `CandidateResult` format:

| Tool | Output Format | Key Metrics |
|------|--------------|-------------|
| RFdiffusion | Poly-Gly PDB backbones + ProteinMPNN sequences + AF2 validation CSV | ipTM, pLDDT, i_pAE |
| BindCraft | Ranked PDB files + CSV with all metrics | ipTM, pLDDT, RMSD, shape_complementarity, SAP |
| RFantibody | PDB files + RF2 validation scores | RF2 confidence, CDR geometry |
| BoltzGen | `final_ranked_designs/` directory + `final_designs_metrics_N.csv` | Refolding RMSD, ipTM, pLDDT |
| PXDesign | `summary.csv` + design PDB/CIF files | ipTM, pLDDT, pAE, filter status |

### Recommended Project Structure

```
backend/
  gpu/
    provider.py          # ABC (exists)
    runpod.py            # RunPod client (exists)
  worker/
    tasks.py             # arq tasks (exists -- needs tool-specific payload builders)
  pipelines/             # NEW: tool-specific config generation + result parsing
    __init__.py
    base.py              # Abstract pipeline with generate_config() + parse_results()
    rfdiffusion.py       # RFdiffusion-specific logic
    bindcraft.py         # BindCraft-specific logic
    rfantibody.py        # RFantibody-specific logic
    boltzgen.py          # BoltzGen-specific logic
    pxdesign.py          # PXDesign-specific logic
docker/
  rfdiffusion/
    Dockerfile
    rp_handler.py
  bindcraft/
    Dockerfile
    rp_handler.py
  rfantibody/
    Dockerfile
    rp_handler.py
  boltzgen/
    Dockerfile
    rp_handler.py
  pxdesign/
    Dockerfile
    rp_handler.py
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PDB/CIF format conversion | Custom parser | gemmi library | Edge cases with chain IDs, insertion codes, altloc |
| Protein relaxation | Custom MD | OpenMM (via FreeBindCraft) | Solvation, restraints, clash resolution |
| Shape complementarity | Custom geometry | sc-rs (Rust, MIT) | Validated against PyRosetta results |
| Sequence design | Custom model | ProteinMPNN (dauparas/ProteinMPNN) | Pre-trained, well-validated |
| AF2 structure prediction | Custom prediction | ColabFold / ColabDesign | AF2 weight management, MSA handling |
| CIF residue re-indexing | Custom script | gemmi (BoltzGen/PXDesign require index-1 CIF) | Handles edge cases in mmCIF format |
| RunPod handler boilerplate | Custom HTTP server | `runpod` Python SDK | Handles heartbeat, error capture, streaming |

## Common Pitfalls

### Pitfall 1: RunPod Default Execution Timeout Is 10 Minutes
**What goes wrong:** Jobs fail with TIMED_OUT because the default `executionTimeout` is 600,000 ms (10 min). BindCraft can run 1-4 hours.
**Why it happens:** Must explicitly set `policy.executionTimeout` in the submit payload.
**How to avoid:** Set `executionTimeout` per tool: RFdiffusion 30 min, BindCraft 4 hours, RFantibody 1 hour, BoltzGen 2 hours, PXDesign 2 hours. Add to the `GPUJobSubmission` or the `run_job` task.
**Warning signs:** Jobs consistently fail at exactly 10 minutes.

### Pitfall 2: BoltzGen CIF Residue Indexing
**What goes wrong:** BoltzGen crashes with residue indexing errors.
**Why it happens:** CIF files must have chains starting at residue index 1 using canonical `label_asym_id` (not `auth_asym_id`).
**How to avoid:** Re-index all CIF files with gemmi before passing to BoltzGen or PXDesign. The PDB normalization in Phase 2 handles PDB format but may not produce correct CIF indexing.
**Warning signs:** "IndexError" or residue mismatch errors in BoltzGen logs.

### Pitfall 3: AF2 Weight Download Size
**What goes wrong:** Docker build takes 30+ minutes or image is unexpectedly large.
**Why it happens:** ColabFold AF2 weights are ~3.5 GB. Full database is 556 GB (NOT needed -- only model weights needed).
**How to avoid:** Only download AF2 model parameters (not databases). For ColabFold, download params only. BindCraft auto-downloads on first run -- trigger this during Docker build.
**Warning signs:** Docker image size exceeds 30 GB.

### Pitfall 4: BindCraft Cannot Be Parallelized On a Single GPU
**What goes wrong:** Attempting to run multiple BindCraft trajectories in parallel on one GPU causes OOM.
**Why it happens:** AF2 backpropagation uses full GPU memory for one trajectory at a time.
**How to avoid:** Each RunPod worker runs one BindCraft process. For 500 designs, dispatch 50 parallel workers each running 10 trajectories.
**Warning signs:** CUDA OOM errors.

### Pitfall 5: PXDesign First Run JIT Compilation
**What goes wrong:** First PXDesign run on a fresh worker takes significantly longer than expected.
**Why it happens:** Protenix compiles custom CUDA kernels via JIT on first invocation.
**How to avoid:** Run a dummy inference during Docker build to pre-compile kernels. Or accept the one-time cost.
**Warning signs:** First job takes 2-3x longer than subsequent jobs.

### Pitfall 6: Presigned URL Expiry
**What goes wrong:** RunPod worker tries to upload results but presigned URLs have expired.
**Why it happens:** Default presigned URL expiry is 1 hour (`expires_in=3600`). BindCraft can run 4+ hours.
**How to avoid:** Set presigned URL expiry to `max(job_timeout * 1.5, 7200)`. For BindCraft: 6 hours minimum.
**Warning signs:** HTTP 403 errors when worker tries to upload to R2.

### Pitfall 7: PXDesign MSA Not Provided
**What goes wrong:** PXDesign Extended mode fails or produces unreliable confidence scores.
**Why it happens:** Protenix requires MSA for target folding and confidence estimation.
**How to avoid:** For Phase 4 pilot, use Basic mode (no MSA required). Extended mode requires pre-computed MSA via ColabFold or MMseqs2 -- add as a future enhancement.
**Warning signs:** Low-quality confidence scores, Protenix errors about missing MSA.

### Pitfall 8: Missing PXDesign Endpoint ID in Config
**What goes wrong:** PXDesign jobs fail to dispatch because there is no `runpod_endpoint_pxdesign` in settings.
**Why it happens:** Config only has endpoints for rfdiffusion, rfantibody, bindcraft, boltzgen.
**How to avoid:** Add `runpod_endpoint_pxdesign: str = ""` to `Settings` and `"pxdesign"` to `ENDPOINT_IDS` in `worker/tasks.py`.
**Warning signs:** KeyError or empty endpoint_id when dispatching PXDesign jobs.

### Pitfall 9: JobSpec Missing "pxdesign" Tool Literal
**What goes wrong:** PXDesign jobs rejected by Pydantic validation.
**Why it happens:** `JobSpec.tool` is `Literal["rfdiffusion", "rfantibody", "bindcraft", "boltzgen"]` -- no "pxdesign".
**How to avoid:** Add `"pxdesign"` to the Literal type in `agent/jobspec.py`.
**Warning signs:** Pydantic validation error on job creation.

### Pitfall 10: RFdiffusion Outputs Backbone Only
**What goes wrong:** RFdiffusion output PDBs contain only glycine residues (no designed sequences).
**Why it happens:** RFdiffusion is a backbone generator. ProteinMPNN must run as a post-processing step.
**How to avoid:** The RFdiffusion handler must chain: RFdiffusion -> ProteinMPNN -> AF2 validation. All three steps run within the same RunPod worker.
**Warning signs:** All output sequences are poly-glycine.

## Code Examples

### RunPod Handler Template (rp_handler.py)

```python
# Source: RunPod docs + Kendrew architecture
import os
import subprocess
import requests
import runpod


def download_input(presigned_url: str, local_path: str) -> None:
    """Download target PDB from R2 via presigned GET URL."""
    response = requests.get(presigned_url)
    response.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(response.content)


def upload_output(presigned_url: str, local_path: str) -> None:
    """Upload result file to R2 via presigned PUT URL."""
    with open(local_path, "rb") as f:
        response = requests.put(presigned_url, data=f.read())
        response.raise_for_status()


def handler(job):
    """RunPod serverless handler for [TOOL_NAME]."""
    job_input = job["input"]
    job_spec = job_input["job_spec"]
    input_url = job_input["input_presigned_url"]
    output_urls = job_input["output_presigned_urls"]
    report_url = job_input["report_presigned_url"]

    # 1. Download target PDB
    target_path = "/tmp/target.pdb"
    download_input(input_url, target_path)

    # 2. Generate tool-specific config from job_spec
    config = generate_config(job_spec, target_path)

    # 3. Run design tool
    result = run_tool(config)

    # 4. Upload outputs
    candidates = []
    for i, design_file in enumerate(result["design_files"]):
        upload_output(output_urls[i], design_file)
        candidates.append({
            "rank": i + 1,
            "pdb_key": f"design_{i+1:03d}.pdb",
            "scores": result["scores"][i],
        })

    # 5. Upload metrics CSV
    upload_output(report_url, result["metrics_csv"])

    return {
        "candidates": candidates,
        "candidate_count": len(candidates),
        "next_steps": generate_next_steps(job_spec["tool"]),
    }


runpod.serverless.start({"handler": handler})
```

### Execution Timeout Policy (worker/tasks.py modification)

```python
# Source: RunPod API docs
# Must set per-tool execution timeouts to avoid default 10-minute limit

TOOL_TIMEOUTS_MS: dict[str, int] = {
    "rfdiffusion": 1_800_000,    # 30 min
    "bindcraft": 14_400_000,     # 4 hours
    "rfantibody": 3_600_000,     # 1 hour
    "boltzgen": 7_200_000,       # 2 hours
    "pxdesign": 7_200_000,       # 2 hours
}

# In the submit payload:
payload = {
    "input": submission.input_payload,
    "webhook": submission.webhook_url,
    "policy": {
        "executionTimeout": TOOL_TIMEOUTS_MS.get(tool, 3_600_000),
    },
}
```

### BoltzGen YAML Spec Generation

```python
# Source: BoltzGen README
import yaml

def generate_boltzgen_yaml(job_spec: dict, target_cif_path: str) -> str:
    """Generate BoltzGen YAML design spec from a Kendrew JobSpec."""
    params = job_spec["parameters"]
    hotspots = job_spec["hotspot_residues"]
    chain = job_spec["target_chain"]

    spec = {
        "entities": [
            {
                "file": {
                    "path": target_cif_path,
                    "include": [
                        {"chain": {"id": chain}}
                    ],
                }
            }
        ],
    }

    # Add hotspot binding constraint if specified
    if hotspots:
        min_res = min(hotspots)
        max_res = max(hotspots)
        spec["binding_types"] = [{
            "chain": chain,
            "res_index": f"{min_res}..{max_res}",
        }]

    yaml_path = "/tmp/design_spec.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(spec, f)
    return yaml_path
```

## Testing Strategy

### Validation Test Targets

Use well-characterized targets with known binder design outcomes:

| Target | PDB ID | Residues | Why Good for Testing |
|--------|--------|----------|---------------------|
| IL-7Ra | 7S4E | ~200 | Published RFdiffusion benchmark (Cao et al.); known hotspots |
| PDL1 | 6R3K | ~120 | Small, well-studied; BenchBB target; fast runtime |
| BHRF1 | 5FCG | ~150 | BenchBB target; moderate difficulty |

### Pilot Run Parameters Per Tool

| Tool | Num Designs | Expected Runtime (A100) | Expected Output |
|------|-------------|------------------------|-----------------|
| RFdiffusion | 100 | 5-10 min | 100 poly-Gly backbones + MPNN sequences + AF2 scores |
| BindCraft | 10 final | 30-60 min | 10 ranked PDBs + metrics CSV |
| RFantibody | 100 | 15-30 min | 100 VHH designs + AbMPNN sequences + RF2 scores |
| BoltzGen | 100 | 25-30 min | Ranked designs + metrics CSV |
| PXDesign | 100 (basic mode) | 20-30 min | 100 backbones + MPNN sequences + AF2-IG scores |

### Validation Checklist Per Tool

For each tool, verify:
1. Handler receives input and downloads PDB from R2
2. Tool executes without errors on GPU
3. Output PDBs are valid (parseable, correct chain IDs)
4. Metrics CSV contains expected columns with non-zero values
5. Results uploaded to R2 successfully
6. Webhook fires with correct payload format
7. `CandidateResult` objects parse correctly in the backend
8. Results display correctly on the job page frontend

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual conda environments per tool | Docker images with baked deps | 2024-2025 | Reproducible, no dependency conflicts |
| Download weights at runtime | Bake into Docker image | 2024-2025 | Eliminates cold start weight download |
| Single long-running pods | Serverless with parallel batching | 2025 | Scale to zero, per-second billing |
| PyRosetta for relaxation/scoring | OpenMM + FreeSASA + sc-rs (FreeBindCraft) | 2025 | MIT licensed, no PyRosetta dependency |
| ProteinMPNN from GitHub clone | `pip install protein-mpnn-pip` (PyPI) | 2024 | Simpler dependency management |
| BoltzGen custom install | `pip install boltzgen` (PyPI) | 2025 | Clean package with Docker support |

## Open Questions

1. **RFdiffusion Docker Hub image version**
   - What we know: `rosettacommons/rfdiffusion` exists on Docker Hub with official support
   - What's unclear: Whether the image includes ProteinMPNN and AF2 validation, or just RFdiffusion alone
   - Recommendation: Pull and inspect the image; if it lacks MPNN/AF2, extend it in our Dockerfile

2. **PXDesign Extended Mode MSA in Docker**
   - What we know: Extended mode requires pre-computed MSA; Basic mode does not
   - What's unclear: Whether the MSA computation (ColabFold/MMseqs2) can run inside the Docker image or must be pre-computed externally
   - Recommendation: Start with Basic mode for Phase 4. Add Extended mode with pre-computed MSA in a future phase

3. **BindCraft AF2 Weight Pre-caching**
   - What we know: AF2 weights auto-download on first run; should be baked into image
   - What's unclear: Exact trigger to force download during Docker build (not at runtime)
   - Recommendation: Run a minimal BindCraft invocation during Docker build that triggers the download, then clean up the test output

4. **RFdiffusion SE3nv Environment in Docker**
   - What we know: The conda environment `SE3nv.yml` targets CUDA 11.1; needs customization for different GPUs
   - What's unclear: Whether the official Docker image already handles this, or if we need to manually configure for A100/H100
   - Recommendation: Test the official Docker image first; only build custom if it fails

5. **R2 Presigned URL for Input PDB**
   - What we know: `worker/tasks.py` currently generates presigned PUT URLs for outputs. It passes `job_spec.target_pdb_path` in the payload but no presigned GET URL.
   - What's unclear: Whether the RunPod worker can directly access MinIO/R2 or needs a presigned GET URL
   - Recommendation: Generate a presigned GET URL for the input PDB and pass it in the payload. The RunPod worker has no direct R2 credentials.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker | Image builds | Must verify on build machine | -- | None (required) |
| RunPod API key | All GPU jobs | Config placeholder exists | -- | None (required) |
| R2 / MinIO | Input/output storage | Local MinIO via Docker Compose | -- | MinIO for local dev |
| NVIDIA GPU | Local Docker testing | Must verify | -- | Skip local testing; test on RunPod directly |
| RunPod container registry | Image hosting | Available (Docker Hub or RunPod registry) | -- | Docker Hub |

**Missing dependencies with no fallback:**
- RunPod API key (must be provisioned before testing)
- RunPod endpoint creation (must be done via RunPod dashboard or API for each tool)

**Missing dependencies with fallback:**
- Local GPU for Docker testing -- can test on RunPod directly instead

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | `backend/pytest.ini` or `pyproject.toml` |
| Quick run command | `pytest backend/tests/pipelines/ -x -q` |
| Full suite command | `pytest backend/tests/ -x` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PIPE-01 | RFdiffusion handler processes input and returns candidates | integration | Manual GPU test on RunPod | -- Wave 0 |
| PIPE-02 | BindCraft handler runs 4 stages | integration | Manual GPU test on RunPod | -- Wave 0 |
| PIPE-03 | RFantibody handler produces VHH designs | integration | Manual GPU test on RunPod | -- Wave 0 |
| PIPE-04 | BoltzGen handler runs full pipeline | integration | Manual GPU test on RunPod | -- Wave 0 |
| PIPE-05 | PXDesign handler runs basic mode | integration | Manual GPU test on RunPod | -- Wave 0 |
| PIPE-06 | Result parsing normalizes to CandidateResult | unit | `pytest backend/tests/pipelines/test_parsers.py -x` | -- Wave 0 |
| PIPE-07 | Config generation produces valid tool configs | unit | `pytest backend/tests/pipelines/test_configs.py -x` | -- Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest backend/tests/pipelines/ -x -q`
- **Per wave merge:** Full backend test suite
- **Phase gate:** All 5 tools validated with real GPU pilot runs

### Wave 0 Gaps
- [ ] `backend/tests/pipelines/test_parsers.py` -- unit tests for each tool's result parser
- [ ] `backend/tests/pipelines/test_configs.py` -- unit tests for config generation per tool
- [ ] `backend/pipelines/` module -- does not exist yet

## Sources

### Primary (HIGH confidence)
- [RunPod API docs - send requests](https://docs.runpod.io/serverless/endpoints/send-requests) - API endpoints, policies, rate limits
- [RunPod endpoint configurations](https://docs.runpod.io/serverless/references/endpoint-configurations) - timeouts, scaling, GPU types
- [RunPod handler functions](https://docs.runpod.io/serverless/workers/handler-functions) - handler format, error handling
- [RunPod model caching](https://docs.runpod.io/serverless/endpoints/model-caching) - bake vs download strategy
- [FreeBindCraft GitHub](https://github.com/cytokineking/FreeBindCraft) - installation, Docker, --no-pyrosetta
- [RFdiffusion GitHub](https://github.com/RosettaCommons/RFdiffusion) - weight URLs, SE3nv, Docker
- [RFantibody GitHub](https://github.com/RosettaCommons/RFantibody) - Dockerfile, weight download, RF2 config
- [BoltzGen GitHub](https://github.com/HannesStark/boltzgen) - pip install, YAML format, Docker, protocols
- [PXDesign GitHub](https://github.com/bytedance/PXDesign) - installation, YAML config, MSA requirements
- [ProteinMPNN GitHub](https://github.com/dauparas/ProteinMPNN) - weights, model selection

### Secondary (MEDIUM confidence)
- [RFdiffusion Docker Hub](https://hub.docker.com/r/rosettacommons/rfdiffusion) - official image existence confirmed
- [Adaptyv Bio BenchBB](https://start.adaptyvbio.com/benchbb) - benchmark targets for validation
- [RunPod worker deployment](https://docs.runpod.io/serverless/workers/deploy) - Docker platform requirements

### Tertiary (LOW confidence)
- RunPod network volume vs baked weights sizing (community discussion, not official benchmarks)
- PXDesign CUTLASS 3.5.1 requirement for DeepSpeed Evo attention (from README, not independently verified)

## Metadata

**Confidence breakdown:**
- Standard stack (RunPod API, handler format): HIGH - verified against official docs
- Docker images per tool: MEDIUM-HIGH - official Dockerfiles and READMEs consulted; exact image sizes are estimates
- Architecture patterns: HIGH - based on existing codebase analysis + RunPod docs
- Pitfalls: HIGH - derived from tool-specific documentation and known issues
- Testing strategy: MEDIUM - test targets chosen from published benchmarks; runtimes are estimates

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (tools are actively maintained; check for new releases before building)
