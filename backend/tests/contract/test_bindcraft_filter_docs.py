"""Contract test: the agent reference docs quote BindCraft's real filter thresholds.

The agent reads backend/agent/reference/02_technical_setup_guide.md and repeats its
"Default Filter Thresholds" table to users. That table drifted badly from reality once
(wrong scales on pLDDT and i_pAE, a SAP filter that does not exist, a clash filter that
is not active). This test locks it to a snapshot of the real file.

Ground truth is settings_filters/default_filters.json on FreeBindCraft master -- the
filter_set backend/pipelines/bindcraft.py selects, cloned at image build time by
docker/bindcraft/Dockerfile.

Tests, in definition order:
  - test_docs_match_snapshot: the filter table's values and directions, offline.
  - test_no_sap_claims_in_reference_docs: SAP may appear only in lines that deny it
    exists, and those denials may not silently disappear either.
  - test_inline_threshold_mentions_match_snapshot: thresholds restated in prose, not just
    the table -- 01 and 03 quote several, and the agent repeats those just as readily.
  - test_doc_states_the_snapshotted_facts: the stated counts, the per-model shape, the
    eight hard-coded constants and the four failure sentinels.
  - test_snapshot_matches_upstream: network, opt-in via CHECK_UPSTREAM_FILTERS=1. Catches
    upstream drift (the Dockerfile clones master unpinned), including the per-model shape.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REF_DIR = REPO_ROOT / "backend/agent/reference"
DOC = REF_DIR / "02_technical_setup_guide.md"
SNAPSHOT = Path(__file__).parent / "_bindcraft_filters_snapshot.json"
SAP_SNAPSHOT = Path(__file__).parent / "_sap_mentions_snapshot.txt"
UPSTREAM = (
    "https://raw.githubusercontent.com/cytokineking/FreeBindCraft/"
    "master/settings_filters/default_filters.json"
)
UPSTREAM_SRC = (
    "https://raw.githubusercontent.com/cytokineking/FreeBindCraft/"
    "master/functions/pr_alternative_utils.py"
)

# Thresholds restated in prose outside the table, e.g. `Average_i_pAE <= 0.35`, or the
# per-model form `1_ShapeComplementarity >= 0.55`. Both get quoted to users verbatim.
# The sign matters: `Average_dG <= -30` was a real defect, and without -? it would not
# match at all, so it would go unchecked rather than fail.
INLINE = re.compile(r"`(Average|[1-5])_([A-Za-z_%]+)\s*(>=|<=)\s*(-?[0-9.]+)")


def _fetch(url):
    """GET a raw upstream file, skipping the test when the network is unavailable.

    Without this a transient DNS failure surfaces as a bare URLError under a dozen lines
    of urllib internals, which reads like the docs are wrong when they are not.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.URLError as exc:  # pragma: no cover - network-dependent
        pytest.skip(f"upstream unreachable ({exc}); this test needs network access")


def _snapshot_meta():
    """The whole snapshot document, including the _-prefixed metadata entries."""
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _snapshot():
    """Just the active Average_* filters, as {key: {threshold, higher}}."""
    return {
        k: {"threshold": v["threshold"], "higher": v["higher"]}
        for k, v in _snapshot_meta().items()
        if not k.startswith("_")
    }


def _reference_docs():
    """Every reference doc as (path, text), read once."""
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(REF_DIR.rglob("*.md"))]


def _doc_table_rows():
    """Parse `key` | <pass condition> rows out of the Default Filter Thresholds table.

    Returns {filter_key: (direction, threshold)} where direction is '>=' or '<='.
    """
    text = DOC.read_text(encoding="utf-8")
    heads = [
        m.start()
        for m in re.finditer(r"^### Default Filter Thresholds\s*$", text, re.M)
    ]
    assert len(heads) == 1, (
        f"Expected exactly one 'Default Filter Thresholds' heading, found {len(heads)}. "
        "A duplicate section would leave the later one unchecked."
    )
    start = heads[0]
    # Bound at the next heading of any level, so adding a subsection or reordering
    # cannot silently truncate what gets checked.
    nxt = re.compile(r"^#{1,4} ", re.M).search(text, start + 1)
    end = nxt.start() if nxt else len(text)
    row = re.compile(
        r"^\|\s*`(Average_[A-Za-z_%/]+)`[^|]*\|\s*(>=|<=)\s*(-?[0-9.]+)", re.M
    )
    rows = {}
    for m in row.finditer(text[start:end]):
        key = m.group(1)
        assert key not in rows, f"{key} appears twice in the filter table"
        rows[key] = (m.group(2), float(m.group(3)))
    return rows


