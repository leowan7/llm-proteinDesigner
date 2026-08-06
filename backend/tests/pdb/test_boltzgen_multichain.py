"""Tests for BoltzGen's multi-chain target handling.

``docker/boltzgen/run_pipeline.py`` ships inside the Modal image and cannot
import from the backend package, so it is loaded here by path.

Upstream BoltzGen expresses a multi-chain target natively — ``include:``
and ``binding_types:`` are both per-chain LISTS:

    include:
      - chain: {id: A, res_index: "2..50,55.."}
      - chain: {id: B}
    binding_types:
      - chain: {id: A, binding: "5..7,13"}
      - chain: {id: B, not_binding: "all"}

The wrapper put exactly one entry in each list, and filtered the structure
to one chain before writing the CIF.
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
    _REPO_ROOT, "docker", "boltzgen", "run_pipeline.py"
)

gemmi = pytest.importorskip("gemmi", reason="gemmi is a container-only dep")
yaml = pytest.importorskip("yaml")


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

_RESNAMES = ["ALA", "GLY", "SER", "THR", "VAL", "LEU"]


def _residue(serial, chain, resnum, resname, x):
    out = []
    for i, (aname, elem, dx, dy) in enumerate([
        ("N", "N", 0.0, 0.0), ("CA", "C", 1.0, 0.0),
        ("C", "C", 2.0, 0.0), ("O", "O", 2.0, 1.0),
    ]):
        out.append(
            f"ATOM  {serial + i:5d} {' ' + aname.ljust(3)}"
            f" {resname:>3} {chain}{resnum:4d}    "
            f"{x + dx:8.3f}{dy:8.3f}{0.0:8.3f}{1.0:6.2f}{10.0:6.2f}"
            f"          {elem:>2}\n"
        )
    return "".join(out), serial + 4


def _make_pdb(chains):
    out = ["HEADER    SYNTH\n"]
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


SINGLE_PDB = _make_pdb([("A", 20, 21)])
TWO_CHAIN_PDB = _make_pdb([("A", 20, 21), ("B", 20, 21)])


@pytest.fixture
def single_target(tmp_path):
    p = tmp_path / "single.pdb"
    p.write_text(SINGLE_PDB)
    return str(p)


@pytest.fixture
def two_chain_target(tmp_path):
    p = tmp_path / "double.pdb"
    p.write_text(TWO_CHAIN_PDB)
    return str(p)


def _spec_for(tmp_path, target_pdb, target_chain, hotspots, sub="work"):
    work = tmp_path / sub
    work.mkdir(exist_ok=True)
    cif, renumber_map = bg.ensure_cif(
        target_pdb, str(work), target_chain=target_chain
    )
    job_spec = {
        "target_chain": target_chain,
        "hotspot_residues": hotspots,
        "parameters": {"binder_length": {"min": 50, "max": 100}},
    }
    spec = bg.build_yaml_spec(job_spec, cif, renumber_map=renumber_map)
    return spec, cif


def _file_entity(spec):
    return next(e for e in spec["entities"] if "file" in e)["file"]


def _binder_entity(spec):
    return next(e for e in spec["entities"] if "protein" in e)["protein"]


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_single_chain_spec_shape_unchanged(tmp_path, single_target):
    spec, _ = _spec_for(tmp_path, single_target, "A", [25, 30])
    fe = _file_entity(spec)
    assert fe["include"] == [{"chain": {"id": "A"}}]
    assert fe["binding_types"] == [{"chain": {"id": "A", "binding": "6,11"}}]
    assert _binder_entity(spec)["id"] == "B"
    assert _binder_entity(spec)["sequence"] == "50..100"


def test_single_chain_on_two_chain_input_keeps_one_chain(
    tmp_path, two_chain_target
):
    spec, cif = _spec_for(tmp_path, two_chain_target, "A", [25])
    assert [e["chain"]["id"] for e in _file_entity(spec)["include"]] == ["A"]
    st = gemmi.read_structure(cif)
    assert [ch.name for ch in st[0]] == ["A"]


# ---------------------------------------------------------------------------
# Multi-chain
# ---------------------------------------------------------------------------

def test_two_chain_include_lists_both(tmp_path, two_chain_target):
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25", "B30"])
    assert [e["chain"]["id"] for e in _file_entity(spec)["include"]] == ["A", "B"]


def test_two_chain_binding_types_is_per_chain(tmp_path, two_chain_target):
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25", "B30"])
    bt = {
        e["chain"]["id"]: e["chain"]["binding"]
        for e in _file_entity(spec)["binding_types"]
    }
    assert bt == {"A": "6", "B": "11"}


def test_two_chain_cif_contains_both_chains(tmp_path, two_chain_target):
    _, cif = _spec_for(tmp_path, two_chain_target, "A,B", ["A25", "B30"])
    st = gemmi.read_structure(cif)
    assert sorted(ch.name for ch in st[0]) == ["A", "B"]
    assert {ch.name: len(ch) for ch in st[0]} == {"A": 21, "B": 21}


def test_chain_without_hotspots_is_included_but_gets_no_binding_entry(
    tmp_path, two_chain_target
):
    """A protomer with no hotspots is still structure the binder must
    accommodate — it belongs in include: but not in binding_types:."""
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25"])
    fe = _file_entity(spec)
    assert [e["chain"]["id"] for e in fe["include"]] == ["A", "B"]
    assert [e["chain"]["id"] for e in fe["binding_types"]] == ["A"]


def test_hotspots_land_on_the_chain_they_name(tmp_path, two_chain_target):
    """Both chains are author-numbered identically here, so a chain-blind
    implementation would put both hotspots on one chain."""
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["B30"])
    bt = _file_entity(spec)["binding_types"]
    assert bt == [{"chain": {"id": "B", "binding": "11"}}]


def test_bare_ints_go_to_first_target_chain(tmp_path, two_chain_target):
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", [25, 30])
    bt = _file_entity(spec)["binding_types"]
    assert bt == [{"chain": {"id": "A", "binding": "6,11"}}]


# ---------------------------------------------------------------------------
# Binder entity id must not collide with a target chain
# ---------------------------------------------------------------------------

def test_binder_id_single_target_a_is_b():
    assert bg._pick_binder_entity_id(["A"]) == "B"


def test_binder_id_avoids_two_chain_collision():
    assert bg._pick_binder_entity_id(["A", "B"]) == "C"


def test_binder_id_avoids_self_collision_on_single_chain_b():
    """Latent bug: a target_chain of "B" previously produced a binder entity
    also called "B" — two entities claiming one id in the same spec."""
    assert bg._pick_binder_entity_id(["B"]) == "C"


def test_binder_id_in_two_chain_spec(tmp_path, two_chain_target):
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25"])
    binder_id = _binder_entity(spec)["id"]
    include_ids = {e["chain"]["id"] for e in _file_entity(spec)["include"]}
    assert binder_id == "C"
    assert binder_id not in include_ids


# ---------------------------------------------------------------------------
# Unmapped hotspots are a hard error
# ---------------------------------------------------------------------------

def test_out_of_range_hotspot_raises(tmp_path, two_chain_target):
    with pytest.raises(ValueError, match="not present after structure cleanup"):
        _spec_for(tmp_path, two_chain_target, "A,B", ["A999"])


def test_hotspot_on_untargeted_chain_raises(tmp_path, two_chain_target):
    with pytest.raises(ValueError, match="does not name any target chain"):
        _spec_for(tmp_path, two_chain_target, "A,B", ["C25"])


def test_single_chain_out_of_range_hotspot_raises(tmp_path, single_target):
    with pytest.raises(ValueError, match="not present after structure cleanup"):
        _spec_for(tmp_path, single_target, "A", [999])


def test_missing_target_chain_raises(tmp_path, two_chain_target):
    with pytest.raises(ValueError, match=r"Target chain\(s\) \['Z'\]"):
        _spec_for(tmp_path, two_chain_target, "A,Z", ["A25"])


def test_no_hotspots_omits_binding_types(tmp_path, two_chain_target):
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", [])
    assert "binding_types" not in _file_entity(spec)


def test_spec_round_trips_through_yaml(tmp_path, two_chain_target):
    spec, _ = _spec_for(tmp_path, two_chain_target, "A,B", ["A25", "B30"])
    path = tmp_path / "spec.yaml"
    with open(path, "w") as fh:
        yaml.dump(spec, fh, default_flow_style=False)
    reloaded = yaml.safe_load(path.read_text())
    fe = next(e for e in reloaded["entities"] if "file" in e)["file"]
    assert [e["chain"]["id"] for e in fe["include"]] == ["A", "B"]
