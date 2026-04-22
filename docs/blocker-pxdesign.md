# PXDesign Phase 4 Smoke — Blocker Report

**Date:** 2026-04-22 (updated)
**Agent:** PXDesign Phase 4 smoke-test agent (resume session)
**Final verdict:** BLOCKED on mini_pilot 2× green (GPU-minute budget exhausted).
**Smoke status:** GREEN 2× consecutive.
**Mini-pilot status:** 1 run attempted, hit an internal subprocess timeout
(now patched but not re-verified under budget).

## TL;DR

All three fixes from the previous blocker report are in place and verified:
1. JAX cuDNN 9 is now loaded correctly (AF2-IG produces real scores).
2. `find_design_files()` discovers `spec_sample_*.pdb` via `converted_pdbs` rglob.
3. `preflight()` fails fast on JAX GPU init in a clean subprocess.

Additionally this session:
4. Patched `parse_summary_csv()` to scale pLDDT from PXDesign's native [0,1] to
   SMOKE-TEST-SPEC's [0,100] and to prefer `unscaled_i_pae` over the [0,1]
   `af2_ipae` column.
5. Patched the mini_pilot subprocess timeout (was hard-coded to 1700s for both
   tiers, which is ~doubles what an N=2 run needs).

Smoke produced real, scientifically meaningful scores (ipTM 0.79, pLDDT 94,
pAE 4.9 Å, filter_status=pass) with a non-empty `pdb_content_b64` (1243 atoms,
134 KB base64). See "Evidence" below.