def test_docs_match_snapshot():
    """Every threshold in the doc table matches the real filter file, and vice versa."""
    snap = _snapshot()
    rows = _doc_table_rows()

    assert rows, "Could not parse any rows out of the Default Filter Thresholds table"

    missing = sorted(set(snap) - set(rows))
    assert not missing, (
        f"Active filters absent from the doc table: {missing}. "
        "Every non-null threshold must be documented, or the agent will omit it."
    )

    extra = sorted(set(rows) - set(snap))
    assert not extra, (
        f"Doc table lists filters that are not active upstream: {extra}. "
        "Do not document a filter that rejects nothing."
    )

    for key, (direction, value) in sorted(rows.items()):
        expected = snap[key]
        want_dir = ">=" if expected["higher"] else "<="
        assert direction == want_dir, (
            f"{key}: doc says '{direction}', filter file has higher={expected['higher']} "
            f"(i.e. '{want_dir}')"
        )
        assert value == pytest.approx(expected["threshold"]), (
            f"{key}: doc says {value}, filter file says {expected['threshold']}"
        )


def test_no_sap_claims_in_reference_docs():
    """BindCraft computes no Spatial Aggregation Propensity; docs must not imply otherwise.

    'SAP' may appear only in the exact lines snapshotted in _sap_mentions_snapshot.txt,
    all of which deny it exists. An exact allowlist beats a phrase regex: a substring test
    like "not compute" passes any sentence that merely contains it, including one
    asserting that BindCraft does report a SAP score. Guards the regression where these
    docs advertised a SAP_score filter and told users to add one.

    The check runs both ways -- a denial may not be added to, nor quietly deleted from,
    the docs without updating the fixture.
    """
    allowed = {
        line.strip()
        for line in SAP_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("//")
    }
    offenders, found = [], set()
    for path, text in _reference_docs():
        for lineno, line in enumerate(text.splitlines(), 1):
            if "SAP" not in line:
                continue
            found.add(line.strip())
            if line.strip() not in allowed:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    missing = sorted(allowed - found)
    assert not missing, (
        "A SAP denial disappeared from the reference docs. These lines are load-bearing: "
        "without them nothing tells the agent SAP is not a real metric:\n"
        + "\n".join(missing)
    )
    assert not offenders, (
        "SAP mentioned outside the allowlist (it is not a real metric here). If the new "
        "line is legitimate, add it to _sap_mentions_snapshot.txt deliberately:\n"
        + "\n".join(offenders)
    )


def test_inline_threshold_mentions_match_snapshot():
    """Prose like `Average_i_pAE <= 0.35` anywhere in the reference docs must agree too.

    The table is not the only place the agent reads a threshold from:
    01_tool_selection_guide.md restates several in prose and 03_metric_profiles.md quotes
    them in its band notes. Those get read out to users just as readily as the table does.

    Per-model keys are checked against the _per_model shape, so a doc cannot invent a
    filter on a model that does not carry one.
    """
    snap = _snapshot()
    per_model = _snapshot_meta()["_per_model"]
    bad = []
    for path, text in _reference_docs():
        for lineno, line in enumerate(text.splitlines(), 1):
            for prefix, base, direction, value in INLINE.findall(line):
                key = f"Average_{base}"
                where = f"{path.name}:{lineno}"
                if key not in snap:
                    bad.append(f"{where}: {key} is not an active filter")
                    continue

                expected = snap[key]["threshold"]
                model = per_model.get(prefix)
                if model is not None:
                    if model["rule"] == "only" and base not in model["active"]:
                        bad.append(
                            f"{where}: {prefix}_{base} is not active on model {prefix} "
                            f"(only {', '.join(model['active'])} are)"
                        )
                        continue
                    override = (model.get("except") or {}).get(base, "absent")
                    if override != "absent":
                        if override is None:
                            bad.append(
                                f"{where}: {prefix}_{base} is null on model {prefix}; "
                                "only the average carries it"
                            )
                            continue
                        expected = override

                want = ">=" if snap[key]["higher"] else "<="
                if direction != want or float(value) != pytest.approx(expected):
                    bad.append(
                        f"{where}: doc says '{prefix}_{base} {direction} {value}', "
                        f"filter file says '{want} {expected}'"
                    )
    assert not bad, (
        "Inline threshold mentions disagree with the filter file:\n" + "\n".join(bad)
    )


