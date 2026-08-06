# SMOKE-TEST-SPEC.md — Canonical Spec for Phase-4 Per-Tool Agents

## Purpose

You are one of four agents (RFdiffusion, RFantibody, BoltzGen, or PXDesign) assigned to fix your tool's Modal pipeline. BindCraft already works on Modal. Your tool does not. Every run produces a different error.

> **Correction, 2026-08-06.** "BindCraft already works" was true of its
> *webhook* path and only that. Because BindCraft was the reference the four
> agents copied, it was never itself in scope here — so it received the uniform
> wrapper-side reader (`infrastructure/modal/bindcraft_app.py` opens
> `/tmp/smoke_results.json` and returns it as `smoke_result`) and never
> received the pipeline-side writer. `smoke` and `mini_pilot` appeared zero
> times in `docker/bindcraft/run_pipeline.py`, so `smoke_result` was `None` on
> every tier and a caller invoking the Modal function directly got nothing
> back. Fixed on `feat/bindcraft-smoke-tier`; BindCraft now implements Layer 2
> and Layer 3 below like the other four. Two BindCraft-specific deviations are
> documented there: it bakes no `/opt/smoke_target.pdb` fixture, so a
> caller-supplied `input_pdb_url` / `input_presigned_url` is mandatory rather
> than optional; and it has no stage to stub, so its tiers differ by cost
> bounds (`max_trajectories`, a wall-clock cap, and the filter set) instead of
> by a `skip_af2` flag.

Your job: add fail-fast checks at three layers and iterate until your tool returns two real binders in mini-pilot mode.

This file is the **contract** every agent follows. Read it end-to-end before editing.

## File ownership (strict)

You only edit files under your tool's prefix. Do not touch files assigned to other agents or shared infrastructure files — the orchestrator has already set those up.

| Agent | Files you may edit | Files you must NOT edit |
|---|---|---|
| RFdiffusion | `docker/rfdiffusion/*`, `backend/pipelines/rfdiffusion.py`, `infrastructure/modal/rfdiffusion_app.py` | any other `docker/*`, other `backend/pipelines/*.py`, other `infrastructure/modal/*_app.py`, `backend/gpu/*`, `backend/webhooks/*` |
| RFantibody | `docker/rfantibody/*`, `backend/pipelines/rfantibody.py`, `infrastructure/modal/rfantibody_app.py` | same as above |
| BoltzGen | `docker/boltzgen/*`, `backend/pipelines/boltzgen.py`, `infrastructure/modal/boltzgen_app.py` | same as above |
| PXDesign | `docker/pxdesign/*`, `backend/pipelines/pxdesign.py`, `infrastructure/modal/pxdesign_app.py` | same as above |

## Working reference (every agent reads)

BindCraft is the working example. Study:
- `infrastructure/modal/bindcraft_app.py` — Modal wrapper pattern (now extended to support tier-based return; see section "Modal wrapper tier contract" below).
- `docker/bindcraft/run_pipeline.py` — already has a good `startup_check()` you can copy-adapt for your preflight.
- `backend/pipelines/bindcraft.py` — `pilot_preset()` shape. Add `smoke_preset()` and `mini_pilot_preset()` following the same pattern.
- `.planning/phases/04-pipeline-validation/04-RESEARCH.md` — canonical pitfalls list for your tool. Read the section for your tool before debugging.

## Target PDB

Every smoke and mini-pilot run uses the same target: PD-L1 IgV domain.

- **Path inside repo:** `backend/tests/fixtures/target_pdl1.pdb`
- **Source:** PDB 4ZQK chain A (residues 18–132), PD-L1 ectodomain IgV fold.
- **Chain ID:** `A`.
- **Rationale:** easiest possible binder-design target — ships as the BindCraft demo, has published binders from all four tools. A failing smoke run implicates your code, not the target.

When invoking `modal run`, serve this file to the container via a `file://`-style staging mechanism (see "Shared invocation command" below) or bake it into the image for smoke tests only.

## Modal wrapper tier contract (already implemented by orchestrator)

Each `infrastructure/modal/<tool>_app.py` now supports a `tier` field in the payload:
- `"tier": "smoke"` — use `smoke_preset()`, return results inline via `/tmp/smoke_results.json`.
- `"tier": "mini_pilot"` — use `mini_pilot_preset()`, return results inline via `/tmp/smoke_results.json`.
- absent or any other value — legacy webhook path.

