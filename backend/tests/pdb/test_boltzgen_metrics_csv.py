"""BoltzGen metric extraction — which ipTM ends up on a design.

``aggregate_metrics_analyze.csv`` carries BOTH ``iptm`` (complex-wide) and
``design_iptm`` (the binder-to-target interface). ``parse_metrics_csv`` picks
the first key present from an ordered list, and ``design_iptm`` sat FOURTH,
behind ``iptm`` — so the complex-wide value always won and the per-pair value,
which is the one a binder-design user is actually asking about, was never read.

Why that matters beyond neatness: bare ``iptm`` is an interface-pTM averaged
over EVERY chain pair, so on a multi-chain target the target's own chain-chain
interface is folded in almost independently of binder quality. Measured across
the 29 Fel d 1 cofolds, that A:B pair spans 0.185-0.930 (mean 0.555).

``docs/MULTI-CHAIN-TARGETS.md`` "Known limitations" states the reduction as "a
max over residues" and the dimer interface as "~0.9". Neither survives, and
the page is corrected as of this change: five pipeline files in this repo say
averaged-over-every-pair (bindcraft, rfdiffusion, pxdesign, rfantibody and this
one), and 0.9 is the top of a 0.185-0.930 range rather than its value. The
conclusion holds under either reduction.

And ipTM is not just displayed. It is the ranking key on both paths, and on the
smoke/mini_pilot path it decides which designs SHIP: ``run_smoke_tier`` serves
both tiers, sorts, then truncates (``scored = scored[:num_designs]``).
``filter_and_rank`` sorts without truncating, so there it sets order only.

It NO LONGER labels ``filter_status``. It used to, against IPTM_THRESHOLD=0.70,
and that gate could not fire on any run ever — 0.70 is the Boltz-2 COFOLD bar
and this pipeline never cofolds. The second half of this file covers that, and
the pLDDT column read that failed the same way for the same reason.

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
    # Labels are deliberately NOT asserted here any more: they no longer depend
    # on ipTM at all (see test_iptm_does_not_decide_the_label). Both rows carry
    # the same pLDDT and RMSD, so both pass, and asserting that here would only
    # restate the fixture. Ranking is what this test is for.


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


# ===========================================================================
# On the audited protocols the gate could not fire — two legs read against
# bars that describe something else
# ===========================================================================
#
# Symptom: 6 production tools-hub runs, 65 candidates, every one labelled
# "below threshold", which makes the results page render "All N designs fell
# below quality thresholds ... should not be advanced to validation". Same on
# the 460-design self-hosted campaign. Not a bad-luck run: two of the three
# legs had NO OVERLAP with their own thresholds.
#
# Measured on the 100-design 2EJN engine audit
# (boltzgen-workspace/feld1/results/engine_audit/n100):
#
#   leg    column read (before)          range          could reach bar?
#   ----   ---------------------------   ------------   ----------------
#   pLDDT  complex_plddt                 54.5 - 66.1    no  (bar 80)
#   ipTM   design_to_target_iptm         0.084 - 0.583  no  (bar 0.70)
#   RMSD   designfolding-bb_rmsd         0.35 - 17.08   yes (28/100)
#
# pLDDT was a wrong-column read and is fixed by reordering PLDDT_KEYS: the
# refold's own pLDDT spans 44.3-87.7 and clears 80 for 11/100.
#
# NOT "for any design ever". On peptide-anything bare complex_plddt spans
# 68.3-84.9 and 2 of 36 clear 80 — and that is precisely the protocol where
# BoltzGen emits no designfolding column to prefer, so the fallback is live
# there. Scope these statements to the runs behind them.
#
# ipTM is not fixable by reordering, because the quantity the 0.70 bar means
# is not in the audited CSV at all — that refold folds the design ALONE, so
# all seven of its numeric ipTM columns are 0.0 and its min_interaction_pae is
# the 100000.25 "no interaction" sentinel. Re-scoring the same campaign's
# designs on a real Boltz-2 cofold gave 0.166-0.806 on `binder_to_target` (the
# per-chain-pair column) where this run's design_to_target_iptm gave <=0.583.
# So the leg is removed rather than re-columned.
#
# QUOTE THE RIGHT COLUMN in anything derived from here. The familiar "460
# designs, max 0.650" and the cofold's "0.263-0.852" are both complex-wide
# all-chain-pair numbers on a homodimer target — contaminated by the target's
# own interface, which is the same class of error this module tests for.


def test_plddt_prefers_the_refold_not_the_whole_complex(tmp_path):
    """THE pLDDT regression. Bare ``complex_plddt`` is the whole conditioned
    complex, so the supplied target dominates it — on the audited run it
    spans 54.5-66.1 and reaches PLDDT_THRESHOLD=80 for none of the 100.

    The refold contains the design and nothing else, so ITS complex pLDDT is
    the binder's pLDDT, which is the quantity the 80 bar actually means.

    The two columns carry DIFFERENT values here, which is the only arrangement
    that can tell the fix from the bug.
    """
    header = (
        "file_name,designed_sequence,design_iptm,"
        "designfolding-complex_plddt,complex_plddt,designfolding-bb_rmsd"
    )
    scores = _scores(tmp_path, header, "d0.cif,AAAA,0.41,0.86,0.61,1.2")
    assert scores["pLDDT"] == 86.0, (
        "read the target-dominated complex_plddt (61.0), the quantity the 80 "
        "bar was not calibrated on"
    )


def test_plddt_falls_through_when_the_refold_column_is_absent(tmp_path):
    """Same back-compat argument as the ipTM reorder: adding a preferred key
    the deployed BoltzGen build may not emit is only safe if its absence falls
    straight through instead of scoring every design 0.0."""
    scores = _scores(
        tmp_path, _HEADER, f"d0.cif,AAAA,{COMPLEX_IPTM},{DESIGN_IPTM},0.88,1.2"
    )
    assert scores["pLDDT"] == 88.0


def test_plddt_empty_refold_cell_falls_through(tmp_path):
    """BoltzGen really does ship empty cells in the designfolding family — on
    the audited run ``designfolding-bb_rmsd_target`` is empty on all 100 rows.
    An empty preferred cell must not score the design 0.0."""
    header = (
        "file_name,designed_sequence,design_iptm,"
        "designfolding-complex_plddt,complex_plddt,designfolding-bb_rmsd"
    )
    scores = _scores(tmp_path, header, "d0.cif,AAAA,0.41,,0.61,1.2")
    assert scores["pLDDT"] == 61.0


def test_iptm_does_not_decide_the_label(tmp_path):
    """A design that is well folded and self-consistent must be labelled a pass
    even though its in-run ipTM is nowhere near 0.70 — 0.70 is a bar for a
    measurement this pipeline does not compute, whatever value the in-run
    number happens to take.

    0.583 is the best `design_to_target_iptm` in the 100 audited designs. Do
    NOT pair that with "0.650 in 460": the 460 figure is the bare all-chain-
    pair `iptm`, a different column (0.450-0.649 on this same audited run).
    """
    header = (
        "file_name,designed_sequence,design_to_target_iptm,"
        "designfolding-complex_plddt,designfolding-bb_rmsd"
    )
    path = _csv(tmp_path, header, "d0.cif,AAAA,0.583,0.86,1.2")
    ranked = bg.filter_and_rank(bg.parse_metrics_csv(path))
    assert ranked[0]["scores"]["filter_status"] == "pass", (
        "gated on an in-run ipTM against a Boltz-2 cofold bar, which is a "
        "bar for a measurement this run does not perform"
    )


def test_a_whole_realistic_run_is_not_uniformly_below_threshold(tmp_path):
    """The production symptom itself, at the scale it was reported: values are
    drawn from the ranges the audited run actually produced, and the run must
    NOT come back 0/N passing."""
    header = (
        "file_name,designed_sequence,design_to_target_iptm,"
        "designfolding-complex_plddt,designfolding-bb_rmsd"
    )
    rows = [
        # ipTM is low across the board — that is the real distribution.
        "a.cif,AAAA,0.583,0.877,0.35",   # best fold, self-consistent -> pass
        "b.cif,CCCC,0.471,0.842,1.80",   # ditto                      -> pass
        "c.cif,DDDD,0.207,0.610,1.10",   # poorly folded              -> fail
        "d.cif,EEEE,0.390,0.860,7.60",   # folded but not consistent  -> fail
    ]
    ranked = bg.filter_and_rank(bg.parse_metrics_csv(_csv(tmp_path, header, *rows)))
    statuses = {d["design_name"]: d["scores"]["filter_status"] for d in ranked}
    assert statuses == {
        "a": "pass", "b": "pass", "c": "below threshold", "d": "below threshold"
    }, statuses


def test_a_missing_leg_fails_closed(tmp_path):
    """An unmeasured leg is not a passed leg. This label is what the results
    page turns into 'advance to validation' or not, so absence must not read
    as success."""
    assert bg.label_filter_status({}) == "below threshold"
    assert bg.label_filter_status({"pLDDT": 95.0}) == "below threshold"
    assert bg.label_filter_status({"refolding_rmsd": 0.5}) == "below threshold"
    assert bg.label_filter_status({"pLDDT": 95.0, "refolding_rmsd": 0.5}) == "pass"
    # ipTM neither rescues a failing leg nor breaks a passing one.
    assert bg.label_filter_status(
        {"pLDDT": 95.0, "refolding_rmsd": 0.5, "ipTM": 0.01}
    ) == "pass"
    assert bg.label_filter_status({"ipTM": 0.99}) == "below threshold"


def test_both_gates_share_one_definition(tmp_path):
    """The pilot path labels via ``filter_and_rank`` and the smoke/mini_pilot
    path labels inline. They were two copies of one expression, so the ipTM leg
    had to be deleted in both places or in neither. Pin that the inline copy is
    gone and the smoke path calls the same function."""
    import ast
    import inspect

    # AST, not a substring count: IPTM_THRESHOLD is named several times in the
    # comments that explain WHY it is not a gate, and a text search cannot tell
    # those from a live comparison. What must not exist is a comparison.
    tree = ast.parse(inspect.getsource(bg))
    compares = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(n, ast.Name) and n.id == "IPTM_THRESHOLD"
            for n in [node.left, *node.comparators]
        )
    ]
    assert compares == [], (
        f"{len(compares)} comparison(s) against IPTM_THRESHOLD are back at "
        f"line(s) {[n.lineno for n in compares]} — 0.70 is a Boltz-2 cofold "
        f"bar and this pipeline does not compute a cofold ipTM"
    )

    # And the smoke path must reach the label through the shared function
    # rather than growing its own copy again.
    smoke = inspect.getsource(bg.run_smoke_tier)
    assert "label_filter_status" in smoke
    assert "filter_status" in inspect.getsource(bg.filter_and_rank)


def test_a_plddt_fallback_to_the_complex_column_is_logged_loudly(tmp_path, caplog):
    """The regression can come back without anyone editing this file.

    The image pip-installs boltzgen UNPINNED. A build that stops emitting
    ``designfolding-complex_plddt`` drops pLDDT onto the whole-complex column
    silently — the fallback is behaving correctly, the values still look like
    pLDDTs, and every design quietly returns to "below threshold". So the
    fallback has to be audible.
    """
    import logging

    header = "file_name,designed_sequence,design_iptm,complex_plddt,bb_rmsd"
    with caplog.at_level(logging.WARNING, logger="boltzgen_pipeline"):
        bg.parse_metrics_csv(_csv(tmp_path, header, "d0.cif,AAAA,0.41,0.61,1.2"))
    assert any(
        "PLDDT_THRESHOLD" in r.message or "unreachable" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


def test_no_warning_when_plddt_comes_from_the_refold(tmp_path, caplog):
    """The premise: the warning above is conditional, not printed every run.
    A warning on every run is one nobody reads."""
    import logging

    header = (
        "file_name,designed_sequence,design_iptm,"
        "designfolding-complex_plddt,complex_plddt,designfolding-bb_rmsd"
    )
    with caplog.at_level(logging.WARNING, logger="boltzgen_pipeline"):
        bg.parse_metrics_csv(_csv(tmp_path, header, "d0.cif,AAAA,0.41,0.86,0.61,1.2"))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        [r.message for r in caplog.records]
    )
