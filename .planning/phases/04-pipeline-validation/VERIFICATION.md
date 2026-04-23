---
phase: 04-pipeline-validation
status: complete (4 of 5 tools shipped)
closed: 2026-04-22
scope_decision: Launch Kendrew with 4 pipelines; RFdiffusion deferred post-launch
---

# Phase 04 — Pipeline Validation: Closeout

## Launch scope

Kendrew ships with **4 production pipelines**, all live-validated end-to-end
via the real path: `/jobs/launch` → worker dispatch → Modal GPU → webhook →
MinIO upload → Supabase `jobs` + `job_candidates`.

| Tool | Pilot job | Result | Candidates | Duration | Scores populated |
|---|---|---|---|---|---|
| BoltzGen | `8d3ef98e-4b33-4b4a-9f38-bba21eb0abfa` | COMPLETE | 2 | ~9.5 min | yes |
| RFantibody | `2adaa915-…` | COMPLETE | 2 | ~7 min | yes |
| PXDesign | `178ad6a1-…` | COMPLETE | 1 (AF2-IG rejected 1/2) | ~18 min | yes |
| BindCraft | `f1f08a62-f75b-47ca-b84a-63c653880757` | COMPLETE | 2 | ~46 min | yes |

**BindCraft scores sample** (rank 1, 4Z18 target, 53-residue binder):
`ipTM=0.82, pLDDT=0.90, pTM=0.79, i_pAE=0.08, Binder_RMSD=0.45,
Hotspot_RMSD=4.14, Target_RMSD=0.66`

All runs executed on live Modal deployments (`kendrew-{tool}-prod`) with
cloudflared-tunneled webhooks into the local backend; not stubbed or mocked.

## Deferred: RFdiffusion

RFdiffusion is **not in the launch set**. Stage 3 (AF2 multimer validation
via `colabfold_batch`) hangs silently on JAX/XLA JIT compile for 28+ min on
every cold container with no stdout, blocking pilot completion.

Fix options documented in `docs/blocker-rfdiffusion.md`:
1. Bake the compiled XLA cache into the image via `@app.build(gpu="T4")`.
2. Switch AF2 validation to ESMFold (no JIT, ~10 s inference).
3. Use `modal.Cls` with `@modal.enter()` warm-pool pattern.

Last pilot `f2feed5e-a053-452c-9f94-cb05c7022400` reproduced the blocker
(60+ min AF2 silence, terminated as `Pipeline error`). No fix attempted
this cycle — post-launch work.

## Fixes landed this cycle

| Commit | Fix |
|---|---|
| `a0dbcf1` | `fix(bindcraft): populate real scores + add pilot submission helper` — parser keyed by `Design` column, `_modelN` stripping, `Average_*` metric mapping, `_HeartbeatThread` guard, pilot max_trajectories=10, `backend/scripts/submit_pilot.py` helper |
| `5f22eec` | `fix(pxdesign): mini_pilot N=2 -> N=1 for wall-clock-bound verify` |
| `f41e17e` | `fix(pxdesign): jax cuDNN 9 + file discovery + preflight GPU init` |
| `64c4ab0` | `feat(rfantibody): Modal smoke + mini_pilot tiers with 3-layer fail-fast` |
| `4e9eaa1` | `fix(boltzgen): wire smoke/mini_pilot tiers with Layer 1-3 checks` |
| `d421117` | `fix(docker): 6 bug fixes for BindCraft container v7` |

## Plan 01 status

`04-01-SUMMARY.md` covers the pipeline infrastructure (ToolPipeline ABC,
per-tool generators/parsers, PIPELINE_MAP, PXDesign JobSpec integration,
worker presigned URL + timeout). That plan remains accurate.

Subsequent plans (04-02 through 04-07) were container-build and
smoke/mini_pilot validation cycles that materialized as the commits
above rather than as per-plan SUMMARY.md docs. Writing retrospective
per-plan summaries is low-value given we can read the git log; this
phase-level VERIFICATION.md is the authoritative closeout record.

## What ship-readiness means here

End-to-end pilot completion via the real webhook path is the shipping
criterion for Phase 4. All 4 launch-scope tools clear it.

What this does NOT cover (belongs to later phases):
- UI rendering of score fields (Phase 6 — ui-improvements).
- Post-run analysis agent consuming scores (Phase 8).
- Production load testing at concurrency >1 (Phase 5 — production-hardening).
- CI/CD gating of pipeline regressions (Phase 9 — testing-ci-cd).
