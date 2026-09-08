"""Contract test: the agent reference docs quote BindCraft's real filter thresholds.

The agent reads backend/agent/reference/02_technical_setup_guide.md and repeats its
"Default Filter Thresholds" table to users. That table drifted badly from reality once
(wrong scales on pLDDT and i_pAE, a SAP filter that does not exist, a clash filter that
is not active). This test locks it to a snapshot of the real file.

Ground truth is settings_filters/default_filters.json on FreeBindCraft master -- the
filter_set backend/pipelines/bindcraft.py selects, cloned at image build time by
docker/bindcraft/Dockerfile.

Tests:
  - test_docs_match_snapshot: the filter table's values and directions, offline.
  - test_no_sap_claims_in_reference_docs: SAP may only appear in lines that deny it exists,
    and those denials may not silently disappear either.
  - test_doc_prose_counts_match_snapshot: the counts and per-model shape stated in prose.
  - test_doc_states_the_hard_coded_constants: the eight constants and four hard-coded fallbacks.
  - test_inline_threshold_mentions_match_snapshot: thresholds restated in prose, not just
    the table -- 01 and 03 quote several, and the agent repeats those just as readily.
  - test_snapshot_matches_upstream: network, opt-in via CHECK_UPSTREAM_FILTERS=1. Catches
    upstream drift (the Dockerfile clones master unpinned), including the per-model shape.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "backend/agent/reference/02_technical_setup_guide.md"
SNAPSHOT = Path(__file__).parent / "_bindcraft_filters_snapshot.json"
UPSTREAM = (
    "https://raw.githubusercontent.com/cytokineking/FreeBindCraft/"
    "master/settings_filters/default_filters.json"
)
UPSTREAM_SRC = (
    "https://raw.githubusercontent.com/cytokineking/FreeBindCraft/"
    "master/functions/pr_alternative_utils.py"
)
SAP_SNAPSHOT = Path(__file__).parent / "_sap_mentions_snapshot.txt"

# Thresholds restated in prose outside the table, e.g. `Average_i_pAE <= 0.35`, or the
# per-model form `1_ShapeComplementarity >= 0.55`. Both get quoted to users verbatim.
INLINE = re.compile(r"`(Average|[1-5])_([A-Za-z_%]+)\s*(>=|<=)\s*([0-9.]+)")


def _snapshot_meta():
    """The _-prefixed metadata entries (counts, per-model shape) the prose quotes."""
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _snapshot():
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {
        k: {"threshold": v["threshold"], "higher": v["higher"]}
        for k, v in data.items()
        if not k.startswith("_")
    }


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
        r"^\|\s*`(Average_[A-Za-z_%/]+)`[^|]*\|\s*(>=|<=)\s*([0-9.]+)", re.M
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

    'SAP' may appear only in the exact lines snapshotted in _sap_mentions_snapshot.txt, all of
    which deny it exists. An exact allowlist beats a phrase regex: a substring test like
    "not compute" passes any sentence that merely contains it, including one asserting that
    BindCraft does report a SAP score. Guards the regression where these docs advertised a
    SAP_score filter and told users to add one.
    """
    allowed = {
        line.strip()
        for line in SAP_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    offenders = []
    for path in sorted((REPO_ROOT / "backend/agent/reference").rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "SAP" in line and line.strip() not in allowed:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    found = {
        line.strip()
        for path in sorted((REPO_ROOT / "backend/agent/reference").rglob("*.md"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "SAP" in line
    }
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

    The table is not the only place the agent reads a threshold from: 01_tool_selection_guide.md
    restates several in prose and 03_metric_profiles.md quotes them in its band notes. Those
    get read out to users just as readily as the table does.
    """
    snap = _snapshot()
    bad = []
    for path in sorted((REPO_ROOT / "backend/agent/reference").rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for prefix, base, direction, value in INLINE.findall(line):
                key = f"Average_{base}"
                if key not in snap:
                    bad.append(f"{path.name}:{lineno}: {key} is not an active filter")
                    continue
                want = ">=" if snap[key]["higher"] else "<="
                expected = snap[key]["threshold"]
                # Models 1 and 2 mirror the average except ShapeComplementarity, which is 0.55.
                if prefix in ("1", "2") and base == "ShapeComplementarity":
                    expected = 0.55
                if direction != want or float(value) != pytest.approx(expected):
                    bad.append(
                        f"{path.name}:{lineno}: doc says '{prefix}_{base} {direction} "
                        f"{value}', filter file says '{want} {expected}'"
                    )
    assert not bad, (
        "Inline threshold mentions disagree with the filter file:\n" + "\n".join(bad)
    )


def test_doc_prose_counts_match_snapshot():
    """The counts and the per-model shape stated in prose must match the snapshot.

    These are plain string checks needing no network, but the numbers they guard are the
    ones the agent quotes ("218 top-level keys holding 56 active thresholds", "Models 3-5
    carry only two active filters each"). The upstream test verifies the snapshot itself.
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
    assert meta["_models_3_to_5_note"] in doc, (
        "02_technical_setup_guide.md no longer states the models 3-5 shape verbatim "
        f'("{meta["_models_3_to_5_note"]}"); re-check the per-model note.'
    )


def test_doc_states_the_hard_coded_constants():
    """Every constant and failure sentinel must be stated with its real value, in every file
    that names it.

    These are the numbers most dangerous to get wrong: the agent is told an H-bond count of 5
    means "not measured", and that an exact 0.70 shape complementarity is a failure rather
    than a good fit. Checking per file and per line (not just "the value appears somewhere")
    means a doc that mentions a constant with the wrong number fails, even if another file
    still has it right.
    """
    meta = _snapshot_meta()
    pairs = [
        (name, value)
        for name, value in meta["_constants"].items()
        if not name.startswith("_")
    ] + [
        tuple(pair)
        for key, pair in meta["_sentinels"].items()
        if not key.startswith("_")
    ]

    bad = []
    for path in sorted((REPO_ROOT / "backend/agent/reference").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for token, value in pairs:
            if token not in text:
                continue
            if not any(token in line and value in line for line in text.splitlines()):
                bad.append(
                    f"{path.name}: mentions {token} but never on a line with its real "
                    f"value {value}"
                )
    assert not bad, (
        "A hard-coded constant or failure sentinel is stated with the wrong value:\n"
        + "\n".join(bad)
    )
    # And the canonical listing must be present at all.
    doc = DOC.read_text(encoding="utf-8")
    absent = [f"{t} = {v}" for t, v in pairs if t not in doc or v not in doc]
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
    with urllib.request.urlopen(UPSTREAM, timeout=30) as resp:
        upstream = json.loads(resp.read().decode("utf-8"))

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

    # The docs make claims about the per-model keys too, and those are not covered by the
    # Average_* comparison above. The "models 3-5 are all null" claim was wrong once already.
    leaves = 0
    for v in upstream.values():
        if not isinstance(v, dict):
            continue
        if "threshold" in v:
            leaves += v["threshold"] is not None
        else:
            leaves += sum(sv["threshold"] is not None for sv in v.values())
    assert len(upstream) == _snapshot_meta()["_top_level_keys"], (
        f"Upstream now has {len(upstream)} top-level keys. Update _top_level_keys in "
        "_bindcraft_filters_snapshot.json and the count sentence in "
        "02_technical_setup_guide.md together."
    )
    assert leaves == _snapshot_meta()["_active_leaves"], (
        f"Upstream now has {leaves} active thresholds. Update _active_leaves in "
        "_bindcraft_filters_snapshot.json and the count sentence in "
        "02_technical_setup_guide.md together."
    )

    # The constants live in upstream Python source, not the filter file, and the image
    # clones master unpinned -- so they can drift without any commit here.
    src = urllib.request.urlopen(UPSTREAM_SRC, timeout=30).read().decode("utf-8")
    meta = _snapshot_meta()
    drifted = []
    for name, value in meta["_constants"].items():
        if name.startswith("_"):
            continue
        if not re.search(rf"^\s*{re.escape(name)}\s*=\s*{re.escape(value)}\s*(#|$)", src, re.M):
            drifted.append(f"{name} is no longer assigned {value} upstream")
    assert not drifted, (
        "FreeBindCraft's hard-coded constants changed. Update _constants in "
        "_bindcraft_filters_snapshot.json and the constants table in "
        "02_technical_setup_guide.md together:\n" + "\n".join(drifted)
    )

    for model in ("3", "4", "5"):
        active_for_model = sorted(
            k[len(model) + 1 :]
            for k, v in upstream.items()
            if k.startswith(f"{model}_")
            and "threshold" in v
            and v["threshold"] is not None
        )
        assert active_for_model == ["Binder_RMSD", "Binder_pLDDT"], (
            f"Model {model} active filters changed to {active_for_model}. The doc says "
            "models 3-5 carry only Binder_pLDDT and Binder_RMSD."
        )
