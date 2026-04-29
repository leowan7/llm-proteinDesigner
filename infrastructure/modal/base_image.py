"""Shared helpers for Modal per-tool apps.

Each ``infrastructure/modal/<tool>_app.py`` defines one ``@app.function`` that
runs the tool's production ``run_pipeline.py`` as a subprocess with env vars
populated from the Modal call's payload. This module centralizes:

- ``build_run_env(payload)`` — translate the Modal call payload into the env
  vars the container script expects (JOB_PAYLOAD, WEBHOOK_URL, JOB_ID,
  JOB_TOKEN, JOB_TIER, SESSION_INDEX, SESSION_DEADLINE_UNIX, RESUME_STATE_PATH).
- ``TOOL_DOCKERFILES`` — map of tool → relative Dockerfile.modal path.
- Constants for common Modal function decorator args so they stay in sync
  with the migration plan's per-tool table.

Design intent: keep per-tool app files minimal (< 40 lines each) so the
pattern is easy to replicate and the GPU/timeout per tool is obvious at a glance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Tool → Dockerfile.modal mapping
# ---------------------------------------------------------------------------
#
# Paths are relative to the repo root. The modal CLI should be invoked from
# ``llm-proteinDesigner/`` so these paths resolve correctly.

_REPO_ROOT = Path(__file__).resolve().parents[2]

TOOL_DOCKERFILES: dict[str, Path] = {
    "bindcraft": _REPO_ROOT / "docker" / "bindcraft" / "Dockerfile.modal",
    "boltzgen": _REPO_ROOT / "docker" / "boltzgen" / "Dockerfile.modal",
    "rfdiffusion": _REPO_ROOT / "docker" / "rfdiffusion" / "Dockerfile.modal",
    "rfantibody": _REPO_ROOT / "docker" / "rfantibody" / "Dockerfile.modal",
    "pxdesign": _REPO_ROOT / "docker" / "pxdesign" / "Dockerfile.modal",
}


# ---------------------------------------------------------------------------
# Tool → Python interpreter path in image
# ---------------------------------------------------------------------------
#
# BindCraft runs inside a conda env (miniforge/envs/BindCraft). The other
# tools use the system python3. RFantibody activates via uv-managed venv on
# $PATH already, so python3 resolves correctly there.

TOOL_PYTHON: dict[str, str] = {
    "bindcraft": "/miniforge3/envs/BindCraft/bin/python",
    "boltzgen": "python3",
    "rfdiffusion": "python3",
    "rfantibody": "python3",
    "pxdesign": "python3",
}


# ---------------------------------------------------------------------------
# Tool → run_pipeline.py path in image (always /opt/run_pipeline.py by convention)
# ---------------------------------------------------------------------------

RUN_PIPELINE_PATH = "/opt/run_pipeline.py"


# ---------------------------------------------------------------------------
# Phase 3 per-tool GPU + timeout defaults — mirrors the migration plan table.
# Each per-tool app file imports and uses these so the pattern stays consistent.
# ---------------------------------------------------------------------------

TOOL_GPU: dict[str, str] = {
    "bindcraft": "A100-80GB",
    "boltzgen": "A100-40GB",
    "rfdiffusion": "A10G",           # Modal's SKU name for the 24GB A10G
    "rfantibody": "A100-40GB",
    "pxdesign": "A100-80GB",
}

# Full-design max session (23 hr = 82800s) — Modal's @app.function timeout.
# Pilot jobs finish far below this; full-design jobs chunk at 23hr boundaries.
TOOL_MAX_SESSION_S: dict[str, int] = {
    "bindcraft": 82800,
    "boltzgen": 82800,
    "rfdiffusion": 82800,
    "rfantibody": 82800,
    "pxdesign": 82800,
}


def build_run_env(payload: dict) -> dict:
    """Translate a Modal call payload dict into env vars for run_pipeline.py.

    Mirrors the env-var contract that RunPod Pods use today, so
    ``run_pipeline.py`` is fully provider-agnostic — it sees the same JOB_PAYLOAD,
    WEBHOOK_URL, JOB_ID, JOB_TOKEN vars regardless of whether it's running on
    RunPod or Modal. Adds Phase 2/Phase 6 tier + session vars.

    Args:
        payload: The dict the Modal function was called with (built by
            ``backend/gpu/modal.py::ModalProvider.submit_job``).

    Returns:
        Dict of ``{env_var_name: str}`` ready to merge into ``os.environ`` for
        a subprocess call. All values are stringified.
    """
    env: dict[str, str] = {
        # ------ RunPod-parity env vars (existing run_pipeline.py expects these) ------
        # JOB_PAYLOAD is the FULL payload JSON — kept for scripts that parse it directly.
        "JOB_PAYLOAD": json.dumps({
            "job_spec": payload.get("job_spec", {}),
            "input_presigned_url": payload.get("input_presigned_url", ""),
            "job_token": payload.get("job_token", ""),
            "upload_urls_endpoint": payload.get("upload_urls_endpoint", ""),
        }),
        "WEBHOOK_URL": str(payload.get("webhook_url", "")),
        "JOB_ID": str(payload.get("job_id", "")),
        "JOB_TOKEN": str(payload.get("job_token", "")),

        # ------ Phase 2 tier vars ------
        "JOB_TIER": str(payload.get("job_tier", "pilot")),

        # ------ Phase 6 chunking vars (opt-in; absent / 0 on pilots) ------
        "SESSION_INDEX": str(payload.get("session_index", 0)),
        "TOTAL_BUDGET_HOURS": str(payload.get("total_budget_hours", 4) or 4),
        "RESUME_STATE_PATH": str(payload.get("resume_state_path", "") or ""),
    }

    session_deadline = payload.get("session_deadline_unix")
    if session_deadline:
        env["SESSION_DEADLINE_UNIX"] = str(int(session_deadline))

    # Modal-analog of RunPod's pod ID — container scripts POSTing the webhook
    # include it as "pod_id" for compatibility with the existing webhook parser.
    env["PROVIDER_JOB_ID"] = str(payload.get("provider_job_id", ""))

    return env


def merged_environment(payload: dict) -> dict:
    """Merge ``build_run_env(payload)`` into a copy of ``os.environ``.

    Use this when building the ``env=`` kwarg for ``subprocess.run`` — passing
    a fresh dict would drop PATH, LD_LIBRARY_PATH, CUDA_HOME, etc. which the
    container image relies on.

    Args:
        payload: The dict the Modal function was called with.

    Returns:
        Full environ dict (container-provided + job-specific vars).
    """
    merged = dict(os.environ)
    merged.update(build_run_env(payload))
    return merged
