"""Modal app for PXDesign (self-contained; no cross-module imports).

Deploy:
    modal deploy infrastructure/modal/pxdesign_app.py

See ``bindcraft_app.py`` for the rationale on why every per-tool app file is
self-contained instead of importing shared helpers from a sibling module.

GPU: A100-80GB (DeepSpeed + JAX + AF2 footprint). Max session: 23 hours.
Greenfield on Modal — was not configured on RunPod.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import modal

_TOOL = "pxdesign"
# See bindcraft_app.py for rationale on string-relative paths.
_DOCKERFILE = f"docker/{_TOOL}/Dockerfile.modal"
_RUN_PIPELINE_LOCAL = f"docker/{_TOOL}/run_pipeline.py"
_RUN_PIPELINE_REMOTE = "/opt/run_pipeline.py"
_GPU = "A100-80GB"
_MAX_SESSION_S = 82800
_PYTHON = "python3"

# Raw run artifacts get their OWN Volume — never a weights cache, which exists to
# make cold starts cheap and has no eviction path. Must match RAW_ARCHIVE_PATH in
# docker/pxdesign/run_pipeline.py, which tars its work dir there before teardown.
_RAW_ARCHIVE_PATH = "/tmp/raw_archive.tgz"
_RAW_VOLUME = f"ranomics-{_TOOL}-raw"
_RAW_MOUNT = "/raw"
raw_volume = modal.Volume.from_name(_RAW_VOLUME, create_if_missing=True)


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


def _park_raw_archive(job_id: str) -> dict[str, str]:
    """Move the pipeline's raw work-dir archive onto the raw Volume.

    run_pipeline.py::ship_raw tars its ENTIRE work dir to _RAW_ARCHIVE_PATH in a
    ``finally``, before the rmtree. The curated dict returned below keeps a few
    scores per design; this archive is the only copy of everything else, and
    re-making it costs another A100-80GB session.

    A Volume rather than an inline return or Storage, because all three were
    checked: tools-hub gpu/modal_client.py rejects a non-dict return outright and
    webhooks/modal.py nulls one; a large b64 inside the dict flows into the
    tool_jobs.result JSONB column, where it wedges the UPDATE and the job never
    leaves "running"; and Supabase Storage caps objects at 20 MB with no gzip or
    tar in its MIME allowlist. Naming the object after the job id means nothing
    new has to travel through the DB — the returned keys are top-level, where
    _interpret_pipeline_return ignores them, so no client change is needed.

    Best-effort: this runs after hours of GPU have already been paid for, so a
    failure here is logged and the keys are simply absent. It never raises.
    """
    info: dict[str, str] = {}
    try:
        if not os.path.exists(_RAW_ARCHIVE_PATH):
            print(f"[raw] no archive at {_RAW_ARCHIVE_PATH}; pipeline wrote none",
                  flush=True)
            return info
        # job_id arrives from the caller and is interpolated straight into a
        # path, so keep it to characters that cannot escape the mount.
        safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_")
        if not safe:
            safe = f"unknown_{int(time.time())}"
        size = os.path.getsize(_RAW_ARCHIVE_PATH)
        os.makedirs(_RAW_MOUNT, exist_ok=True)
        dest = os.path.join(_RAW_MOUNT, f"{safe}.tgz")
        shutil.move(_RAW_ARCHIVE_PATH, dest)
        try:
            raw_volume.commit()
        except Exception as exc:  # noqa: BLE001 - a commit race must not lose the run
            print(f"[raw] volume commit failed: {exc}", flush=True)
        info["raw_tgz_volume"] = _RAW_VOLUME
        info["raw_tgz_volume_path"] = dest
        print(f"[raw] parked {size / 1e6:.1f} MB at {dest} (volume {_RAW_VOLUME})",
              flush=True)
    except Exception as exc:  # noqa: BLE001 - capture must never fail the run
        print(f"[raw] failed to park archive (non-fatal): "
              f"{type(exc).__name__}: {exc}", flush=True)
    return info


@app.function(image=image, gpu=_GPU, timeout=_MAX_SESSION_S,
              volumes={_RAW_MOUNT: raw_volume})
def run_tool(payload: dict) -> dict:
    """Run one PXDesign session.

    Subprocess output streams live to Modal logs for debugging.
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

    result = subprocess.run(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        timeout=max(60, _MAX_SESSION_S - 120),
    )

    print(f"[run_tool] subprocess exited: {result.returncode}", flush=True)

    # Park the raw work-dir archive on the Volume, unconditionally — not gated on
    # exit code, on candidates, or on tier. A zero-candidate run that reports a
    # clean success is exactly the one whose tree is worth having.
    raw_info = _park_raw_archive(str(payload.get("job_id", "")))

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

    # raw_tgz_volume / raw_tgz_volume_path ride at the TOP LEVEL, where
    # _interpret_pipeline_return ignores unknown keys — so this needs no client
    # change, and nothing large travels through the DB.
    return {
        "exit_code": result.returncode,
        "stdout_tail": "",
        "stderr_tail": "",
        "provider_job_id": payload.get("job_id", ""),
        "smoke_result": smoke_result,
        "webhook_outcome": webhook_outcome,
        **raw_info,
    }
