"""Modal app for RFdiffusion (self-contained; no cross-module imports).

Deploy:
    modal deploy infrastructure/modal/rfdiffusion_app.py

See ``bindcraft_app.py`` for the rationale on why every per-tool app file is
self-contained instead of importing shared helpers from a sibling module.

GPU: A10G (24GB). Max session: 23 hours.
Pilot runs typically finish in ~15 min (num_designs=10).
"""

from __future__ import annotations

import json
import os
import subprocess

import modal

_TOOL = "rfdiffusion"
# See bindcraft_app.py for rationale on string-relative paths.
_DOCKERFILE = f"docker/{_TOOL}/Dockerfile.modal"
_RUN_PIPELINE_LOCAL = f"docker/{_TOOL}/run_pipeline.py"
_RUN_PIPELINE_REMOTE = "/opt/run_pipeline.py"
_GPU = "A100-40GB"
_MAX_SESSION_S = 82800
_PYTHON = "python3"


def _build_run_env(payload: dict) -> dict[str, str]:
    # ``tier`` and ``input_pdb_url`` support smoke/mini_pilot modes —
    # see docs/SMOKE-TEST-SPEC.md.
    env: dict[str, str] = {
        "JOB_PAYLOAD": json.dumps({
            # Required by the ToolPayload contract validated in run_pipeline.py.
            "job_id": str(payload.get("job_id", "")),
            "job_spec": payload.get("job_spec", {}),
            "input_presigned_url": payload.get("input_presigned_url", ""),
            "job_token": payload.get("job_token", ""),
            "upload_urls_endpoint": payload.get("upload_urls_endpoint", ""),
            "tier": payload.get("tier", ""),
            "input_pdb_url": payload.get("input_pdb_url", ""),
        }),
        "WEBHOOK_URL": str(payload.get("webhook_url", "")),
        "JOB_ID": str(payload.get("job_id", "")),
        "JOB_TOKEN": str(payload.get("job_token", "")),
        "JOB_TIER": str(payload.get("job_tier", "pilot")),
        "SESSION_INDEX": str(payload.get("session_index", 0)),
        "TOTAL_BUDGET_HOURS": str(payload.get("total_budget_hours", 4) or 4),
        "RESUME_STATE_PATH": str(payload.get("resume_state_path", "") or ""),
        "PROVIDER_JOB_ID": str(payload.get("provider_job_id", "")),
    }
    session_deadline = payload.get("session_deadline_unix")
    if session_deadline:
        env["SESSION_DEADLINE_UNIX"] = str(int(session_deadline))
    return env


def _merged_environment(payload: dict) -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(_build_run_env(payload))
    return merged


image = (
    modal.Image.from_dockerfile(_DOCKERFILE, add_python=None)
    .add_local_file(_RUN_PIPELINE_LOCAL, _RUN_PIPELINE_REMOTE, copy=True)
    .add_local_file(
        "backend/pdb_utils/pipeline_normalize.py",
        "/opt/pipeline_normalize.py",
        copy=True,
    )
    # Vendored sync from tools-hub/contracts/ — see contracts/__init__.py header.
    .add_local_dir("./contracts", "/opt/contracts", copy=True)
)

app = modal.App(f"ranomics-{_TOOL}-prod")

# Persistent XLA/JAX compilation cache. The first mini_pilot run populates
# ~/.cache/jax with compiled HLO for the AF2 multimer_v3 model (~10-15 min
# cold JIT); subsequent runs reuse the cache and complete in ~5-8 GPU-min.
# See docs/blocker-rfdiffusion.md for root-cause analysis.
xla_cache_volume = modal.Volume.from_name(
    "kendrew-rfdiffusion-xla-cache",
    create_if_missing=True,
)


@app.function(
    image=image,
    gpu=_GPU,
    timeout=_MAX_SESSION_S,
    volumes={"/root/.cache/jax": xla_cache_volume},
    # Inject WEBHOOK_HMAC_SECRET so run_pipeline.py:post_webhook can sign
    # completion notifications. Without this, the backend's
    # validate_webhook_signature returns 401 and the completion never lands
    # (discovered live during 2026-06-03 Phase 11 SC 6 close-out). The
    # Modal Secret 'ranomics-webhook' must hold the same value as Railway's
    # WEBHOOK_HMAC_SECRET env var (PROVISIONING.md "Locally-generated
    # secrets" section).
    secrets=[modal.Secret.from_name("ranomics-webhook")],
)
def run_tool(payload: dict) -> dict:
    """Run one RFdiffusion session.

    Subprocess output streams live to Modal logs for debugging.
    """
    import sys

    env = _merged_environment(payload)
    cmd = [_PYTHON, "-u", _RUN_PIPELINE_REMOTE]

    print(f"[run_tool] spawning: {' '.join(cmd)}", flush=True)
    print(f"[run_tool] JOB_ID={env.get('JOB_ID')} TIER={env.get('JOB_TIER')} "
          f"WEBHOOK={env.get('WEBHOOK_URL')}", flush=True)

    result = subprocess.run(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        timeout=max(60, _MAX_SESSION_S - 120),
    )

    print(f"[run_tool] subprocess exited: {result.returncode}", flush=True)

    # Persist any new XLA cache entries produced by this run so the next
    # cold container can reuse them.
    try:
        xla_cache_volume.commit()
        print("[run_tool] xla cache volume committed", flush=True)
    except Exception as exc:
        print(f"[run_tool] xla cache commit failed (non-fatal): {exc}", flush=True)

    # Smoke/mini_pilot tier: read inline results from /tmp/smoke_results.json.
    # See docs/SMOKE-TEST-SPEC.md.
    smoke_result: dict | None = None
    try:
        with open("/tmp/smoke_results.json") as fh:
            smoke_result = json.load(fh)
        print(f"[run_tool] loaded smoke_results.json: status={smoke_result.get('status')}",
              flush=True)
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[run_tool] failed to read smoke_results.json: {exc}", flush=True)

    # Webhook tier (pilot, full design): post_webhook writes a delivery
    # outcome file the wrapper surfaces to tools-hub. If smoke_result is
    # None and webhook_outcome reports a failure, tools-hub fails the job
    # with the detail rather than waiting on a webhook that already failed.
    webhook_outcome: dict | None = None
    try:
        with open("/tmp/webhook_outcome.json") as fh:
            webhook_outcome = json.load(fh)
        print(f"[run_tool] webhook_outcome: {webhook_outcome}", flush=True)
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[run_tool] failed to read webhook_outcome.json: {exc}", flush=True)

    return {
        "exit_code": result.returncode,
        "stdout_tail": "",
        "stderr_tail": "",
        "provider_job_id": payload.get("job_id", ""),
        "smoke_result": smoke_result,
        "webhook_outcome": webhook_outcome,
    }
