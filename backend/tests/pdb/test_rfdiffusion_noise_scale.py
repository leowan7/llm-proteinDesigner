"""Binder design must run RFdiffusion at zero denoiser noise.

Own file so the in-flight rfdiffusion branches do not collide at one
file's end on merge. Loader is the sibling pipeline tests' per-file
pattern; it memoizes through sys.modules.
"""
from __future__ import annotations

import importlib.util
import os
import sys

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

_PDB = """\
ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  MET A   1      11.639   5.161  -5.570  1.00 20.00           C
ATOM      3  C   MET A   1      12.985   4.673  -6.055  1.00 20.00           C
ATOM      4  CA  GLU A   2      15.000   5.000  -5.000  1.00 20.00           C
ATOM      5  CA  LYS A   3      18.000   5.000  -5.000  1.00 20.00           C
END
"""


def _args(pdb_path, **params):
    spec = {"target_chain": "A", "hotspot_residues": [], "parameters": params}
    return rfd.build_hydra_args(spec, str(pdb_path))


def _pdb_file(tmp_path):
    p = tmp_path / "target.pdb"
    p.write_text(_PDB)
    return p


def _value(args, key):
    for a in args:
        if a.startswith(key + "="):
            return a.split("=", 1)[1]
    return None


def test_noise_scales_default_to_zero(tmp_path):
    """The authors' binder recipe. Absent entirely before this change."""
    args = _args(_pdb_file(tmp_path))
    assert _value(args, "denoiser.noise_scale_ca") == "0.0"
    assert _value(args, "denoiser.noise_scale_frame") == "0.0"


def test_both_scales_are_set_not_just_one(tmp_path):
    """Setting only one leaves the other at RFdiffusion's default of 1.0."""
    args = _args(_pdb_file(tmp_path))
    assert sum(a.startswith("denoiser.noise_scale") for a in args) == 2


def test_noise_scale_is_overridable_for_the_ab(tmp_path):
    """noise_scale=1.0 must reproduce the old behaviour exactly."""
    args = _args(_pdb_file(tmp_path), noise_scale=1.0)
    assert _value(args, "denoiser.noise_scale_ca") == "1.0"
    assert _value(args, "denoiser.noise_scale_frame") == "1.0"


def test_the_binder_checkpoint_is_still_selected(tmp_path):
    """Guards against the noise args displacing the checkpoint override."""
    args = _args(_pdb_file(tmp_path))
    assert _value(args, "inference.ckpt_override_path").endswith(
        "Complex_base_ckpt.pt"
    )