The wrapper reads `/tmp/smoke_results.json` after the subprocess exits and merges its contents into the function return dict under key `smoke_result`. You do not need to modify the wrapper; just write JSON to that path in the configured shape.

## Required changes in your tool's files

### Layer 1 — Build-time fail-fast `RUN` stage

At the end of `docker/<tool>/Dockerfile.modal`, append a `RUN` line that:
1. Imports every Python package the pipeline uses (e.g. `python3 -c "import rfdiffusion, proteinmpnn"`).
2. Asserts every weight file exists (`test -f /opt/.../weights.pt`).
3. Asserts every CLI the pipeline will call is on `$PATH` (`which qvscorefile`).

This is a single `RUN` with `&&` chains. If any check fails, `modal deploy` fails before any GPU is spent.

### Layer 2 — `preflight()` in `run_pipeline.py`

Add a function `preflight(payload: dict) -> None` called at the top of `main()` before any compute. It must complete in ≤ 60 s on GPU. It must check:

1. Payload parses; all required keys present.
2. Target PDB accessible (`HEAD <input_pdb_url>` returns 200, or local-file equivalent).
3. `torch.cuda.is_available()` or `jax.devices("gpu")` reports the expected SKU.
4. Every tool CLI in the pipeline responds to `--help` with exit 0.
5. `/tmp/smoke_results.json` is writable.

On any failure, write a structured error to `/tmp/smoke_results.json` and `sys.exit(1)`:

```python
{"status": "FAILED",
 "error": {"bucket": "preflight", "check": "<name>", "detail": "<stderr>"}}
```

Copy the `startup_check()` function in `docker/bindcraft/run_pipeline.py` as your starting point.

### Layer 3 — `smoke_preset()` + `mini_pilot_preset()` in `backend/pipelines/<tool>.py`

Add two methods to your `ToolPipeline` subclass:

```python
def smoke_preset(self) -> dict:
    """N=1, cheapest config, scores may be stubbed. Proves pipeline runs."""
    return { ... }

def mini_pilot_preset(self) -> dict:
    """N=2, real scoring, full pipeline. Final success gate."""
    return { ... }
```

Your `run_pipeline.py` detects the tier from payload and selects the preset:
```python
tier = json.loads(os.environ["JOB_PAYLOAD"]).get("tier", "webhook")
if tier == "smoke":
    params = smoke_preset
elif tier == "mini_pilot":
    params = mini_pilot_preset
```

### Layer 3 output shape (required)

On success, write to `/tmp/smoke_results.json`:
```json
{
  "status": "COMPLETED",
  "output": {
    "candidates": [
      {
        "rank": 1,
        "pdb_key": "design_001.pdb",
        "pdb_content_b64": "<base64 of PDB file contents>",
        "scores": {"ipTM": 0.72, "pLDDT": 85.4, ...}
      }
    ]
  },
  "tier": "smoke",
  "gpu_seconds": 142
}
```

`pdb_content_b64` is **required** — it lets the orchestrator verify designs without MinIO/presigned URLs. Do `base64.b64encode(Path(pdb_path).read_bytes()).decode()`.

In `mini_pilot` mode: `candidates` has length 2; every score must be a real float (no stubs, no zeros, no NaN).

## Shared invocation command

Deploy, then fire:
```
modal deploy infrastructure/modal/<tool>_app.py
modal run infrastructure/modal/<tool>_app.py::run_tool --payload '<json>'
```

