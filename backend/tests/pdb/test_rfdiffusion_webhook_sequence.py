"""The binder sequence must survive into the webhook result payload.

Its own file, not appended to a sibling test module, so the in-flight
rfdiffusion branches do not collide at one file's end on merge. The
loader is the same per-file pattern the sibling pipeline tests use and
memoizes through sys.modules.
"""
from __future__ import annotations

import importlib.util
import inspect
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

_SEQ = "SLEDEIVKRYFEALAKNALALALAALAKAAL"


def _candidate(**over):
    c = {
        "rank": 1,
        "pdb_key": "designs/design_001.pdb",
        "scores": {"ipTM": 0.1, "pLDDT": 58.0, "i_pAE": 25.0},
        "sequence": _SEQ,
    }
    c.update(over)
    return c


def test_entry_carries_the_binder_sequence():
    """The deliverable. Dropping it emptied export.fasta on every job."""
    assert rfd._webhook_candidate_entry(_candidate())["sequence"] == _SEQ


def test_entry_preserves_the_existing_fields():
    entry = rfd._webhook_candidate_entry(_candidate())
    assert entry["rank"] == 1
    assert entry["pdb_key"] == "designs/design_001.pdb"
    assert entry["scores"]["ipTM"] == 0.1


def test_missing_sequence_is_empty_not_a_crash():
    c = _candidate()
    del c["sequence"]
    assert rfd._webhook_candidate_entry(c)["sequence"] == ""


def test_pdb_is_inlined_when_the_local_file_exists(tmp_path):
    import base64
    pdb = tmp_path / "design.pdb"
    pdb.write_bytes(b"ATOM  \n")
    entry = rfd._webhook_candidate_entry(_candidate(local_file=str(pdb)))
    assert base64.b64decode(entry["pdb_content_b64"]) == b"ATOM  \n"


def test_no_pdb_field_when_there_is_no_local_file():
    entry = rfd._webhook_candidate_entry(
        _candidate(local_file="/nonexistent/nope.pdb")
    )
    assert "pdb_content_b64" not in entry


def test_main_actually_uses_the_helper():
    """Pins the CALL SITE, not just the helper.

    A helper that is correct but unused is the failure mode this file
    exists to prevent: the payload would silently go back to being built
    inline without a single test turning red.
    """
    src = inspect.getsource(rfd.main)
    assert "_webhook_candidate_entry" in src
    # ...and does not hand-roll the payload alongside it.
    assert '"pdb_content_b64"' not in src


def test_smoke_tier_candidates_also_carry_the_sequence():
    """The sibling path had the identical omission."""
    src = inspect.getsource(rfd.run_smoke_tier)
    assert '"sequence"' in src