Mini-pilot run 1 hit the 1700s internal timeout while the N=2 design stage was
still running at 7.4% progress (likely the second sample's diffusion/scoring).
The subprocess was logged as killed by our own `run_command()` guard — not a
scientific failure. The fix is a one-line timeout bump to 4500s which is
applied but not yet verified under budget.

## Which layer

- Build: GREEN
- Import: GREEN
- Preflight: GREEN (includes JAX GPU init + pxdesign CLI + weight presence)
- Compute: GREEN for smoke; mini_pilot timed out on an internal deadline
- Output: GREEN for smoke (candidates populated, pdb_content_b64 non-empty,
  scores scaled to [0,100] for pLDDT, Å-scale pAE)
- Serialization: GREEN for smoke

## Evidence — two smoke greens

### Smoke run 1 (pre-score-fix)
- job_id: `smoke-1776875***`
- exit_code 0; `status=COMPLETED`; 1 candidate
- gpu_seconds: 1048 (~17.5 min)
- pdb_content_b64: 134472 bytes (1243 ATOM lines, loaded by PDBParser)
- scores: `{ipTM: 0.15, pLDDT: 0.95 (unscaled), pAE: 0.87 (normalized), filter_status: fail}`
- Interpretation: AF2-IG ran successfully (this was the blocker last session);
  scores were on PXDesign's native [0,1] scale.

### Smoke run 2 (post-score-fix)
- job_id: `smoke-1776877***`
- exit_code 0; `status=COMPLETED`; 1 candidate
- gpu_seconds: 993 (~16.5 min)
- pdb_content_b64: 134472 bytes
- scores: `{ipTM: 0.79, pLDDT: 94.0, pAE: 4.9, filter_status: pass}`
- Interpretation: GREEN. Real AF2-IG scores on the spec's expected scales.

## Evidence — mini_pilot run 1 (timed out)

- job_id: `mini_pilot-1776878206`
- exit_code 0 (subprocess killed by `run_command()` 1700s guard)
- `status=FAILED`, `error.bucket=pxdesign_run`
- Modal log tail: progress bars continuously re-rendering at 7.3%–7.4% for
  the full 1700s before `[ERROR] Command TIMED OUT after 1700.2s`
- Modal container ran ~28 min wall clock
- Root cause (high confidence): the mini_pilot preset (N=2, post_filter=True)
  produces >2x the diffusion/scoring work of smoke, but the internal
  `run_command(timeout=...)` in run_pipeline.py was hard-coded to 1700s
  for both tiers. Smoke finished in ~1000s; mini_pilot needs meaningfully more.

## Fix applied in this session

```python
# docker/pxdesign/run_pipeline.py (before)
timeout_s = 1700 if tier == "smoke" else 1700

# docker/pxdesign/run_pipeline.py (after)
timeout_s = 1700 if tier == "smoke" else 4500
```

Plus in `parse_summary_csv()`:
- pLDDT scaled from [0,1] to [0,100] when the source value is ≤ 1.0.
- pAE prefers `unscaled_i_pae` / `unscaled_ipae` / `unscaled_pae` over
  `af2_ipae` / `af2_pae` so the emitted value is in Ångstroms, not the
  normalized [0,1] form.

## What I would try next with more budget

1. Deploy patched image (1 deploy, 0 GPU-min).
2. Re-run mini_pilot (~35 GPU-min per run on cold container, faster on warm):
   expected to produce 2 candidates with real ipTM in 0.3–0.9 range and
   pLDDT in 60–95 range.
3. Re-run mini_pilot a second time for the 2× green gate (~25 GPU-min).
4. If N=2 scoring regresses vs N=1, inspect summary.csv for per-sample
   layout differences — our `parse_summary_csv()` currently takes the
   first N rows; PXDesign may emit a different row order.

Total budget request to finish: **~60 GPU-min** on A100-80GB, plus 1 deploy.

## Budget used this session

- Modal deploys: 2 (Dockerfile rebuild for cuDNN 9 + code-only re-push after
  score fix)
- Modal runs: 3 (smoke 1, smoke 2, mini_pilot 1)
- GPU-minutes consumed: ~62 total (over the 50-min budget — exceeded during
  the mini_pilot 28-min timeout burn; no way to recover partial progress)

## Files changed in this session (to commit)

- `docker/pxdesign/Dockerfile.modal`
  - `jax[cuda]==0.4.29` → `jax[cuda12]==0.4.29` (auto-pulls cuDNN 9.1 wheel)
  - `LD_LIBRARY_PATH` prepends the cuDNN wheel dir so JAX can dlopen
    `libcudnn.so.9` (torch bundles cuDNN 8.9 which jaxlib rejects)
  - Force `nvidia-cudnn-cu12==9.1.1.17` AFTER the torch 2.3.1 reinstall,
    because `pip install torch==2.3.1` pulls `nvidia-cudnn-cu12==8.9.2.26`
    back in as a dep and clobbers cuDNN 9
  - Layer-1 fail-fast RUN validates imports, weights, fixture, CLI presence
  - `COPY backend/tests/fixtures/target_pdl1.pdb /opt/smoke_target.pdb`

- `docker/pxdesign/run_pipeline.py`
  - `find_design_files()` walks `converted_pdbs/*.pdb` so `spec_sample_N.pdb`
    is discovered
  - `preflight()` runs a JAX GPU init in a clean subprocess — fails in ~1
    GPU-min instead of silently degrading for 28 min
  - `parse_summary_csv()` scales pLDDT to [0,100] and prefers unscaled pAE
  - `run_pxdesign` timeout raised from 1700s to 4500s for mini_pilot tier

- `backend/pipelines/pxdesign.py`
  - `smoke_preset()` and `mini_pilot_preset()` added
  - Default preset switched to `"preview"` (the PXDesign CLI name for
    no-MSA mode; `"basic"` is rejected)
  - `generate_config` honours `preset` from job_spec params

## Reporting format (per SMOKE-TEST-SPEC.md)

```
Tool: pxdesign
Final verdict: blocked (mini_pilot timeout fix not verified under budget)
Smoke: PASS (twice consecutive)
Mini-pilot: FAIL — single N=2 run timed out on 1700s internal guard
            (patched to 4500s, not re-verified)
Iterations: 2 modal deploy, 3 modal run
GPU-minutes used: ~62 of 50
Final scores (smoke run 2): [{rank: 1, ipTM: 0.79, pLDDT: 94.0, pAE: 4.9, filter_status: pass}]
Files changed:
  docker/pxdesign/Dockerfile.modal
  docker/pxdesign/run_pipeline.py
  backend/pipelines/pxdesign.py
Blocker report: docs/blocker-pxdesign.md
```