Minimal payload JSON (adapt for your tool's job_spec shape):
```json
{
  "tier": "smoke",
  "job_spec": {
    "tool": "<tool>",
    "target_chain": "A",
    "hotspot_residues": [],
    "parameters": {}
  },
  "input_pdb_url": "<url served by orchestrator harness or baked path>",
  "job_id": "smoke-<timestamp>",
  "webhook_url": "",
  "job_token": ""
}
```

If no harness is set up, bake the PD-L1 fixture into the image (`COPY backend/tests/fixtures/target_pdl1.pdb /opt/smoke_target.pdb`) and have `run_pipeline.py` use that path when `input_pdb_url` is empty and `tier` is smoke/mini_pilot. Document this choice in your report.

## Triage loop

```
while not_green:
  edit -> modal deploy -> modal run --smoke -> classify error -> fix
```

Error buckets and where to edit:

| Bucket | Signal | Where to fix |
|---|---|---|
| Build failure | `modal deploy` errors; Layer 1 RUN fails | Dockerfile.modal — missing weights, wrong paths, dep conflict |
| Import | container starts, `ImportError` before preflight | Dockerfile.modal pip/conda installs |
| Preflight | preflight() exits 1 with bucket="preflight" | Dockerfile (weight bake) or CLI PATH |
| Tool invocation | subprocess exits non-zero inside main pipeline | run_pipeline.py CLI args, paths, env vars |
| Output parse | tool runs, zero PDBs or zero metrics in expected dir | run_pipeline.py glob patterns, result parsing |
| Serialization | exit 0 but `/tmp/smoke_results.json` missing/malformed | run_pipeline.py result write |

## Success criterion (per agent)

1. `modal deploy` completes cleanly.
2. `modal run ...::run_tool --payload '{"tier":"smoke",...}'` returns `smoke_result.status == "COMPLETED"` with `len(candidates) == 1`, twice in a row.
3. `modal run ...::run_tool --payload '{"tier":"mini_pilot",...}'` returns `smoke_result.status == "COMPLETED"` with `len(candidates) == 2`, each with a parseable `pdb_content_b64` (≥ 50 ATOM lines, loads cleanly in `Bio.PDB.PDBParser`) and `scores` with real floats in sane ranges.
4. Step 3 passes twice in a row.

## Per-tool exceptions

**PXDesign — mini_pilot N=1 (not N=2).** Each PXDesign design takes ~35 GPU-min on A100-80GB because every candidate runs AF2-initial-guess validation inline. Two designs per run pushes mini_pilot past a reasonable wall-clock budget (≥ 70 GPU-min per attempt, and two consecutive green runs are required). Per user decision on 2026-04-22, PXDesign's mini_pilot success criterion is reduced to `len(candidates) == 1` with real ipTM/pLDDT/pAE (no stubs, no zeros, no NaN). One candidate with real AF2-IG scores is sufficient pipeline-end-to-end evidence. Other tools (BoltzGen, RFantibody, RFdiffusion) retain N=2 as normal. Production pilots invoked via `pilot_preset` are unchanged (N≥2); this exception applies only to the `mini_pilot` smoke-verify tier.

## Budget (hard stop)

- 30 GPU-minutes total for smoke iteration.
- 20 GPU-minutes total for mini-pilot iteration.
- 10 `modal deploy` attempts max.

If exhausted, stop. Write a **blocker report** to `docs/blocker-<tool>.md` with:
- Which layer you reached (build / import / preflight / compute / output / serialization).
- The last error message verbatim.
- Your suspected root cause.
- What you would try next if given more budget.

Return that path in your final message so the orchestrator can surface it to the user.

## What not to do

- Do not edit shared infrastructure (`backend/gpu/`, `backend/webhooks/`, `backend/worker/`, other tools' files).
- Do not add new dependencies to `requirements.txt` or the Dockerfile if the failure is not a missing dep.
- Do not disable preflight checks to "get past" them — fix the underlying cause.
- Do not commit secrets, API keys, or credentials.
- Do not skip Stage A (smoke) to jump straight to Stage B (mini-pilot). The N=1 run is your cheap signal.
- Do not spend more than your GPU budget. Write the blocker report instead.

## Reporting format (final message)

End your session with a single structured summary:
```
Tool: <name>
Final verdict: green | blocked
Smoke: PASS (twice) | FAIL — <reason>
Mini-pilot: PASS (twice) | FAIL — <reason> | N/A (smoke not green)
Iterations: <n> modal deploy, <n> modal run
GPU-minutes used: <n>
Final scores: [{rank: 1, ipTM: ..., pLDDT: ...}, {rank: 2, ...}]  # if green
Files changed: docker/<tool>/Dockerfile.modal, docker/<tool>/run_pipeline.py, backend/pipelines/<tool>.py
Blocker report: docs/blocker-<tool>.md  # if blocked
```