def test_doc_states_the_snapshotted_facts():
    """The counts, the per-model shape, the constants and the sentinels, as stated in prose.

    None of these are thresholds, so nothing above covers them -- but they are what the
    agent quotes. The most dangerous are the constants: the agent is told an H-bond count
    of 5 means "not measured", and that an exact 0.70 shape complementarity is a failure
    rather than a good fit, so a drifted value silently re-points those instructions.

    Constants are checked per file: a file that names a constant must state its real value
    somewhere in that file. (Not per line -- several legitimate lines name a token without
    its value, and `surface_hydrophobicity` deliberately carries two different sentinels.)
    """
    meta = _snapshot_meta()
    doc = DOC.read_text(encoding="utf-8")

    keys = meta["_top_level_keys"]
    assert f"{keys} top-level keys" in doc, (
        f"The snapshot records {keys} top-level keys but 02_technical_setup_guide.md does "
        "not say so. Update the count sentence."
    )
    leaves = meta["_active_leaves"]
    assert f"{leaves} active thresholds" in doc, (
        f"The snapshot records {leaves} active thresholds but "
        "02_technical_setup_guide.md does not say so. Update the count sentence."
    )
    # Matched on the claim, not a whole sentence, so a harmless rewording does not fail.
    assert "only two active filters" in doc, (
        "02_technical_setup_guide.md no longer states that models 3-5 carry only two "
        "active filters; re-check the per-model note."
    )

    pairs = [
        (name, value)
        for name, value in meta["_constants"].items()
        if not name.startswith("_")
    ] + [
        tuple(pair) for key, pair in meta["_sentinels"].items() if not key.startswith("_")
    ]

    bad = []
    for path, text in _reference_docs():
        lines = text.splitlines()
        for token, value in pairs:
            if token not in text:
                continue
            if not any(token in line and value in line for line in lines):
                bad.append(
                    f"{path.name}: mentions {token} but never states its real value {value}"
                )
    assert not bad, (
        "A hard-coded constant or failure sentinel is stated with the wrong value:\n"
        + "\n".join(bad)
    )

    absent = [f"{t} = {v}" for t, v in pairs if t not in doc]
    assert not absent, (
        "02_technical_setup_guide.md no longer states these hard-coded values:\n"
        + "\n".join(absent)
    )


@pytest.mark.skipif(
    os.environ.get("CHECK_UPSTREAM_FILTERS") != "1",
    reason="network test; set CHECK_UPSTREAM_FILTERS=1 to check upstream drift",
)
def test_snapshot_matches_upstream():
    """The snapshot still reflects FreeBindCraft master.

    The image clones master unpinned, so upstream can change thresholds under us
    without any commit here. If this fails, update the snapshot AND the doc table.
    """
    upstream = json.loads(_fetch(UPSTREAM))
    meta = _snapshot_meta()

    active = {}
    for k, v in upstream.items():
        if not k.startswith("Average_") or not isinstance(v, dict):
            continue
        if "threshold" in v:
            if v["threshold"] is not None:
                active[k] = {"threshold": v["threshold"], "higher": v["higher"]}
            continue
        # Nested per-residue dict (Average_InterfaceAAs). Flatten to one entry when
        # every active sub-threshold agrees; otherwise fail loudly rather than guess.
        subs = {
            sk: (sv["threshold"], sv["higher"])
            for sk, sv in v.items()
            if sv.get("threshold") is not None
        }
        if not subs:
            continue
        distinct = set(subs.values())
        assert len(distinct) == 1, (
            f"{k} now has differing sub-thresholds {subs}; the doc table's single "
            "'K, M | <= 3 each' row can no longer represent it."
        )
        thr, higher = distinct.pop()
        active[k] = {"threshold": thr, "higher": higher}

    assert active == _snapshot(), (
        "FreeBindCraft default_filters.json changed upstream. Update "
        "_bindcraft_filters_snapshot.json and the Default Filter Thresholds table "
        "in backend/agent/reference/02_technical_setup_guide.md together."
    )

    leaves = 0
    for v in upstream.values():
        if not isinstance(v, dict):
            continue
        if "threshold" in v:
            leaves += v["threshold"] is not None
        else:
            leaves += sum(sv["threshold"] is not None for sv in v.values())

    assert len(upstream) == meta["_top_level_keys"], (
        f"Upstream now has {len(upstream)} top-level keys. Update _top_level_keys in "
        "_bindcraft_filters_snapshot.json and the count sentence in "
        "02_technical_setup_guide.md together."
    )
    assert leaves == meta["_active_leaves"], (
        f"Upstream now has {leaves} active thresholds. Update _active_leaves in "
        "_bindcraft_filters_snapshot.json and the count sentence in "
        "02_technical_setup_guide.md together."
    )

    # The constants live in upstream Python source, not the filter file, and the image
    # clones master unpinned -- so they can drift without any commit here.
    src = _fetch(UPSTREAM_SRC)
    drifted = [
        f"{name} is no longer assigned {value} upstream"
        for name, value in meta["_constants"].items()
        if not name.startswith("_")
        and not re.search(
            rf"^\s*{re.escape(name)}\s*=\s*{re.escape(value)}\s*(#|$)", src, re.M
        )
    ]
    assert not drifted, (
        "FreeBindCraft's hard-coded constants changed. Update _constants in "
        "_bindcraft_filters_snapshot.json and the constants table in "
        "02_technical_setup_guide.md together:\n" + "\n".join(drifted)
    )

    for model, shape in meta["_per_model"].items():
        if model.startswith("_") or shape["rule"] != "only":
            continue
        active_for_model = sorted(
            k[len(model) + 1 :]
            for k, v in upstream.items()
            if k.startswith(f"{model}_")
            and "threshold" in v
            and v["threshold"] is not None
        )
        assert active_for_model == sorted(shape["active"]), (
            f"Model {model} active filters changed to {active_for_model}, but the "
            f"snapshot records {sorted(shape['active'])}. Update _per_model and the "
            "per-model note in 02_technical_setup_guide.md together."
        )


