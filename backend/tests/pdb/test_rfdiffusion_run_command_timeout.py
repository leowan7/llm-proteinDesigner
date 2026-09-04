"""A subprocess timeout must not unwind past the per-design handlers.

Own file, per this directory's pattern, so in-flight rfdiffusion
branches do not collide at one file's end on merge.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_PDB_UTILS = os.path.join(_REPO_ROOT, "backend", "pdb_utils")
_RUN_PIPELINE = os.path.join(
    _REPO_ROOT, "docker", "rfdiffusion", "run_pipeline.py"
)


def _load_pipeline():
    if _PDB_UTILS not in sys.path:
        sys.path.insert(0, _PDB_UTILS)
    if "rfdiffusion_run_pipeline" in sys.modules:
        return sys.modules["rfdiffusion_run_pipeline"]
    spec = importlib.util.spec_from_file_location(
        "rfdiffusion_run_pipeline", _RUN_PIPELINE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rfdiffusion_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


rfd = _load_pipeline()

_SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_timeout_expired_is_not_a_runtime_error():
    """The premise. If this ever changes, the guard below is redundant."""
    assert not issubclass(subprocess.TimeoutExpired, RuntimeError)


def test_a_timeout_surfaces_as_a_runtime_error():
    with pytest.raises(RuntimeError) as caught:
        rfd.run_command(_SLEEP, timeout=1)
    assert "timed out" in str(caught.value).lower()


def test_the_per_design_handler_actually_catches_it():
    """The property that matters, written the way the pipeline writes it.

    Every per-design skip in this file is `except RuntimeError: continue`.
    Before the fix a timeout was a SubprocessError, so it sailed through
    that handler, unwound out of stage_af2_validation, and discarded the
    results list -- every design already scored in the run was lost
    because ONE design's MSA lookup was slow.
    """
    survived = []
    for name in ("design_ok", "design_slow"):
        try:
            if name == "design_slow":
                rfd.run_command(_SLEEP, timeout=1)
            survived.append(name)
        except RuntimeError:
            continue

    assert survived == ["design_ok"], (
        "the slow design must be skipped, not take the run down with it"
    )


def test_the_original_cause_is_still_reachable():
    """Don't let the conversion hide which failure happened."""
    with pytest.raises(RuntimeError) as caught:
        rfd.run_command(_SLEEP, timeout=1)
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


def test_a_nonzero_exit_is_still_a_runtime_error():
    """The pre-existing path must not have changed shape."""
    with pytest.raises(RuntimeError) as caught:
        rfd.run_command([sys.executable, "-c", "raise SystemExit(3)"], timeout=60)
    assert "exit 3" in str(caught.value)
