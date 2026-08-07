"""BoltzGen metric extraction — which ipTM ends up on a design.

``aggregate_metrics_analyze.csv`` carries BOTH ``iptm`` (complex-wide) and
``design_iptm`` (the binder-to-target interface). ``parse_metrics_csv`` picks
the first key present from an ordered list, and ``design_iptm`` sat FOURTH,
behind ``iptm`` — so the complex-wide value always won and the per-pair value,
which is the one a binder-design user is actually asking about, was never read.

Why that matters beyond neatness, per
``docs/MULTI-CHAIN-TARGETS.md`` "Known limitations":

    ipTM is a max over residues rather than a mean, so on a multi-chain target
    the target's OWN chain-chain interface — ~0.9 for a real crystal dimer —
    dominates almost independently of binder quality.

And ipTM is not just displayed. It is the ranking key at run_pipeline.py:509,
where the sort happens BEFORE ``scored = scored[:num_designs]`` — so it decides
which designs ship at all, not merely their order — and it is the
``IPTM_THRESHOLD`` comparison that labels ``filter_status``.

``parse_metrics_csv`` had no test in either repo. The fixtures below carry both
columns at DIFFERENT values, which is the only way the assertion can tell the
bug from the fix: with equal values, a wrong key order passes.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_PDB_UTILS = os.path.join(_REPO_ROOT, "backend", "pdb_utils")
_RUN_PIPELINE = os.path.join(_REPO_ROOT, "docker", "boltzgen", "run_pipeline.py")


def _load_pipeline():
    if _PDB_UTILS not in sys.path:
        sys.path.insert(0, _PDB_UTILS)
    if "boltzgen_run_pipeline" in sys.modules:
        return sys.modules["boltzgen_run_pipeline"]
    spec = importlib.util.spec_from_file_location(
        "boltzgen_run_pipeline", _RUN_PIPELINE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["boltzgen_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


bg = _load_pipeline()

# Deliberately far apart. 0.93 is what a dimeric target's own interface reads;
# 0.41 is the binder that dimer is carrying.
COMPLEX_IPTM = 0.93
DESIGN_IPTM = 0.41
# Distinct from both, so a test can tell "narrowest mask wins" from "the
# design_iptm reorder happened to be enough".
DESIGN_TO_TARGET_IPTM = 0.27

_HEADER = "file_name,designed_sequence,iptm,design_iptm,complex_plddt,bb_rmsd"


def _csv(tmp_path, header: str, *rows: str) -> str:
    path = tmp_path / "aggregate_metrics_analyze.csv"
    path.write_text("\n".join((header,) + rows) + "\n", encoding="utf-8")
    return str(path)


def _scores(tmp_path, header: str, row: str) -> dict:
    designs = bg.parse_metrics_csv(_csv(tmp_path, header, row))
    assert len(designs) == 1, designs
    return designs[0]["scores"]


def test_design_iptm_wins_over_complex_iptm(tmp_path):
    """THE regression. Before the key reorder this returned 0.93."""
    scores = _scores(
        tmp_path, _HEADER, f"d0.cif,AAAA,{COMPLEX_IPTM},{DESIGN_IPTM},0.88,1.2"
    )
    assert scores["ipTM"] == DESIGN_IPTM


def test_design_to_target_iptm_wins_over_design_iptm(tmp_path):
    """The three upstream ipTM variants differ only in their token-pair mask
    (boltzgen/model/layers/confidence_utils.py::compute_ptms), and we want the
    narrowest one available:

        iptm                   any pair across an asym_id boundary
        design_iptm            the whole design CHAIN vs the target
        design_to_target_iptm  only the DESIGNED TOKENS vs the target

    They coincide for a fully de-novo binder, which is all this wrapper builds
    today — so this test is the only thing standing between us and a silent
    regression the day partial design lands, when `design_iptm` starts
    averaging fixed-scaffold-vs-target pairs into the interface score.

    All three columns are present at three DIFFERENT values, which is the only
    arrangement that can tell the intended order from any other.
    """
    header = (
        "file_name,designed_sequence,iptm,design_iptm,"
        "design_to_target_iptm,complex_plddt,bb_rmsd"
    )
    scores = _scores(
        tmp_path, header,
        f"d0.cif,AAAA,{COMPLEX_IPTM},{DESIGN_IPTM},{DESIGN_TO_TARGET_IPTM},"
        f"0.88,1.2",
    )
    assert scores["ipTM"] == DESIGN_TO_TARGET_IPTM


def test_design_iptm_is_used_when_design_to_target_iptm_is_absent(tmp_path):
    """The wrapper must keep working against the BoltzGen build deployed today,
    whose CSV this repo has never captured as a fixture. Adding a preferred key
    that the deployed container may not emit is only safe if its absence falls
    straight through — so this pins that it does, and it is what makes the
    added key cost nothing rather than score every design 0.0."""
    scores = _scores(
        tmp_path, _HEADER, f"d0.cif,AAAA,{COMPLEX_IPTM},{DESIGN_IPTM},0.88,1.2"
    )
    assert scores["ipTM"] == DESIGN_IPTM


def test_complex_iptm_is_still_used_when_design_iptm_is_absent(tmp_path):
    """The fallback has to survive, or an older BoltzGen build that does not
    emit design_iptm silently scores every design 0.0. This also proves the
    test above is not merely asserting "the first CSV column wins"."""
    header = "file_name,designed_sequence,iptm,complex_plddt,bb_rmsd"
    scores = _scores(tmp_path, header, f"d0.cif,AAAA,{COMPLEX_IPTM},0.88,1.2")
    assert scores["ipTM"] == COMPLEX_IPTM


def test_an_empty_design_iptm_cell_falls_through_to_complex_iptm(tmp_path):
    """The loop guards on ``row[key] not in (None, "")``. Reordering makes that
    guard newly reachable for design_iptm, so an empty cell must fall through
    rather than score the design 0.0."""
    scores = _scores(
        tmp_path, _HEADER, f"d0.cif,AAAA,{COMPLEX_IPTM},,0.88,1.2"
    )
    assert scores["ipTM"] == COMPLEX_IPTM


def test_ranking_follows_the_binder_interface_not_the_complex(tmp_path):
    """The consequence that costs money. Two designs where the complex-wide
    value ranks them one way and the binder interface ranks them the other —
    which is exactly the multi-chain case, since the target's own interface is
    identical in both rows and swamps the signal.

    ``filter_and_rank`` sorts descending on ipTM, and the caller truncates to
    num_designs AFTER sorting, so this decides which designs a user receives.
    """
    path = _csv(
        tmp_path, _HEADER,
        # good binder, same dimer interface
        "good.cif,AAAA,0.93,0.82,0.90,1.1",
        # mediocre binder, marginally higher complex-wide score
        "weak.cif,CCCC,0.94,0.35,0.90,1.1",
    )
    ranked = bg.filter_and_rank(bg.parse_metrics_csv(path))
    assert [d["design_name"] for d in ranked] == ["good", "weak"], (
        "ranked on the complex-wide value: the weaker binder came first "
        "because the target's own chain-chain interface scored higher"
    )
    # And the labels follow, so the user is not told the weak one passed.
    assert ranked[0]["scores"]["filter_status"] == "pass"
    assert ranked[1]["scores"]["filter_status"] == "below threshold"


def test_threshold_labelling_uses_the_binder_interface(tmp_path):
    """A design whose complex-wide ipTM clears IPTM_THRESHOLD on the strength
    of the target's own interface must not be labelled a pass."""
    assert DESIGN_IPTM < bg.IPTM_THRESHOLD < COMPLEX_IPTM, (
        "fixture no longer straddles the threshold, so this proves nothing"
    )
    path = _csv(
        tmp_path, _HEADER,
        f"d0.cif,AAAA,{COMPLEX_IPTM},{DESIGN_IPTM},0.95,1.0",
    )
    ranked = bg.filter_and_rank(bg.parse_metrics_csv(path))
    assert ranked[0]["scores"]["filter_status"] == "below threshold"


def test_the_other_metric_keys_are_untouched(tmp_path):
    """RMSD_KEYS carries its own ordering hazard (native_rmsd_* ship as 0.0 for
    de-novo binders and must stay AFTER the real refolding columns). Pin both
    siblings so a future reorder of one does not quietly reorder the others."""
    header = (
        "file_name,designed_sequence,iptm,design_iptm,complex_plddt,"
        "bb_rmsd,native_rmsd"
    )
    scores = _scores(
        tmp_path, header,
        f"d0.cif,AAAA,{COMPLEX_IPTM},{DESIGN_IPTM},0.88,1.75,0.0",
    )
    assert scores["refolding_rmsd"] == 1.75, "native_rmsd=0.0 must not win"
    # complex_plddt is emitted in [0,1] and rescaled to 0..100.
    assert scores["pLDDT"] == 88.0
