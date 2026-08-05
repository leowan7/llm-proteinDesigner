"""Tests for PXDesign's multi-chain target handling.

``docker/pxdesign/run_pipeline.py`` ships inside the Modal image and cannot
import from the backend package, so it is loaded here by path. Only the
chain/hotspot resolution and YAML-spec construction are exercised — no GPU,
no PXDesign install.

Why this file exists: the wrapper was written for single-chain targets and
silently discarded the multi-chain capability PXDesign has upstream
(``target.chains`` is a per-chain map there). Two failure modes are
specifically guarded:

- ``ensure_cif`` used to filter the structure down to one chain, so a
  correct multi-chain YAML pointed at a CIF that no longer contained the
  second protomer.
- ``build_yaml_spec`` used to log-and-skip hotspots that missed the
  renumber map. On a per-chain map that turns one wrong chain key into
  ``hotspots: []`` — an untargeted design that still completes and scores.
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
    _REPO_ROOT, "docker", "pxdesign", "run_pipeline.py"
)

# gemmi is a container dependency, not a backend one. The pure-parser tests
# below run without it; the CIF-writing tests skip if it is absent.
gemmi = pytest.importorskip("gemmi", reason="gemmi is a container-only dep")

yaml = pytest.importorskip("yaml")


def _load_pipeline():
    """Import docker/pxdesign/run_pipeline.py by path, once."""
    if _PDB_UTILS not in sys.path:
        # The container mounts pipeline_normalize.py bare at /opt; locally it
        # lives in backend/pdb_utils/.
        sys.path.insert(0, _PDB_UTILS)
    if "pxdesign_run_pipeline" in sys.modules:
        return sys.modules["pxdesign_run_pipeline"]
    spec = importlib.util.spec_from_file_location(
        "pxdesign_run_pipeline", _RUN_PIPELINE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pxdesign_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


pxd = _load_pipeline()


# ---------------------------------------------------------------------------
# Synthetic target fixtures
# ---------------------------------------------------------------------------

_RESNAMES = ["ALA", "GLY", "SER", "THR", "VAL", "LEU"]


def _residue(serial: int, chain: str, resnum: int, resname: str, x: float):
    lines = []
    for i, (aname, elem, dx, dy) in enumerate([
        ("N", "N", 0.0, 0.0), ("CA", "C", 1.0, 0.0),
        ("C", "C", 2.0, 0.0), ("O", "O", 2.0, 1.0),
    ]):
        lines.append(
            f"ATOM  {serial + i:5d} {' ' + aname.ljust(3)}"
            f" {resname:>3} {chain}{resnum:4d}    "
            f"{x + dx:8.3f}{dy:8.3f}{0.0:8.3f}{1.0:6.2f}{10.0:6.2f}"
            f"          {elem:>2}\n"
        )
    return "".join(lines), serial + 4


def _make_pdb(chains) -> str:
    """chains: [(chain_id, first_author_resnum, n_residues)].

    The x base is non-zero on purpose — an all-zero backbone atom is
    (correctly) dropped by the normalizer's drop_zero_backbone filter and
    would skew the residue counts asserted below.
    """
    out = ["HEADER    SYNTH FC-LIKE\n"]
    serial = 1
    for ci, (cid, first, n) in enumerate(chains):
        for k in range(n):
            block, serial = _residue(
                serial, cid, first + k, _RESNAMES[k % len(_RESNAMES)],
                x=10.0 + 100.0 * ci + 4.0 * k,
            )
            out.append(block)
    out.append("END\n")
    return "".join(out)


# Author-numbered 20..40 on both chains — the IgG1 Fc homodimer shape, and
# an offset that makes a missing renumber step obvious.
SINGLE_CHAIN_PDB = _make_pdb([("A", 20, 21)])
TWO_CHAIN_PDB = _make_pdb([("A", 20, 21), ("B", 20, 21)])


@pytest.fixture
def two_chain_target(tmp_path):
    p = tmp_path / "target.pdb"
    p.write_text(TWO_CHAIN_PDB)
    return str(p)


@pytest.fixture
def single_chain_target(tmp_path):
    p = tmp_path / "target.pdb"
    p.write_text(SINGLE_CHAIN_PDB)
    return str(p)


def _spec_for(tmp_path, target_pdb, target_chain, hotspots, sub="work"):
    work = tmp_path / sub
    work.mkdir(exist_ok=True)
    cif, renumber_map = pxd.ensure_cif(
        target_pdb, str(work), target_chain=target_chain
    )
    job_spec = {
        "target_chain": target_chain,
        "hotspot_residues": hotspots,
        "parameters": {"binder_length": 80, "num_designs": 4},
    }
    spec = pxd.build_yaml_spec(
        job_spec, cif, preset="preview", num_designs=4, binder_length=80,
        renumber_map=renumber_map,
    )
    return spec, cif, renumber_map


# ---------------------------------------------------------------------------
# parse_target_chains / parse_hotspot_token
# ---------------------------------------------------------------------------

def test_parse_target_chains_scalar_and_comma():
    assert pxd.parse_target_chains("A") == ["A"]
    assert pxd.parse_target_chains("A,B") == ["A", "B"]
    assert pxd.parse_target_chains(" A , B ") == ["A", "B"]


def test_parse_target_chains_requires_at_least_one():
    for empty in ("", "  ", ",", None):
        with pytest.raises(ValueError, match="at least one chain"):
            pxd.parse_target_chains(empty)


def test_bare_int_hotspot_attaches_to_first_chain():
    assert pxd.parse_hotspot_token(296, ["A", "B"]) == ("A", 296)
    assert pxd.parse_hotspot_token("296", ["A", "B"]) == ("A", 296)
    # Order is caller-supplied, so "first" follows the target_chain string.
    assert pxd.parse_hotspot_token(296, ["B", "A"]) == ("B", 296)


def test_chain_prefixed_hotspot_parses():
    assert pxd.parse_hotspot_token("A296", ["A", "B"]) == ("A", 296)
    assert pxd.parse_hotspot_token("B264", ["A", "B"]) == ("B", 264)
    assert pxd.parse_hotspot_token(" B264 ", ["A", "B"]) == ("B", 264)


def test_hotspot_naming_a_non_target_chain_raises():
    with pytest.raises(ValueError, match="does not name any target chain"):
        pxd.parse_hotspot_token("C25", ["A", "B"])


def test_unparseable_hotspot_raises():
    for bad in ("xyz", "A", "AB", "A2x", ""):
        with pytest.raises(ValueError):
            pxd.parse_hotspot_token(bad, ["A", "B"])


def test_multi_character_chain_id_prefix_matches_longest():
    assert pxd.parse_hotspot_token("AA12", ["A", "AA"]) == ("AA", 12)


# ---------------------------------------------------------------------------
# Backward compatibility — single chain must behave exactly as before
# ---------------------------------------------------------------------------

def test_single_chain_spec_shape_unchanged(tmp_path, single_chain_target):
    spec, _, _ = _spec_for(tmp_path, single_chain_target, "A", [25, 30, 35])
    assert spec["target"]["chains"].keys() == {"A"}
    assert spec["target"]["chains"]["A"]["crop"] == ["1-21"]
    # Author 25/30/35 with the chain starting at author 20 -> 6/11/16.
    assert spec["target"]["chains"]["A"]["hotspots"] == [6, 11, 16]
    assert spec["binder_length"] == 80
    assert spec["N_sample"] == 4
    assert spec["preset"] == "preview"


def test_single_chain_on_multi_chain_input_keeps_only_that_chain(
    tmp_path, two_chain_target
):
    spec, cif, _ = _spec_for(tmp_path, two_chain_target, "A", [25])
    assert list(spec["target"]["chains"]) == ["A"]
    st = gemmi.read_structure(cif)
    assert [ch.name for ch in st[0]] == ["A"]


# ---------------------------------------------------------------------------
# Multi-chain targets
# ---------------------------------------------------------------------------

def test_two_chain_spec_emits_per_chain_map(tmp_path, two_chain_target):
    spec, _, _ = _spec_for(
        tmp_path, two_chain_target, "A,B", ["A25", "A30", "B25", "B30"]
    )
    chains = spec["target"]["chains"]
    assert sorted(chains) == ["A", "B"]
    assert chains["A"]["hotspots"] == [6, 11]
    assert chains["B"]["hotspots"] == [6, 11]
    assert chains["A"]["crop"] == ["1-21"]
    assert chains["B"]["crop"] == ["1-21"]


def test_two_chain_cif_actually_contains_both_chains(
    tmp_path, two_chain_target
):
    """The failure that passes every other check: a two-chain job whose CIF
    quietly holds one protomer still produces and scores designs."""
    _, cif, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25", "B25"])
    st = gemmi.read_structure(cif)
    assert sorted(ch.name for ch in st[0]) == ["A", "B"]
    assert {ch.name: len(ch) for ch in st[0]} == {"A": 21, "B": 21}


def test_two_chain_renumber_map_is_per_chain(tmp_path, two_chain_target):
    _, _, renumber_map = _spec_for(
        tmp_path, two_chain_target, "A,B", ["A25", "B25"]
    )
    assert renumber_map[("A", 20)] == 1
    assert renumber_map[("B", 20)] == 1
    assert renumber_map[("A", 40)] == 21
    assert renumber_map[("B", 40)] == 21


def test_hotspots_land_on_the_chain_they_name(tmp_path, two_chain_target):
    """A hotspot on B must not leak into A's list — the two chains are
    author-numbered identically here, so a chain-blind implementation would
    put both on whichever chain it looked at first."""
    spec, _, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["B30"])
    chains = spec["target"]["chains"]
    assert chains["A"]["hotspots"] == []
    assert chains["B"]["hotspots"] == [11]


def test_bare_ints_on_two_chain_target_go_to_first_chain(
    tmp_path, two_chain_target
):
    spec, _, _ = _spec_for(tmp_path, two_chain_target, "A,B", [25, 30])
    chains = spec["target"]["chains"]
    assert chains["A"]["hotspots"] == [6, 11]
    assert chains["B"]["hotspots"] == []


def test_chain_order_follows_target_chain_string(tmp_path, two_chain_target):
    spec, _, _ = _spec_for(tmp_path, two_chain_target, "B,A", ["B25"])
    assert list(spec["target"]["chains"]) == ["B", "A"]


# ---------------------------------------------------------------------------
# Trap B — an unmapped hotspot must be a hard error, never hotspots: []
# ---------------------------------------------------------------------------

def test_out_of_range_hotspot_raises(tmp_path, two_chain_target):
    with pytest.raises(ValueError, match="not present after structure cleanup"):
        _spec_for(tmp_path, two_chain_target, "A,B", ["A999"])


def test_hotspot_on_untargeted_chain_raises(tmp_path, two_chain_target):
    with pytest.raises(ValueError, match="does not name any target chain"):
        _spec_for(tmp_path, two_chain_target, "A,B", ["C25"])


def test_single_chain_out_of_range_hotspot_raises(
    tmp_path, single_chain_target
):
    """Previously logged-and-skipped, yielding an untargeted design."""
    with pytest.raises(ValueError, match="not present after structure cleanup"):
        _spec_for(tmp_path, single_chain_target, "A", [999])


def test_no_hotspots_supplied_is_still_allowed(tmp_path, two_chain_target):
    """An empty input list is a legitimate untargeted run; only a non-empty
    list collapsing to empty is the silent failure."""
    spec, _, _ = _spec_for(tmp_path, two_chain_target, "A,B", [])
    chains = spec["target"]["chains"]
    assert chains["A"]["hotspots"] == []
    assert chains["B"]["hotspots"] == []


# ---------------------------------------------------------------------------
# Named chain missing from the structure
# ---------------------------------------------------------------------------

def test_missing_target_chain_raises(tmp_path, two_chain_target):
    with pytest.raises(ValueError, match=r"Target chain\(s\) \['Z'\]"):
        _spec_for(tmp_path, two_chain_target, "A,Z", ["A25"])


def test_spec_round_trips_through_yaml(tmp_path, two_chain_target):
    """PXDesign reads the spec off disk; make sure it survives yaml.dump."""
    spec, _, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25", "B30"])
    path = tmp_path / "spec.yaml"
    with open(path, "w") as fh:
        yaml.dump(spec, fh, default_flow_style=False)
    reloaded = yaml.safe_load(path.read_text())
    assert reloaded["target"]["chains"]["A"]["hotspots"] == [6]
    assert reloaded["target"]["chains"]["B"]["hotspots"] == [11]
