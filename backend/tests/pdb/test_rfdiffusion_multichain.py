"""Tests for RFdiffusion's multi-chain target handling.

``docker/rfdiffusion/run_pipeline.py`` ships inside the Modal image and
cannot import from the backend package, so it is loaded here by path. No
GPU, no RFdiffusion install — only the four places the single-chain
assumption was baked in:

a. ``build_contig_string`` — ``contigmap.contigs`` needs a ``/0 `` chain
   break between target chains (the trailing space is required upstream).
b. ``build_hotspot_string`` — ``ppi.hotspot_res`` may span chains.
c. ``infer_binder_chain`` — replaced ``"B" if target_chain == "A" else "A"``,
   which hardcoded exactly two chains.
d. ``_resolve_binder_sequence`` / the AF2 complex FASTA — the binder used to
   be picked positionally.

Plus ``_compute_interface_pae``, whose boundary was ``chain_lengths[0]``.

Stages (c) and (d) fail in ways that STILL PRODUCE OUTPUT, so each is
asserted on directly rather than through a smoke run.
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


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

_AA = [("ALA", "A"), ("GLY", "G"), ("SER", "S"),
       ("THR", "T"), ("VAL", "V"), ("LEU", "L")]


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
    """chains: [(chain_id, first_resnum, n_residues)] -> (text, {chain: seq})"""
    out = ["HEADER    SYNTH\n"]
    seqs, serial = {}, 1
    for ci, (cid, first, n) in enumerate(chains):
        letters = []
        for k in range(n):
            three, one = _AA[k % len(_AA)]
            letters.append(one)
            block, serial = _residue(
                serial, cid, first + k, three, x=10.0 + 100.0 * ci + 4.0 * k
            )
            out.append(block)
        seqs[cid] = "".join(letters)
    out.append("END\n")
    return "".join(out), seqs


SINGLE_PDB, SINGLE_SEQ = _make_pdb([("A", 18, 30)])
DOUBLE_PDB, DOUBLE_SEQ = _make_pdb([("A", 1, 30), ("B", 1, 30)])
# RFdiffusion output backbones: fixed target chains keep their letters, the
# diffused binder takes the next free letter.
BACKBONE_1, _ = _make_pdb([("A", 18, 30), ("B", 1, 12)])
BACKBONE_2, _ = _make_pdb([("A", 1, 30), ("B", 1, 30), ("C", 1, 12)])

BINDER_SEQ = "WWWWWWWWWWWW"


@pytest.fixture
def single_pdb(tmp_path):
    p = tmp_path / "single.pdb"
    p.write_text(SINGLE_PDB)
    return str(p)


@pytest.fixture
def double_pdb(tmp_path):
    p = tmp_path / "double.pdb"
    p.write_text(DOUBLE_PDB)
    return str(p)


@pytest.fixture
def backbone_1(tmp_path):
    p = tmp_path / "bb1.pdb"
    p.write_text(BACKBONE_1)
    return str(p)


@pytest.fixture
def backbone_2(tmp_path):
    p = tmp_path / "bb2.pdb"
    p.write_text(BACKBONE_2)
    return str(p)


def _hydra(pdb_path, target_chain, hotspots):
    args = rfd.build_hydra_args(
        {
            "target_chain": target_chain,
            "hotspot_residues": hotspots,
            "parameters": {
                "num_designs": 2,
                "binder_length": {"min": 50, "max": 70},
            },
        },
        pdb_path,
    )
    return {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args}


# ---------------------------------------------------------------------------
# (a) contigmap.contigs
# ---------------------------------------------------------------------------

def test_single_chain_contig_unchanged(single_pdb):
    assert _hydra(single_pdb, "A", [])["contigmap.contigs"] == "[A18-47/0 50-70]"


def test_two_chain_contig_has_chain_break_between_targets(double_pdb):
    got = _hydra(double_pdb, "A,B", [])["contigmap.contigs"]
    assert got == "[A1-30/0 B1-30/0 50-70]"


def test_chain_break_keeps_the_required_trailing_space(double_pdb):
    """Upstream: "To specify chain breaks, we use /0 " — NOTE, the space is
    important here. Without it the model treats the segments as one
    continuous polymer."""
    got = _hydra(double_pdb, "A,B", [])["contigmap.contigs"]
    assert "/0 " in got
    assert got.count("/0 ") == 2  # A|B and B|binder
    assert "/0B" not in got and "/05" not in got


def test_contig_segment_order_follows_target_chain_string(double_pdb):
    got = _hydra(double_pdb, "B,A", [])["contigmap.contigs"]
    assert got == "[B1-30/0 A1-30/0 50-70]"


def test_contig_reads_real_residue_ranges_per_chain(tmp_path):
    text, _ = _make_pdb([("A", 100, 5), ("B", 7, 9)])
    p = tmp_path / "offset.pdb"
    p.write_text(text)
    got = _hydra(str(p), "A,B", [])["contigmap.contigs"]
    assert got == "[A100-104/0 B7-15/0 50-70]"


# ---------------------------------------------------------------------------
# (b) ppi.hotspot_res
# ---------------------------------------------------------------------------

def test_single_chain_bare_int_hotspots_unchanged(single_pdb):
    got = _hydra(single_pdb, "A", [30, 33, 34])["ppi.hotspot_res"]
    assert got == "[A30,A33,A34]"


def test_hotspots_may_span_chains(double_pdb):
    got = _hydra(double_pdb, "A,B", ["A5", "A9", "B5", "B9"])["ppi.hotspot_res"]
    assert got == "[A5,A9,B5,B9]"


def test_already_qualified_hotspots_are_not_double_prefixed(double_pdb):
    """The old code force-prefixed EVERY hotspot with the single target
    chain, so "B264" became "AB264"."""
    got = _hydra(double_pdb, "A,B", ["B9"])["ppi.hotspot_res"]
    assert got == "[B9]"
    assert "AB" not in got


def test_bare_ints_on_multi_chain_target_attach_to_first_chain(double_pdb):
    assert _hydra(double_pdb, "A,B", [5, 9])["ppi.hotspot_res"] == "[A5,A9]"
    assert _hydra(double_pdb, "B,A", [5, 9])["ppi.hotspot_res"] == "[B5,B9]"


def test_hotspot_naming_an_untargeted_chain_raises(double_pdb):
    with pytest.raises(ValueError, match="does not name any target chain"):
        _hydra(double_pdb, "A,B", ["C5"])


def test_no_hotspot_arg_emitted_when_none_supplied(double_pdb):
    assert "ppi.hotspot_res" not in _hydra(double_pdb, "A,B", [])


# ---------------------------------------------------------------------------
# (c) binder chain — fails while still producing output
# ---------------------------------------------------------------------------

def test_single_chain_binder_matches_the_old_hardcode(backbone_1):
    assert rfd.infer_binder_chain(backbone_1, ["A"]) == "B"


def test_two_chain_binder_is_the_third_chain(backbone_2):
    """The old ``"B" if target_chain == "A" else "A"`` returned "A" for a
    two-chain target — a TARGET chain. ProteinMPNN would then have been told
    to redesign the target and fix the binder, and would still have emitted
    sequences."""
    assert rfd.infer_binder_chain(backbone_2, ["A", "B"]) == "C"


def test_binder_chain_inference_is_order_independent(backbone_2):
    assert rfd.infer_binder_chain(backbone_2, ["B", "A"]) == "C"


def test_binder_chain_raises_when_no_candidate(backbone_1):
    with pytest.raises(RuntimeError, match="Cannot identify the binder chain"):
        rfd.infer_binder_chain(backbone_1, ["A", "B"])


def test_binder_chain_raises_when_ambiguous(backbone_2):
    with pytest.raises(RuntimeError, match="Cannot identify the binder chain"):
        rfd.infer_binder_chain(backbone_2, ["A"])


def test_binder_length_cross_check_accepts_a_plausible_binder(backbone_2):
    """Backbone C is 12 residues; a 10-15 request is consistent."""
    assert rfd.infer_binder_chain(
        backbone_2, ["A", "B"], binder_length={"min": 10, "max": 15}
    ) == "C"


def test_binder_length_cross_check_allows_one_residue_slack(backbone_2):
    assert rfd.infer_binder_chain(
        backbone_2, ["A", "B"], binder_length={"min": 13, "max": 20}
    ) == "C"


def test_binder_length_cross_check_catches_a_chain_swap(backbone_2):
    """The signal a letter-level target/binder swap actually produces: the
    chain picked as binder has a target-sized residue count. Here chain B
    (30 residues, a target protomer) is mislabelled by declaring only A as
    a target, and the 50-70 binder range rejects it."""
    with pytest.raises(RuntimeError, match="outside the requested binder length"):
        rfd.infer_binder_chain(
            backbone_2, ["A", "C"], binder_length={"min": 50, "max": 70}
        )


def test_binder_length_cross_check_skipped_when_not_supplied(backbone_2):
    assert rfd.infer_binder_chain(backbone_2, ["A", "B"]) == "C"


def test_residue_counts_per_chain(backbone_2):
    assert rfd._residue_counts_per_chain(backbone_2) == {"A": 30, "B": 30, "C": 12}


# ---------------------------------------------------------------------------
# (d) binder sequence — fails while still producing output
# ---------------------------------------------------------------------------

def test_vanilla_proteinmpnn_output_passes_through():
    """With assign_fixed_chains, vanilla ProteinMPNN emits only the designed
    chain. This is the live path."""
    assert rfd._resolve_binder_sequence(
        BINDER_SEQ, [DOUBLE_SEQ["A"], DOUBLE_SEQ["B"]], "d0"
    ) == BINDER_SEQ


@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_joined_complex_binder_found_by_identity(position):
    """Fork-style '/'-joined output. The binder is identified by identity, so
    its position in the join is irrelevant — the old positional pick
    (chain_seqs[1] if target_chain == "A" else chain_seqs[0]) would feed AF2
    a target chain as the binder and return a plausible ipTM for the wrong
    molecule."""
    ta, tb = DOUBLE_SEQ["A"], DOUBLE_SEQ["B"]
    joined = {
        "first": f"{BINDER_SEQ}/{ta}/{tb}",
        "middle": f"{ta}/{BINDER_SEQ}/{tb}",
        "last": f"{ta}/{tb}/{BINDER_SEQ}",
    }[position]
    assert rfd._resolve_binder_sequence(joined, [ta, tb], "d0") == BINDER_SEQ


def test_joined_single_chain_complex_resolves():
    ta = SINGLE_SEQ["A"]
    assert rfd._resolve_binder_sequence(
        f"{ta}/{BINDER_SEQ}", [ta], "d0"
    ) == BINDER_SEQ


def test_unresolvable_join_raises_rather_than_guessing():
    ta = SINGLE_SEQ["A"]
    with pytest.raises(RuntimeError, match="Cannot identify the binder sequence"):
        rfd._resolve_binder_sequence(
            f"{ta}/{BINDER_SEQ}/{BINDER_SEQ}", [ta], "d0"
        )


def test_target_sequences_extracted_per_chain(double_pdb):
    a = rfd._extract_target_sequence(double_pdb, "A")
    b = rfd._extract_target_sequence(double_pdb, "B")
    assert a == DOUBLE_SEQ["A"]
    assert b == DOUBLE_SEQ["B"]
    # The AF2 complex is written target chains first, binder last.
    assert ":".join([a, b]).count(":") == 1


# ---------------------------------------------------------------------------
# interface PAE boundary
# ---------------------------------------------------------------------------

def _pae_with_interface(total: int, boundary: int):
    """PAE matrix where every target<->binder cell is 1.0 and the rest 0.0."""
    return [
        [1.0 if (r < boundary) != (c < boundary) else 0.0 for c in range(total)]
        for r in range(total)
    ]


def test_interface_pae_single_chain_unchanged():
    pae = _pae_with_interface(8, 6)
    assert rfd._compute_interface_pae(pae, {"chain_lengths": [6, 2]}, 1) == 1.0


def test_interface_pae_two_target_chains_uses_summed_boundary():
    """chain_lengths[0] would put the boundary after the FIRST protomer and
    score chain B as binder-side — a wrong i_pAE with no visible symptom."""
    pae = _pae_with_interface(8, 6)
    got = rfd._compute_interface_pae(pae, {"chain_lengths": [3, 3, 2]}, 2)
    assert got == 1.0
    # What the old boundary would have produced.
    wrong = rfd._compute_interface_pae(pae, {"chain_lengths": [3, 3, 2]}, 1)
    assert wrong != 1.0


def test_interface_pae_falls_back_when_chain_lengths_missing():
    pae = _pae_with_interface(8, 4)
    assert rfd._compute_interface_pae(pae, {}, 2) == 1.0


def test_interface_pae_empty_matrix():
    assert rfd._compute_interface_pae([], {"chain_lengths": [3, 3, 2]}, 2) == 99.0


# ---------------------------------------------------------------------------
# target_chain parsing
# ---------------------------------------------------------------------------

def test_parse_target_chains_forms():
    assert rfd.parse_target_chains("A") == ["A"]
    assert rfd.parse_target_chains("A,B") == ["A", "B"]
    assert rfd.parse_target_chains(" A , B ") == ["A", "B"]


def test_parse_target_chains_requires_one():
    with pytest.raises(ValueError, match="at least one chain"):
        rfd.parse_target_chains("")