# Metric name in agent.analysis.tools.METRIC_THRESHOLDS -> filter key in the snapshot.
# Metrics with no entry here are not filtered by BindCraft, so any band is reachable.
_SCORED_TO_FILTER = {
    "ipTM": "Average_i_pTM",
    "pLDDT": "Average_pLDDT",
    "ShapeComplementarity": "Average_ShapeComplementarity",
    "Surface_Hydrophobicity": "Average_Surface_Hydrophobicity",
    "dSASA": "Average_dSASA",
}


def test_scoring_bands_are_reachable_after_filtering():
    """METRIC_THRESHOLDS must not band values BindCraft already rejected.

    agent/analysis/tools.py is the executable twin of the interpretation table in
    03_metric_profiles.md. Every candidate it scores has already passed default_filters.json,
    so a band on the far side of a filter can never fire -- it only ever mislabels. This
    shipped once: pLDDT red at 0.7 and ShapeComplementarity red at 0.5 were both unreachable,
    and a dG rule scored a value that is a fixed constant.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from agent.analysis.tools import METRIC_THRESHOLDS

    snap = _snapshot()
    problems = []

    assert "dG" not in METRIC_THRESHOLDS, (
        "dG is back in METRIC_THRESHOLDS. FreeBindCraft has no PyRosetta, so interface_dG is "
        "the fixed constant -10.0 for every design -- scoring it ranks noise."
    )

    for metric, filter_key in _SCORED_TO_FILTER.items():
        band = METRIC_THRESHOLDS.get(metric)
        if band is None:
            continue
        limit = snap[filter_key]["threshold"]
        higher_is_better = snap[filter_key]["higher"]

        if higher_is_better:
            if band["green"] < limit:
                problems.append(
                    f"{metric}: green {band['green']} is below the filter line {limit}, so "
                    "every candidate is already 'strong'"
                )
            if band["red"] is not None and band["red"] <= limit:
                problems.append(
                    f"{metric}: red {band['red']} is at or below the filter line {limit}, so "
                    "it can never fire -- use None"
                )
        else:
            if band["green"] > limit:
                problems.append(
                    f"{metric}: green {band['green']} is above the filter line {limit}, so "
                    "every candidate is already 'strong'"
                )
            if band["red"] is not None and band["red"] >= limit:
                problems.append(
                    f"{metric}: red {band['red']} is at or above the filter line {limit}, so "
                    "it can never fire -- use None"
                )

    assert not problems, (
        "Scoring bands disagree with the filters that ran upstream of them. Fix "
        "METRIC_THRESHOLDS and 03_metric_profiles.md together:\n" + "\n".join(problems)
    )
