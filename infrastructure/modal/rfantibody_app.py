"""Modal app for RFantibody (self-contained; no cross-module imports).

Deploy:
    modal deploy infrastructure/modal/rfantibody_app.py

See ``bindcraft_app.py`` for the rationale on why every per-tool app file is
self-contained instead of importing shared helpers from a sibling module.

GPU: A100-40GB. Max session: 23 hours.
Greenfield on Modal — was not configured on RunPod.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import modal

_TOOL = "rfantibody"
# See bindcraft_app.py for rationale on string-relative paths.
_DOCKERFILE = f"docker/{_TOOL}/Dockerfile.modal"
_RUN_PIPELINE_LOCAL = f"docker/{_TOOL}/run_pipeline.py"
_RUN_PIPELINE_REMOTE = "/opt/run_pipeline.py"
_GPU = "A100-40GB"
_MAX_SESSION_S = 82800
_PYTHON = "python3"

# Raw run artifacts get their OWN Volume, never a weights or cache volume: those
# exist to make cold starts cheap and have no eviction path, so parking GB-scale
# run output in one bloats the very thing it is for.
_RAW_VOLUME = f"ranomics-{_TOOL}-raw"
_RAW_MOUNT = "/raw"
# Must match _RAW_ARCHIVE_PATH in docker/rfantibody/run_pipeline.py — that script
# runs as a subprocess and cannot mount a Volume, so it tars its work dir to this
# fixed path and this wrapper moves it onto the Volume.
_RAW_ARCHIVE_PATH = "/tmp/raw_archive.tgz"

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
    # Bake the PD-L1 smoke fixture into the image. Must use add_local_file
    # (not a COPY in the Dockerfile) because modal.Image.from_dockerfile uses
    # the Dockerfile's directory as the build context, so repo-root paths
    # aren't visible to COPY. See docs/SMOKE-TEST-SPEC.md.
    .add_local_file(
        "backend/tests/fixtures/target_pdl1.pdb",
        "/opt/smoke_target.pdb",
        copy=True,
    )
    .add_local_file(
        "backend/pdb_utils/pipeline_normalize.py",
        "/opt/pipeline_normalize.py",
        copy=True,
    )
    # Vendored sync from tools-hub/contracts/ — see contracts/__init__.py header.
    .add_local_dir("./contracts", "/opt/contracts", copy=True)
)

app = modal.App(f"ranomics-{_TOOL}-prod")


def _park_raw_archive(job_id: str) -> dict:
    """Move run_pipeline.py's raw work-dir tar onto the raw Volume.

    run_pipeline.py tars its ENTIRE work dir to _RAW_ARCHIVE_PATH in its finally,
    before the rmtree — see archive_raw_outputs() there for why the container must
    not be the thing that decides which fields were worth keeping.

    The tar rides a Volume rather than the return dict or Storage because the
    alternatives are dead ends, not because a Volume is elegant:
      - gpu/modal_client.py rejects a non-dict return outright, and webhooks/
        modal.py nulls a non-dict result.
      - a big inline b64 in the returned dict flows into the tool_jobs.result
        JSONB column; that UPDATE already threw once on oversized inline b64 and
        left jobs stuck "running" (tools-hub shared/jobs.py exists for that).
      - Supabase Storage caps objects at 20 MB and its MIME allowlist has no
        gzip/tar.
    Naming the object after the job id means nothing new travels through the DB.
    The keys returned here are top level, and _interpret_pipeline_return ignores
    unknown top-level keys, so this needs zero client changes.

    Best-effort: never raises. Capture must not fail a run that already produced
    real science.
    """
    info: dict = {}
    try:
        if not os.path.exists(_RAW_ARCHIVE_PATH):
            # Not necessarily an error: a hard timeout or OOM-kill of the
            # subprocess never reaches the finally that writes the tar.
            print(f"[raw] no archive at {_RAW_ARCHIVE_PATH}", flush=True)
            return info

        os.makedirs(_RAW_MOUNT, exist_ok=True)
        dest = os.path.join(_RAW_MOUNT, f"{job_id or 'unknown'}.tgz")
        size = os.path.getsize(_RAW_ARCHIVE_PATH)
        shutil.move(_RAW_ARCHIVE_PATH, dest)
        # Hand the caller the pointer before committing: if the commit races, the
        # location is still worth reporting.
        info["raw_tgz_volume"] = _RAW_VOLUME
        info["raw_tgz_volume_path"] = dest
        try:
            raw_volume.commit()
        except Exception as exc:
            info["raw_error"] = f"volume commit failed: {exc}"
            print(f"[raw] volume commit failed: {exc}", flush=True)
        print(f"[raw] parked {size / 1e6:.1f} MB at {dest} (volume {_RAW_VOLUME})",
              flush=True)
    except Exception as exc:
        info["raw_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[raw] failed to park archive (non-fatal): {exc}", flush=True)
    return info


@app.function(image=image, gpu=_GPU, timeout=_MAX_SESSION_S,
              volumes={_RAW_MOUNT: raw_volume})
def run_tool(payload: dict) -> dict:
    """Run one RFantibody session.

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

    # Collect the complete raw work-dir tar the pipeline left behind. Not gated on
    # returncode: a non-zero exit is exactly when the tree is worth the most.
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

    return {
        "exit_code": result.returncode,
        "stdout_tail": "",
        "stderr_tail": "",
        "provider_job_id": payload.get("job_id", ""),
        "smoke_result": smoke_result,
        "webhook_outcome": webhook_outcome,
        # raw_tgz_volume / raw_tgz_volume_path: top-level pointers to the parked
        # archive. Unknown top-level keys are ignored by the client's return
        # interpreter, so these ride along without any client change.
        **raw_info,
    }
