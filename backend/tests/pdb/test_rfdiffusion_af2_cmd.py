"""The colabfold_batch argv for rfdiffusion's AF2 validation stage.

Lives in its own file rather than appended to test_rfdiffusion_multichain
so the two in-flight rfdiffusion fixes do not both append to one file's
end and collide on merge. The loader below is the same per-file pattern
the sibling pipeline tests use, and it memoizes through sys.modules, so
importing both files in one run loads run_pipeline.py once.
"""
from __future__ import annotations

import importlib.util
import os
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


# The argv is asserted EXACTLY, as an allowlist, rather than by forbidding
# known-bad flags. A subtractive test cannot see an ADDED one, and additions
# are not cheap here: "--templates" reaches ColabFold's mk_template(), which
# shells out to hhsearch -- absent from docker/rfdiffusion/Dockerfile.modal,
# so every run would crash -- and "--host-url" would redirect the MSA lookup
# to an arbitrary third-party server. Both slipped past the earlier
# flag-absence assertions. Changing the argv means changing these lists.
_PRODUCTION_ARGV = [
    "colabfold_batch", "in.fasta", "out",
    "--model-type", "alphafold2_multimer_v3",
    "--num-recycle", "3",
    "--num-models", "1",
    "--rank", "iptm",
]

_FAST_TIER_ARGV = [
    "colabfold_batch", "in.fasta", "out",
    "--model-type", "alphafold2_multimer_v3",
    "--num-recycle", "1",
    "--num-models", "1",
    "--rank", "iptm",
    "--stop-at-score", "85",
    "--recycle-early-stop-tolerance", "0.5",
]


def test_af2_cmd_does_not_force_single_sequence():
    """The target needs an MSA to fold; forcing single-sequence pinned
    every score to a constant. ColabFold's default handles both chains.

    Redundant against the exact-argv tests below, and kept anyway: it is
    the one that NAMES the regression, so its failure message says what
    broke instead of printing two long lists.
    """
    cmd = rfd._build_af2_cmd("in.fasta", "out", "")
    assert "--msa-mode" not in cmd
    assert "single_sequence" not in cmd


def test_af2_cmd_production_tier_argv_is_exactly_the_allowlist():
    assert rfd._build_af2_cmd("in.fasta", "out", "") == _PRODUCTION_ARGV


@pytest.mark.parametrize("tier", ["smoke", "mini_pilot"])
def test_af2_cmd_fast_tiers_argv_is_exactly_the_allowlist(tier):
    """Also guards the index-based mutation: it must hit --num-recycle's
    value and leave every other flag's value alone."""
    assert rfd._build_af2_cmd("in.fasta", "out", tier) == _FAST_TIER_ARGV


def test_stage_af2_validation_builds_its_argv_with_the_helper(
    tmp_path, monkeypatch,
):
    """Pins the PRODUCTION call site to ``_build_af2_cmd``.

    Every test above exercises the helper directly, which leaves the exact
    regression this change exists to prevent unguarded: revert the call
    site to its old inline argv, ``--msa-mode single_sequence`` and all,
    and the helper tests keep passing over a function nothing calls.

    Asserts on the argv ``run_command`` actually receives, so it pins the
    subprocess input and not merely the fact of a call.
    """
    fasta = tmp_path / "design_0.fasta"
    fasta.write_text(">backbone\nGGGGGGGGGG\n>design_0\nWWWWWWWWWW\n")
    out_dir = tmp_path / "af2"

    seen: dict = {}
    real_build = rfd._build_af2_cmd

    def _spy_build(combined_fasta, out, tier):
        seen["built"] = real_build(combined_fasta, out, tier)
        return seen["built"]

    def _spy_run(cmd, **kwargs):
        seen["ran"] = cmd
        return ""

    # /root/.cache/jax is a Modal Volume mount. Redirect it so the test does
    # not try to create a directory at the filesystem root.
    real_path = rfd.Path

    def _path(p):
        q = real_path(p)
        return tmp_path / "jax" if q.as_posix() == "/root/.cache/jax" else q

    monkeypatch.setattr(rfd, "Path", _path)
    monkeypatch.setattr(rfd, "_build_af2_cmd", _spy_build)
    monkeypatch.setattr(rfd, "run_command", _spy_run)
    monkeypatch.setattr(
        rfd, "_extract_target_sequence", lambda pdb, chain: "MTARGETSEQ"
    )

    rfd.stage_af2_validation(
        [str(fasta)], "target.pdb", "A", str(out_dir), tier="",
    )

    assert "built" in seen, "stage_af2_validation did not call _build_af2_cmd"
    assert seen["ran"] == seen["built"]
    assert seen["ran"][0] == "colabfold_batch"
    assert "--msa-mode" not in seen["ran"]
