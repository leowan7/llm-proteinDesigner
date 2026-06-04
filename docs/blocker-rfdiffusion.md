# RFdiffusion Modal smoke-test blocker report

**Agent:** RFdiffusion (Kendrew Phase 4 smoke-testing)
**Date:** 2026-04-22 (initial), **2026-04-26 RESOLVED**
**Status:** ✅ **RESOLVED 2026-04-26.** Mini_pilot completed in 6.3 min on A100-SXM4-80GB
(376.2 s wallclock, 352 GPU-s, 2/2 candidates produced). Root cause was NOT XLA JIT
or persistent cache — it was TF/JAX VRAM contention inside `colabfold_batch`. TensorFlow
(transitive dep for AF2's `tf.data` feature pipeline) preallocated nearly all VRAM at
import; JAX hung silently during XLA JIT. Fix is the LocalColabFold-prescribed env-var
set added to `_af2_env_with_jax_cache()` in commit `d83335c` — `TF_FORCE_GPU_ALLOW_GROWTH=true`,
`XLA_PYTHON_CLIENT_PREALLOCATE=false`, `XLA_PYTHON_CLIENT_ALLOCATOR=platform`,
`XLA_PYTHON_CLIENT_MEM_FRACTION=4.0`, `TF_FORCE_UNIFIED_MEMORY=1`, `TF_ENABLE_ONEDNN_OPTS=0`.

Same fix unblocked tools-hub D2 AF2 + D3 ColabFold the same day (tools-hub commit
`f5257b8`). All three Bug 8-affected pipelines now ship.

Run record (job `rfdiff-mini-pilot-bug8-1777223957`):
- 376.2 s wallclock (was 28+ min hang before fix)
- 352 GPU seconds
- Status: COMPLETED, exit 0
- Stages: preflight → RFdiffusion (~3 min) → ProteinMPNN (~30 s) → AF2 validation
  (~3 min, used to hang here for 18-29 min) → output write
- 2/2 candidates produced

Persistent XLA cache (`kendrew-rfdiffusion-xla-cache` modal Volume) now actually
populates on success; subsequent cold pods will skip the JIT entirely and finish
in ~5 min total.

---

**Original investigation summary (retained for reference, hypotheses now superseded):**

Smoke tier GREEN x2 (both pre-fix and post-fix). Mini_pilot tier STILL BLOCKED after retry 2. JIT compile silence window extended to 28+ min in this retry (vs 20+ min in retry 1) with no AF2 output produced before budget exhaustion. Reproduced again on 2026-04-22 pilot `f2feed5e-a053-452c-9f94-cb05c7022400` (A100-40GB, num_designs=1): Stage 3 AF2 silent for 60+ min, terminated with `Pipeline error`. Same signature.

The "JIT silent compile" framing turned out to be wrong — the silence wasn't a long-running JIT, it was JAX waiting forever for VRAM that TF had preallocated and would never release. Once both frameworks were forced into growth-allocation mode, JAX got memory and finished JIT in seconds, not minutes.

---

## Summary

- `smoke` tier (N=1, AF2 stubbed): PASS x2 consecutive before fix, and PASS x1 post-fix on A100 (83 GPU-s). Smoke path is solid.
- `mini_pilot` tier (N=2, real AF2 multimer): FAILED TWICE to complete inside budget.
  - First attempt (A10G, `--num-recycle 3 --num-models 1`): AF2 for design_0 still running at 25+ min, heartbeat-only logs.
  - Second attempt (A100-40GB — Modal allocated A100 80GB PCIe — `--num-recycle 1 --num-models 1 --stop-at-score 85 --recycle-early-stop-tolerance 0.5`): AF2 for design_0 still running at 20+ min at the point we aborted to preserve budget. SAME silent-heartbeat pattern as before.

The "verified fix" did not resolve the bottleneck. Post-fix GPU-min budget was exhausted (~24 of 30) with 0/2 mini_pilot designs completed, so the 2-consecutive-greens requirement is not achievable under current configuration.

---

## Layer reached

- Layer 1 (Dockerfile build-time validation): PASS. No rebuild needed for fixes (code-only changes).
- Layer 2 (runtime preflight): PASS for both smoke and mini_pilot post-fix. GPU detected as `NVIDIA A100 80GB PCIe`.
- Layer 3 (tool invocation):
  - Smoke: PASS x2 pre-fix, PASS x1 post-fix.
  - Mini_pilot: **STILL BLOCKED inside Stage 3 (AF2 multimer)** on design_0. Same symptom as pre-fix, same silent JIT wait on fresh container.

---

## Post-fix attempt state (verbatim from Modal logs, JOB_ID=mini-post-fix-1776872054)

```
2026-04-22 15:32:12,458 [INFO] === Preflight ===
2026-04-22 15:32:12,458 [INFO] GPU: NVIDIA A100 80GB PCIe
2026-04-22 15:32:13,949 [INFO] Preflight: OK (tier=mini_pilot)
2026-04-22 15:32:13,949 [INFO] Downloading AF2 multimer weights to /opt/colabfold_weights (this takes 3-5 min)...
2026-04-22 15:32:13,953 [INFO] AF2 weights downloaded successfully
2026-04-22 15:32:13,965 [INFO] Chain A residue range: 18-132
2026-04-22 15:33:43,657 [INFO] Command finished in 89.7s (exit code 0)    # RFdiffusion stage
2026-04-22 15:33:43,658 [INFO] RFdiffusion emitted 2 backbone PDBs
2026-04-22 15:33:50,100 [INFO] Command finished in 5.7s (exit code 0)     # ProteinMPNN stage
2026-04-22 15:33:50,101 [INFO] ProteinMPNN produced 2 designed sequences
2026-04-22 15:33:50,102 [INFO] === Stage 3: AF2 multimer validation ===
2026-04-22 15:33:50,111 [INFO] FASTA /tmp/.../mpnn_output/seqs/design_0.fa: 3 entries, lengths=[59, 59, 59]
2026-04-22 15:33:50,111 [INFO] AF2 input for design_0: target_len=115, binder_len=59
2026-04-22 15:33:50,112 [INFO] Running: colabfold_batch ... --num-recycle 1 --num-models 1 ...
                                        --stop-at-score 85 --recycle-early-stop-tolerance 0.5
2026-04-22 15:34:50,120 [WARNING] Heartbeat failed ...
... (heartbeat-only output, one per minute, no colabfold progress) ...
2026-04-22 15:53:50,152 [WARNING] Heartbeat failed ...
[manually aborted via `modal app stop` at ~20 min AF2 elapsed to preserve budget]
```

Target complex: 174 residues total (115-residue target + 59-residue binder). Same fixture as pre-fix; RFdiffusion and ProteinMPNN stages both ran identically fast.

---

## Why the verified fix didn't help

The orchestrator's hypothesis was that A10G GPU bandwidth was the limit and A100 + reduced recycles would bring per-design AF2 into budget. The empirical result suggests otherwise:

1. **Modal assigned A100 80GB PCIe, not A100 40GB.** Same memory bandwidth (1555 GB/s) as 40GB — bandwidth argument should still apply. Inference time per recycle at 174 residues should be ~1-2 min on A100, so 1 recycle × 1 model × 2 designs = 2-4 min total AF2 work, not 20+ min.
2. **Bottleneck is JAX/XLA JIT compile, not inference.** ColabFold's multimer_v3 model compile on first invocation takes 10-15 min wall-clock CPU-side regardless of GPU tier (A10G, A100, H100 all same CPU-side compile). `--num-recycle` and `--stop-at-score` only affect post-compile inference, not compile cost. `--num-models 1` prevents 5x model-recompile but the first model still pays full JIT.
3. **Silent stdout masks progress but isn't the root cause.** Colabfold doesn't emit per-step compile progress; we only see nothing for the entire JIT window. Even with line-buffering we'd just see 15 min of silence followed by quick inference.

The prior blocker report's suggestion #3 ("Pre-warm JAX JIT in the image") was rejected in the task spec ("Don't pre-warm JIT at build time — Modal builders are CPU-only; JAX CUDA JIT requires a GPU device to compile"). That is correct as stated, but it means **there is no escape from paying the 10-15 min JIT cost on every cold container**. Modal does not offer warm pools for `@app.function`.

---

## What the fix DID achieve

- Code is cleaner: tier-gated AF2 args (1 recycle + early-stop for smoke/mini_pilot, 3/5 unchanged for legacy).
- Backend and Modal GPU SKU are now consistent (both `A100-40GB`).
- Smoke tier confirmed still passing on A100 post-fix.
- Deploy is fast (~7 s; no Dockerfile rebuild triggered).

These changes are worth keeping even though they didn't unblock mini_pilot on their own.

---

## Next-try suggestions (updated, after the "verified fix" did not work)

1. **Bake the XLA compile cache into the image** — use Modal's `@app.build()` hook with a GPU (`gpu="T4"` or smallest available for builds) to run a dummy 174-residue colabfold forward pass, capture `~/.cache/jax` + `~/.cache/xla_cache`, and embed in a later image layer. Cold containers would reuse the cache and skip the 10-15 min compile. Modal docs: https://modal.com/docs/guide/custom-container#running-functions-during-image-build. This contradicts the task spec note but the spec note's premise (no GPU at build time) is wrong for recent Modal — `modal.App.build()` does support GPU builds.

2. **Switch to a warm-pool pattern** — `modal.Cls` with `@modal.enter()` that precompiles the model on container start and keeps the container alive between calls (`container_idle_timeout=600`). First call still pays JIT but subsequent calls are instant. Doesn't help the "2 consecutive greens" requirement unless the second green reuses the warm container.

3. **Drop ColabFold for a lighter AF2 implementation** — e.g. ESMFold (no JIT, ~10s inference on 174 residues) or AF2-reduced (single-model, pre-compiled TF graph). Sacrifices the "real AF2 multimer" scoring fidelity but mini_pilot's purpose is pipeline-shape validation; scores just need to be real floats in plausible ranges.

4. **Raise mini_pilot budget to 45 GPU-min** — conservatively, 2 designs × (15 min JIT + 2 min inference) + 2 min RFdiff/MPNN ≈ 36 min per run. With safety margin, 45 min would fit. But this re-opens the "30 GPU-min cap" conversation in the task spec.

5. **Redefine mini_pilot to N=1 with a warm cache** — the orchestrator was explicit that N=2 is load-bearing for the spec, so this is only viable if (1) or (2) also apply.

---

## Artifacts

- Docker image: `ranomics-rfdiffusion-prod` (Modal). No rebuild needed for fixes.
- Modal app logs: `modal app logs ranomics-rfdiffusion-prod`.
- Smoke results (post-fix, 83 GPU-s): `smoke_result.status=COMPLETED`, 1 candidate, scores `{ipTM: 0.46, pLDDT: 71.0, i_pAE: 11.9, filter_status: "stub (smoke)"}`, pdb_b64_len=63608.
- Invocation helper script: `scratch/modal_spike/invoke_rfdiff.py` (uses `modal.Function.from_name` + `.remote()` so dict payload works; `modal run ... --payload` chokes on dict annotation).

## Files changed this session (see git diff)

- `infrastructure/modal/rfdiffusion_app.py` — `_GPU` A10G -> A100-40GB.
- `docker/rfdiffusion/run_pipeline.py` — `stage_af2_validation(..., tier="")` signature; tier-gated colabfold args (`--num-recycle 1`, `--stop-at-score 85`, `--recycle-early-stop-tolerance 0.5`); smoke/mini_pilot call site passes `tier=tier`; legacy prod call site unchanged (no `tier=`).
- `backend/pipelines/rfdiffusion.py` — `gpu_sku` A10G-24GB -> A100-40GB (matches Modal).

## Budget used this session

- `modal deploy` attempts: 1 / 3 (code-only, no Dockerfile rebuild).
- Smoke GPU-min: ~1.4 post-fix (83 s single run).
- Mini_pilot GPU-min: ~22 / 20 (aborted incomplete; same silent-JIT pattern as pre-fix).
- Total: ~24 / 30 GPU-min. Stopped before second mini_pilot to avoid blowing budget on a run that would fail the same way.

---

# Retry 2 — Persistent XLA cache via Modal Volume (2026-04-22)

## What was attempted

Hypothesis: prior retries failed because the cold JAX/XLA JIT compile (~10–15 min wall-clock) was hit on every cold container. Solution plan: persist `/root/.cache/jax` to a named Modal Volume so that first run populates the cache and subsequent cold containers reuse compiled HLO.

### Changes landed this retry
- `infrastructure/modal/rfdiffusion_app.py`
  - Added `xla_cache_volume = modal.Volume.from_name("kendrew-rfdiffusion-xla-cache", create_if_missing=True)`.
  - `@app.function(..., volumes={"/root/.cache/jax": xla_cache_volume})`.
  - `xla_cache_volume.commit()` after subprocess exits, with non-fatal exception handling.
- `docker/rfdiffusion/run_pipeline.py`
  - New helper `_af2_env_with_jax_cache()` sets `JAX_COMPILATION_CACHE_DIR=/root/.cache/jax`, `JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS=0`, `JAX_PERSISTENT_CACHE_MIN_ENTRY_SIZE_BYTES=0`, ensures the dir exists, returns a merged env.
  - `run_command()` now accepts an `env` kwarg (backwards-compatible — `None` means inherit).
  - The colabfold_batch call in `stage_af2_validation` passes `env=_af2_env_with_jax_cache()`.
  - `preflight()` and `stage_af2_validation` both log cache state: `JAX persistent cache: N files at /root/.cache/jax` so cold-vs-warm is obvious from logs.

Deploy succeeded in ~15 s (code-only). Volume `kendrew-rfdiffusion-xla-cache` created cleanly and visible in `modal volume list`.

## Observed result

Mini_pilot was launched once to populate the cache:
```
2026-04-22 16:28:39 [INFO] GPU: NVIDIA A100-SXM4-40GB
2026-04-22 16:28:42 [INFO] JAX persistent cache: 0 files at /root/.cache/jax
2026-04-22 16:28:42 [WARNING] JAX cache cold — first AF2 run may take 10-15 min for XLA compile...
2026-04-22 16:31:29 [INFO] RFdiffusion finished in 167s (exit 0), 2 backbones emitted
2026-04-22 16:31:37 [INFO] ProteinMPNN finished in 7.8s (exit 0), 2 sequences emitted
2026-04-22 16:31:37 [INFO] === Stage 3: AF2 multimer validation ===
2026-04-22 16:31:37 [INFO] Running: colabfold_batch ...design_0 ... --num-recycle 1 --num-models 1 --stop-at-score 85
2026-04-22 16:32:37 → 16:59:37 [WARNING] Heartbeat failed (every 60 s, no colabfold stdout in between)
```

colabfold_batch stayed silent for **28 min** before the run was force-stopped at the 31-min mini_pilot budget boundary. No AF2 output files were produced, so the Volume-backed cache was never populated with compiled HLO — the `commit()` would have written an empty (or near-empty) directory.

GPU was `NVIDIA A100-SXM4-40GB` (confirmed). Preflight and Volume wiring worked. RFdiffusion and ProteinMPNN stages ran in ~3 min total, identical to retry 1. The **only** stage that hangs is colabfold_batch's first-call JIT compile.

## Why retry 2 didn't work

The plan's premise was "first run populates cache, total 17–22 min." In practice the first-call JIT compile exceeded 28 min without producing output, and we can't observe whether it would eventually finish because heartbeat-only logs provide no intermediate JIT progress and the Modal function timeout is 23 h (so it wouldn't self-abort). Three possibilities:

1. **ColabFold's JIT doesn't actually complete** on this A100-SXM4-40GB + CUDA 11.8 + colabfold version combo — could be a memory-copy deadlock, OOM during HLO lowering, or a JAX version incompatibility with Modal's GPU driver stack. No way to tell without stdout.
2. **ColabFold IS compiling but very slowly** — our 174-residue complex (115 target + 59 binder) is not exceptional in size, but the multimer_v3 model's per-shape compile can be slow on first touch and we may have underestimated it (30–40 min rather than 10–15 min).
3. **Writing the JAX cache to a Volume-mounted FUSE path adds write amplification** — every compiled sub-HLO fragment triggers a Volume sync, potentially slowing JIT dramatically. Modal Volume writes go through a FUSE layer that is not optimized for many small files.

Option 3 is suspicious because it would explain why this retry is *worse* than retry 1 (no Volume; same silent-JIT pattern but longer). The cache commit on a 28-min run aborted before writing anything — but the act of *writing into* the mounted path during JIT might be the new bottleneck introduced by this fix.

## What would unblock this (unchanged from retry 1, plus new items)

1. **Use a non-Volume-backed tmpfs cache path, and copy-out on success.** Write cache to `/tmp/jax` (fast local tmpfs during JIT), then after colabfold_batch returns copy the compiled HLO into the Volume via a Python shutil step. Reverses the current pattern: persistent on commit, not during write.

2. **Bake the compiled cache into the image via `@app.build()` with GPU** — generate the HLO once offline, embed in image. Guaranteed zero cold-compile on all future runs. This is probably the highest-value path; the original spec's claim that "Modal builders are CPU-only" was wrong — Modal `App.build()` supports GPU as of 2025.

3. **Switch AF2 to ESMFold or AF2-reduced**. ESMFold has no JIT, single-model inference in ~10 s on A100 for 174 residues. Scores would be ESMFold-based (`pLDDT`, `plDDT`) instead of ColabFold ipTM/pAE — needs downstream alignment but satisfies mini_pilot's "real floats in plausible ranges" requirement.

4. **Raise mini_pilot per-run budget to 45 GPU-min** AND commit to waiting for first-run completion. We have no confirmation that the JIT ever completes — budget expansion without a cache strategy is a gamble.

5. **Contact Modal support** with the app ID and timestamps above — the 28-min silent JIT may indicate a platform-level issue (driver/cuda version mismatch with colabfold JAX build). If they can confirm it's expected, we plan accordingly; if it's a platform bug, the fix is theirs.

## Budget used this retry

- `modal deploy` attempts: 2 / 3 (initial deploy + redeploy after `modal app stop`).
- Mini_pilot GPU-min: ~31 / 30 (single run, aborted at 28-min AF2 JIT silence).
- Smoke GPU-min: 0 (smoke was already green pre-retry-2).
- Total this retry: ~31 / 30. Hard stop, no consecutive greens achieved.
- Cumulative across both retries: ~55 of what would have been a 54 GPU-min budget.

## Files changed this retry (code kept — fix is architecturally valid even though it didn't achieve green)

- `infrastructure/modal/rfdiffusion_app.py` — Modal Volume mount + commit.
- `docker/rfdiffusion/run_pipeline.py` — JAX cache env vars on colabfold subprocess, cache-state logging in preflight + stage_af2_validation, `env` kwarg on `run_command`.

These changes are NOT harmful on their own: if the underlying JIT completes (ever), the cache persists and retry-N runs will be fast. They're also a prerequisite for approaches (1) and (2) above.

---

# Ready-to-run (Leo pulls the trigger)

**Authored 2026-04-22 by Stream E orchestrator. GO.** Status at HEAD (`5f22eec`): wiring verified by code-check. End-to-end attestation is recorded in the consuming web service's validation log, not here. One fresh mini_pilot GPU run is required to close the blocker because commits `97ec005` (A100-40GB + reduced AF2 recycles) and `064266f` (Modal-Volume-backed JAX XLA cache) materially changed the execution path relative to the last recorded smoke-tier green.

## What was verified at HEAD

- `infrastructure/modal/rfdiffusion_app.py`
  - `_GPU = "A100-40GB"` (was `A10G` pre-`97ec005`).
  - `xla_cache_volume = modal.Volume.from_name("kendrew-rfdiffusion-xla-cache", create_if_missing=True)`.
  - `@app.function(..., volumes={"/root/.cache/jax": xla_cache_volume})` — cache mounted read/write.
  - `xla_cache_volume.commit()` after subprocess exit, exception-guarded (non-fatal if commit fails).
- `docker/rfdiffusion/run_pipeline.py`
  - `_af2_env_with_jax_cache()` sets `JAX_COMPILATION_CACHE_DIR=/root/.cache/jax`, `JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS=0`, `JAX_PERSISTENT_CACHE_MIN_ENTRY_SIZE_BYTES=0` and creates the dir before returning.
  - `stage_af2_validation(..., tier=tier)` when `tier in ("smoke","mini_pilot")` replaces `--num-recycle 3` with `--num-recycle 1` and appends `--stop-at-score 85 --recycle-early-stop-tolerance 0.5` to the `colabfold_batch` command.
  - Preflight and `stage_af2_validation` both log `JAX persistent cache: N files at /root/.cache/jax` so cold-vs-warm is observable.
  - No silent-stub fallback on mini_pilot: `_build_smoke_job_spec("mini_pilot")` sets `skip_af2=False`, and the `skip_af2=True` stub scoring branch is unreachable from mini_pilot. Any AF2 RuntimeError or zero parsed results returns `status=FAILED` with an explicit `error.bucket` — there is no exit-zero lie path.
- `backend/pipelines/rfdiffusion.py`
  - `gpu_sku = "A100-40GB"` (matches the Modal app).
  - `mini_pilot_preset()` is `{num_designs: 2, diffusion_steps: 50, skip_af2: False, binder_length: {min:55, max:65}}`.

## Command Leo executes (one run, to populate the JAX cache)

The first run is expected to pay the cold XLA-compile cost. The Volume will persist the compiled HLO so subsequent cold containers reuse it.

```
modal deploy infrastructure/modal/rfdiffusion_app.py   # only if not already deployed on HEAD

python - <<'PY'
import modal, time
f = modal.Function.from_name("ranomics-rfdiffusion-prod", "run_tool")
payload = {
    "tier": "mini_pilot",
    "job_id": f"mini-pilot-{int(time.time())}",
    "job_tier": "mini_pilot",
    "job_spec": {
        "target_chain": "A",
        "parameters": {
            "num_designs": 2,
            "diffusion_steps": 50,
            "skip_af2": False,
            "binder_length": {"min": 55, "max": 65},
        },
    },
    "input_pdb_url": "",   # baked fixture /opt/smoke_target.pdb is used for smoke/mini_pilot
    "webhook_url": "",
    "job_token": "",
    "upload_urls_endpoint": "",
    "total_budget_hours": 2,
}
print(f.remote(payload))
PY
```

`modal run ... --payload` historically rejects dict annotations for this app — use the `modal.Function.from_name(...).remote(...)` pattern above.

## Expected GPU-second envelope (first run, cold cache)

| Stage | Expected wall-clock |
|---|---|
| Container cold-start + preflight | 30–90 s |
| AF2 weight download (3–5 min, cached after first) | 180–300 s |
| RFdiffusion (2 backbones, 50 steps on A100-40GB) | 90–180 s |
| ProteinMPNN (2 sequences) | 5–15 s |
| AF2 multimer JIT compile (first-touch, 174-res complex) | **10–20 min** — dominant cost |
| AF2 inference (1 recycle × 1 model × 2 designs, early-stop) | 60–180 s |
| **Total GPU-seconds (first cold run)** | **~1000–1700** (17–28 min) |

On the second consecutive run — cache warm — the AF2 JIT line collapses to <60 s, bringing total to ~400–700 GPU-seconds (7–12 min).

If wall-clock exceeds 35 GPU-min on the first run without producing AF2 output files, abort and fall through to the escalation path in this doc (bake the cache via `@app.build()` with GPU, or switch AF2 to ESMFold).

## Parser score bounds (what PASS looks like)

The parser produces 2 candidates, each with `scores = {ipTM, pLDDT, i_pAE, filter_status}`. For a PD-L1 / 59-residue binder fixture, the mini_pilot run should produce:

| Score | PASS band | FLAG band | FAIL (rejected) |
|---|---|---|---|
| `ipTM` | 0.15 – 0.95 (real AF2 multimer output) | < 0.10 on one candidate — real but low | exactly 0.46 on rank-1 with `filter_status="stub (smoke)"` — stub leaked into mini_pilot |
| `pLDDT` | 40.0 – 95.0 | < 40 on both | exactly 71.0 with stub marker — stub leaked |
| `i_pAE` | 3.0 – 30.0 (Å) | > 25.0 on both | exactly 11.9 with stub marker — stub leaked |
| `filter_status` | absent, `"pass"`, `"fail"`, or `"below threshold"` | — | contains the substring `"stub"` — this is the silent-fallback anti-pattern |
| PDB | base64 decodes to ≥ 500 ATOM lines across target+binder chains | — | empty or < 100 ATOM lines |

Both candidates must satisfy these bounds and `status` must be `COMPLETED`. Any `status=FAILED` with `error.bucket` is a clean fail (not a silent lie) and can be diagnosed from logs.

## Two-PASS-in-a-row gate

Mini_pilot ship-readiness requires 2 consecutive mini_pilot PASS entries. The first run above populates the XLA cache; the second run (warm) finishes in ~400–700 GPU-s. End-to-end attestation is recorded in the consuming web service's validation log, not here; the web service owns the per-tool feature-flag flip once both passes land.

## Do-not

- Do not execute this run inside this Stream E session — Leo pulls the trigger.
- Do not retry inside the 23-hour Modal function timeout on a stuck run. If `colabfold_batch` emits only heartbeats past the 35 GPU-min envelope above, abort via `modal app stop ranomics-rfdiffusion-prod` to preserve budget and escalate to the "next-try suggestions" further up in this doc.
