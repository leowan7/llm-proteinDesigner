---
phase: 04-pipeline-validation
plan: 01
subsystem: backend/pipelines, backend/worker, backend/agent
tags: [pipelines, config-generation, result-parsing, pxdesign, presigned-urls, timeout-policy]
dependency_graph:
  requires: [backend/agent/jobspec.py, backend/jobs/models.py, backend/gpu/provider.py, backend/storage/client.py]
  provides: [backend/pipelines/*, PIPELINE_MAP registry, per-tool config generators and result parsers]
  affects: [backend/worker/tasks.py, backend/gpu/runpod.py, backend/config.py]
tech_stack:
  added: []
  patterns: [Abstract base class pipeline registry, per-tool timeout and URL expiry]
key_files:
  created:
    - backend/pipelines/__init__.py
    - backend/pipelines/base.py
    - backend/pipelines/rfdiffusion.py
    - backend/pipelines/bindcraft.py
    - backend/pipelines/rfantibody.py
    - backend/pipelines/boltzgen.py
    - backend/pipelines/pxdesign.py
  modified:
    - backend/agent/jobspec.py
    - backend/config.py
    - backend/jobs/models.py
    - backend/gpu/provider.py
    - backend/gpu/runpod.py
    - backend/worker/tasks.py
decisions:
  - "ToolPipeline ABC with generate_config + parse_results + timeout/expiry properties: each tool encapsulates its own config format and output parsing"
  - "Presigned URL expiry defaults to 1.5x execution timeout (min 7200s); BindCraft overrides to 21600s (6hr) for its 4hr runtime"
  - "PXDesign basic preset only in v1 -- extended mode requires MSA preparation, deferred to future release"
  - "RunPod executionTimeout policy sent per-job via optional policy field on GPUJobSubmission dataclass"
metrics:
  duration: 4min
  completed: 2026-03-25
---

# Phase 4 Plan 01: Pipeline Infrastructure Summary

Per-tool config generators and result parsers for all 5 design tools, with PXDesign added to the type system and worker updated with presigned GET URLs and per-tool execution timeouts.

## What Was Built

### Task 1: Pipelines Module (7 files)

Created `backend/pipelines/` with an abstract base class and 5 concrete implementations:

- **base.py**: `ToolPipeline` ABC defining `generate_config()`, `parse_results()`, `execution_timeout_ms`, and `presigned_url_expiry_seconds`. Default URL expiry is 1.5x timeout with 7200s floor.
- **rfdiffusion.py**: Generates Hydra CLI override args (contigmap.contigs, ppi.hotspot_res, inference.num_designs). 30min timeout.
- **bindcraft.py**: Generates JSON settings (starting_pdb, chains, lengths, hotspots). 4hr timeout, 6hr URL expiry.
- **rfantibody.py**: Generates epitope/CDR config (epitope_residues, cdr_design, framework). 1hr timeout.
- **boltzgen.py**: Generates YAML design spec (entities, binder.length, hotspots, protocol). 2hr timeout.
- **pxdesign.py**: Generates YAML task spec (target.file, chains, binder_length, preset=basic). 2hr timeout.
- **__init__.py**: Exports `PIPELINE_MAP` dict mapping all 5 tool name strings to pipeline instances.

### Task 2: PXDesign Integration and Worker Fixes (6 files)

- **jobspec.py**: Added `"pxdesign"` to the `tool` Literal type.
- **config.py**: Added `runpod_endpoint_pxdesign` setting.
- **models.py**: Added `"pxdesign": JobStage.RUNNING_GENERATION` to TOOL_STAGE_MAP.
- **provider.py**: Added optional `policy: dict | None = None` field to GPUJobSubmission.
- **runpod.py**: Includes `policy` dict in POST payload when present.
- **worker/tasks.py**: Looks up pipeline by tool name, generates presigned GET URL for input PDB with tool-specific expiry, sets per-tool executionTimeout in RunPod policy, uses tool-specific expiry for PUT URLs too.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All pipeline methods produce real configuration dicts and parse real output structures. No placeholder data or TODO markers.

## Verification Results

All 4 plan verifications passed:
1. `PIPELINE_MAP` has 5 entries (rfdiffusion, rfantibody, bindcraft, boltzgen, pxdesign)
2. `JobSpec(tool='pxdesign', ...)` validates successfully
3. `GPUJobSubmission(policy={'executionTimeout': 1800000})` works correctly
4. All 5 pipelines have `generate_config`, `parse_results`, `execution_timeout_ms`, `presigned_url_expiry_seconds`

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | b2f455a | Pipelines module with 5 tool implementations |
| 2 | a77c354 | PXDesign in JobSpec, worker presigned URL + timeout |

## Self-Check: PASSED

All 7 created files verified on disk. Both commit hashes (b2f455a, a77c354) found in git log.
