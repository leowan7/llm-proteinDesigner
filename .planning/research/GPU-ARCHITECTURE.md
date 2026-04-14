# GPU Architecture Decision

## Decision: RunPod GPU Pods (Dedicated Instances)

**Date:** 2026-03-23
**Status:** Approved
**Updated:** 2026-04-14 — switched from serverless to dedicated pods

## Core Architecture

Each design tool gets its own Docker image. Jobs are submitted by creating RunPod GPU pods via the Pod REST API. The backend creates a pod, passes the job payload as environment variables, the container runs the pipeline, POSTs results to the webhook, then exits. The backend terminates the pod on webhook receipt.

## Why Pods Over Serverless

- **Long runtimes**: Protein design jobs run 30 min to several hours. Serverless has timeout/preemption risk.
- **No handler boilerplate**: Containers run any Docker image directly — no `rp_handler.py` or `runpod` SDK needed.
- **No UID mapping issues**: Root access by default.
- **No image size limits**: Configurable container disk (our images are 15-25 GB with baked weights).
- **Explicit lifecycle**: Backend controls pod creation and termination. Pod terminates on webhook, stopping billing immediately.

## Docker Images (one per tool)

| Image | Base CUDA | Python | Key Dependencies | Est. Size |
|-------|----------|--------|-----------------|-----------|
| kendrew/rfdiffusion | 11.8 | 3.9 | PyTorch, SE3-Transformers, ProteinMPNN, ColabFold AF2 | ~15 GB |
| kendrew/bindcraft | 11.8 | 3.10 | JAX, ColabDesign, OpenMM, FreeSASA, AF2 weights | ~25 GB |
| kendrew/rfantibody | 11.8 | 3.9 | PyTorch, AbMPNN, RF2 antibody weights | ~15 GB |
| kendrew/boltzgen | 11.8 | 3.12 | PyTorch, BoltzGen, HuggingFace weights | ~20 GB |
| kendrew/pxdesign | 11.8 | 3.10 | PyTorch, Protenix, ProteinMPNN, AF2 weights | ~25 GB |

## Container Pattern

Each image contains `run_pipeline.py` that:
1. Reads `JOB_PAYLOAD` env var (JSON with job_spec, presigned URLs)
2. Downloads input PDB from R2 via presigned GET
3. Runs the tool-specific pipeline stages
4. Uploads output PDBs + metrics CSV to R2 via presigned PUT
5. POSTs results to `WEBHOOK_URL` (Kendrew backend)
6. Exits (backend terminates the pod)

## Job Flow

```
User clicks Launch Job
    |
Kendrew Backend (Job Dispatcher)
    |-- Creates RunPod GPU pod via POST /v1/pods
    |-- Passes: JOB_PAYLOAD, WEBHOOK_URL, JOB_ID, JOB_TOKEN as env vars
    |-- Pod runs docker/[tool]/run_pipeline.py
    |
RunPod GPU Pod
    |-- Pulls input PDB from R2
    |-- Runs design pipeline (30 min - several hours)
    |-- Uploads results to R2 (PDBs + metrics CSV)
    |-- POSTs completion to Kendrew webhook
    |
Kendrew Backend (Webhook Handler)
    |-- Receives results via POST /webhooks/runpod
    |-- Terminates the pod (stops billing)
    |-- Updates job status to "complete"
    |-- Notifies user via SSE + email
    |
User sees ranked candidates in the results page
```

## Parallelization Strategy

Every tool produces independent designs. Large jobs can be split into parallel pods:

| Tool | Batch Size | Workers for 10k designs | Wall Time |
|------|-----------|------------------------|-----------|
| RFdiffusion | 1,000 designs | 10 | ~30 min |
| BindCraft | 10 trajectories | 50 (for 500 designs) | ~1-2 hrs |
| RFantibody | 1,000 designs | 10 | ~30 min |
| BoltzGen | 100 designs | 100 | ~30-60 min |
| PXDesign | 500 designs | 10 | ~30 min |

## Billing Model

Users are billed for total GPU-seconds across all pods:
- 10 pods x 30 min = 5 GPU-hours billed
- User waited 30 min wall time (selling point)

## Phase 4 Testing Plan

Start with single-pod runs (no parallelization) to validate each pipeline:
1. Build Docker image, test locally with `docker run --gpus all`
2. Push to GHCR (ghcr.io/leowan7/kendrew-[tool]:vN)
3. Set image tag in config.py `runpod_image_[tool]`
4. Submit pilot job via Kendrew, verify full loop (pod creation -> pipeline -> webhook -> termination)
5. Repeat for all 5 tools
6. Add batch splitting + aggregation after all tools validated
