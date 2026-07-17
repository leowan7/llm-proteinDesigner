"""Modal app for BindCraft (self-contained; no cross-module imports).

Deploy:
    modal deploy infrastructure/modal/bindcraft_app.py

Runtime: the backend's ``ModalProvider.submit_job`` resolves this function
via ``modal.Function.from_name("ranomics-bindcraft-prod", "run_tool")`` and
invokes ``.spawn.aio(payload)``. The function body translates the payload
into env vars (see ``_build_run_env`` below) and runs the production
``run_pipeline.py`` subprocess, which POSTs candidates + scores to the
webhook embedded in the payload.

Self-contained rationale:
    Modal deploys only the single file you pass to ``modal deploy`` plus any
    Python modules it can auto-detect. Earlier versions imported helpers
    from ``infrastructure.modal.base_image``, which caused
    ``ModuleNotFoundError: No module named 'infrastructure'`` at runtime
    because the sibling package wasn't bundled. The safest fix is to keep
    each per-tool app fully self-contained. Code duplication across the
    5 app files is the tradeoff we accept for guaranteed portability.

GPU: A100-80GB (BindCraft's AF2 multimer + ColabDesign footprint).
Max session: 23 hours (full-design chunking boundary).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import modal

_TOOL = "bindcraft"
# Paths are strings relative to the working directory at ``modal deploy`` time
# (which the user invokes from the repo root). Modal resolves them lazily
# during image build on its servers, so the path never has to exist inside
# the deployed container — avoids an IndexError on Path(__file__).parents
# when the module is imported inside Modal's runtime sandbox.
_DOCKERFILE = f"docker/{_TOOL}/Dockerfile.modal"
_RUN_PIPELINE_LOCAL = f"docker/{_TOOL}/run_pipeline.py"
_RUN_PIPELINE_REMOTE = "/opt/run_pipeline.py"
_GPU = "A100-80GB"
_MAX_SESSION_S = 82800  # 23 hours — Modal's @app.function timeout ceiling.
_PYTHON = "/miniforge3/envs/BindCraft/bin/python"  # BindCraft runs inside conda env.

# Raw run artifacts get their OWN Volume, never a weights cache: a weights volume
# exists to make cold starts cheap and has no eviction path, so parking GB-scale
# run output in it bloats the very thing it is for and leaves no way to reap raw
# without touching weights.
_RAW_VOLUME = f"ranomics-{_TOOL}-raw"
_RAW_MOUNT = "/raw"
# Fixed handoff path written by docker/bindcraft/run_pipeline.py::archive_work_dir.
# The pipeline runs as a subprocess and cannot mount the Volume itself; this
# wrapper can, so the split is: subprocess tars to /tmp, wrapper moves to /raw.
_RAW_ARCHIVE_PATH = "/tmp/raw_archive.tgz"

raw_volume = modal.Volume.from_name(_RAW_VOLUME, create_if_missing=True)


def _build_run_env(payload: dict) -> dict[str, str]:
    """Translate a Modal call payload into env vars for ``run_pipeline.py``.

    Mirrors the env-var contract that RunPod Pods use so ``run_pipeline.py``
    stays provider-agnostic. Adds the Phase 2/6 tier + session vars.

    The ``tier`` and ``input_pdb_url`` fields support smoke/mini_pilot
    modes: when ``tier`` is ``"smoke"`` or ``"mini_pilot"``, run_pipeline.py
    bypasses the webhook/upload path and instead writes results to
    ``/tmp/smoke_results.json`` which the wrapper reads and returns inline.
    See ``docs/SMOKE-TEST-SPEC.md``.
    """
    env: dict[str, str] = {
        # RunPod-parity vars expected by the existing run_pipeline.py.
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
        # Phase 2 tier.
        "JOB_TIER": str(payload.get("job_tier", "pilot")),
        # Phase 6 chunking (absent / 0 on pilots).
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
    """Merge ``_build_run_env`` into ``os.environ`` (preserves PATH/CUDA_HOME)."""
    merged = dict(os.environ)
    merged.update(_build_run_env(payload))
    return merged


image = (
    modal.Image.from_dockerfile(_DOCKERFILE, add_python=None)
    .add_local_file(_RUN_PIPELINE_LOCAL, _RUN_PIPELINE_REMOTE, copy=True)
    # Vendored sync from tools-hub/contracts/ — see contracts/__init__.py header.
    .add_local_dir("./contracts", "/opt/contracts", copy=True)
)

app = modal.App(f"ranomics-{_TOOL}-prod")


def _raw_archive_name(payload: dict) -> str:
    """Deterministic Volume filename for this session's raw archive.

    The job id is derivable by the caller with no round trip, so nothing new has
    to travel through the DB to locate the tar. The session suffix is load-bearing:
    run_tool is documented as "one session (pilot or one CHUNK of a full-design
    campaign)" and every chunk of a campaign shares one job id, so a bare
    <job_id>.tgz would have each chunk silently overwrite its predecessor's raw —
    the same class of quiet loss this capture exists to prevent. session_index is
    already in the payload (see _build_run_env), so <job_id>_s<N>.tgz stays fully
    derivable.
    """
    job_id = str(payload.get("job_id", "") or "").strip()
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in job_id)
    if not safe:
        # No job id to key on; fall back to a unique name rather than clobber.
        safe = f"unknown_{int(time.time())}"
    try:
        session = int(payload.get("session_index", 0) or 0)
    except (TypeError, ValueError):
        session = 0
    return f"{safe}.tgz" if session == 0 else f"{safe}_s{session}.tgz"


def _ship_raw_archive(payload: dict) -> dict:
    """Move the pipeline's raw work-dir archive onto the raw Volume.

    Returns top-level keys to merge into run_tool's return dict. These are IGNORED
    by tools-hub's _interpret_pipeline_return, so this needs zero client changes.

    The archive goes on a Volume rather than inline in the return dict on purpose:
    gpu/modal_client.py rejects a non-dict return, webhooks/modal.py nulls one, and
    a big b64 inside the dict flows into the tool_jobs.result JSONB column, whose
    UPDATE then throws and strands the job in "running" (shared/jobs.py exists
    because inline b64 already broke exactly that). Supabase Storage is not an
    option either: 20 MB object cap and no gzip/tar in its MIME allowlist.

    Never raises: a failed capture must not fail a run that otherwise succeeded.
    """
    out: dict = {}
    try:
        if not os.path.isfile(_RAW_ARCHIVE_PATH):
            # Pipeline crashed before its finally, or was SIGKILLed on timeout.
            print(f"[raw] no archive at {_RAW_ARCHIVE_PATH}; nothing to ship", flush=True)
            return out
        os.makedirs(_RAW_MOUNT, exist_ok=True)
        dest = os.path.join(_RAW_MOUNT, _raw_archive_name(payload))
        size = os.path.getsize(_RAW_ARCHIVE_PATH)
        shutil.move(_RAW_ARCHIVE_PATH, dest)
        try:
            raw_volume.commit()
        except Exception as exc:  # noqa: BLE001 — a commit race must not lose the run
            out["raw_tgz_error"] = f"volume commit failed: {exc}"
            print(f"[raw] volume commit failed (non-fatal): {exc}", flush=True)
        out["raw_tgz_volume"] = _RAW_VOLUME
        out["raw_tgz_volume_path"] = dest
        out["raw_tgz_bytes"] = size
        print(f"[raw] parked {size / 1e6:.1f} MB at {dest} (volume {_RAW_VOLUME})", flush=True)
    except Exception as exc:  # noqa: BLE001 — capture is best-effort by design
        out["raw_tgz_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[raw] capture failed (non-fatal): {type(exc).__name__}: {exc}", flush=True)
    return out


@app.function(image=image, gpu=_GPU, timeout=_MAX_SESSION_S,
              volumes={_RAW_MOUNT: raw_volume})
def run_tool(payload: dict) -> dict:
    """Run one BindCraft session (pilot or one chunk of a full-design campaign).

    Subprocess stdout/stderr stream to Modal's function logs directly so
    failures are visible in ``modal app logs ranomics-bindcraft-prod``
    without needing to fetch the FunctionCall return value out-of-band.
    """
    import sys

    env = _merged_environment(payload)
    cmd = [_PYTHON, "-u", _RUN_PIPELINE_REMOTE]

    print(f"[run_tool] spawning: {' '.join(cmd)}", flush=True)
    print(f"[run_tool] JOB_ID={env.get('JOB_ID')} TIER={env.get('JOB_TIER')} "
          f"WEBHOOK={env.get('WEBHOOK_URL')}", flush=True)

    # Warm containers are reused: a leftover raw archive from a prior job would be parked
    # under THIS job's id. Clear it so we only ever park a tar this run actually wrote.
    try:
        os.remove(_RAW_ARCHIVE_PATH)
    except OSError:
        pass

    # Stream subprocess output live (no capture_output). Modal captures stdout
    # and it shows up in ``modal app logs`` within seconds — critical for
    # debugging early pipeline failures.
    result = subprocess.run(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        # Safety margin under Modal's hard timeout so we can emit a summary.
        timeout=max(60, _MAX_SESSION_S - 120),
    )

    print(f"[run_tool] subprocess exited: {result.returncode}", flush=True)

    # Ship the COMPLETE raw work-dir tree home, unconditionally — not gated on
    # exit code, candidates, or uploads. A zero-candidate run ships nothing today
    # and is precisely the run whose tree is needed.
    raw_info = _ship_raw_archive(payload)

    # Smoke/mini_pilot tier: run_pipeline.py writes results to
    # /tmp/smoke_results.json instead of POSTing to a webhook. Read it back
    # so the Modal function return value carries the candidates inline.
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

    return {
        "exit_code": result.returncode,
        # stdout/stderr now stream live to Modal logs; keep empty tails in the
        # return dict so the webhook handler's shape stays stable.
        "stdout_tail": "",
        "stderr_tail": "",
        "provider_job_id": payload.get("job_id", ""),
        "smoke_result": smoke_result,
        **raw_info,
    }
