# Modal Infrastructure

This directory holds the Modal.com GPU compute infrastructure that replaces the RunPod Pod per-job pattern.

## Contents

| File | Purpose |
|---|---|
| `base_image.py` | Shared helpers: tool → Dockerfile map, GPU SKUs, timeout constants, env-var builder. |
| `bindcraft_app.py` | Modal app `ranomics-bindcraft-prod`, GPU A100-80GB. |
| `boltzgen_app.py` | Modal app `ranomics-boltzgen-prod`, GPU A100-40GB. |
| `rfdiffusion_app.py` | Modal app `ranomics-rfdiffusion-prod`, GPU A10G-24GB. |
| `rfantibody_app.py` | Modal app `ranomics-rfantibody-prod`, GPU A100-40GB. |
| `pxdesign_app.py` | Modal app `ranomics-pxdesign-prod`, GPU A100-80GB. |

## One-time setup

```bash
pip install modal
modal token new
```

Save the token ID + secret to the backend `.env.local` (and to Modal's GitHub Actions secrets for CI deploys):

```
MODAL_TOKEN_ID=<id>
MODAL_TOKEN_SECRET=<secret>
MODAL_WORKSPACE=<your-workspace-slug>
MODAL_ENVIRONMENT=main       # or "staging" for pre-prod testing
GPU_PROVIDER=modal           # default; the backend reads this
```

## Deploy

Run from the **repo root** (`llm-proteinDesigner/`) so the `docker/<tool>/run_pipeline.py` relative paths resolve:

```bash
cd llm-proteinDesigner
modal deploy infrastructure/modal/bindcraft_app.py
modal deploy infrastructure/modal/boltzgen_app.py
modal deploy infrastructure/modal/rfdiffusion_app.py
modal deploy infrastructure/modal/rfantibody_app.py
modal deploy infrastructure/modal/pxdesign_app.py
```

First deploy of a tool builds the image (~20–30 min for BindCraft due to conda env + AF2 weight bake-in). Subsequent deploys reuse the cached image layers — deploy is <60s unless the Dockerfile changed.

## Smoke test (end-to-end from backend)

```bash
cd backend
# Ensure GPU_PROVIDER=modal and MODAL_* are set in .env.local
python -c "
import asyncio
from gpu import get_provider, endpoint_for_tool
from gpu.provider import GPUJobSubmission

async def main():
    provider = get_provider()
    submission = GPUJobSubmission(
        endpoint_id=endpoint_for_tool('bindcraft'),
        input_payload={'job_spec': {'tool': 'bindcraft'}, 'job_token': 'smoke'},
        webhook_url='http://localhost:8000/webhooks/runpod',
        policy={'job_id': 'smoke-test', 'tool': 'bindcraft', 'job_tier': 'pilot'},
    )
    fc_id = await provider.submit_job(submission)
    print('FunctionCall id:', fc_id)

asyncio.run(main())
"
```

Watch `modal app logs ranomics-bindcraft-prod` in a second terminal.

## Rollback (break-glass)

If a Modal deploy is bad and RunPod was previously working for this tool:

```bash
# 1. Flip the backend env var in production:
GPU_PROVIDER=runpod_emergency

# 2. Restart the backend workers. No data migration needed — the RunPod
#    provider is preserved unchanged at backend/gpu/runpod.py.
```

Note: `GPU_PROVIDER=runpod` (without `_emergency`) is intentionally rejected by `backend/gpu/__init__.py:get_provider()` — this prevents accidental fallback. You must type `runpod_emergency` explicitly.

## Troubleshooting

- **Image build fails with `wget: error`**: check the Dockerfile.modal — all `wget -q` should have been swapped for `curl -fsSL --retry 5 --retry-delay 10`. See `scratch/modal_spike/bindcraft_spike.py` for the spike that uncovered this.
- **Function call never starts**: verify `MODAL_WORKSPACE` + `MODAL_ENVIRONMENT` match the app's deployed location. Check `modal app list` to confirm the app exists under the expected workspace.
- **Container OOM on BindCraft / PXDesign**: confirm GPU SKU is A100-80GB, not A100-40GB. See `base_image.py:TOOL_GPU`.
- **Progress page shows "stale"**: the container's `run_pipeline.py` posts heartbeats every 60s to `/webhooks/heartbeat`. Check `WEBHOOK_URL` env var is set correctly in the Modal call payload (`backend/gpu/modal.py::ModalProvider.submit_job`).
- **Chunked full-design job doesn't resume**: Phase 6 work. Confirm `SESSION_DEADLINE_UNIX` is being passed through `ModalProvider.submit_job` and that each tool's `run_pipeline.py` polls it (see `docker/<tool>/run_pipeline.py` — the `--resume-from` flag + deadline polling are added in Phase 6).

## Reference

- Migration plan: `.claude/plans/i-have-been-building-typed-whistle.md`
- Spike that validated BindCraft on Modal: `scratch/modal_spike/bindcraft_spike.py`
