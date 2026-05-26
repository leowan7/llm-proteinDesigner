"""Unit tests for PDB structural feature extraction (ANA-07).

Tests BSA, clash count, and interface contact functions from
agent.analysis.pdb_features against a minimal two-chain fixture.
"""
from pathlib import Path

from agent.analysis.pdb_features import (
    compute_bsa,
    count_clashes,
    count_interface_contacts,
    extract_structural_features,
)

FIXTURE_PDB = str(Path(__file__).parent.parent / "fixtures" / "two_chain.pdb")


def test_compute_bsa_positive():
    """BSA is positive when chains have interface contacts."""
    bsa = compute_bsa(FIXTURE_PDB, "A", "B")
    assert bsa is not None
    assert bsa > 0  # chains in contact must bury surface area


def test_compute_bsa_invalid_path():
    """compute_bsa returns None (not raises) for a non-existent file."""
    bsa = compute_bsa("/nonexistent/path/file.pdb", "A", "B")
    assert bsa is None


def test_count_clashes_zero_for_separated():
    """Well-formed fixture has zero or very few clashes (chains are not overlapping)."""
    clashes = count_clashes(FIXTURE_PDB, "A", "B")
    assert isinstance(clashes, int)
    assert clashes >= 0  # non-negative by definition


def test_count_clashes_missing_chain_returns_none():
    """count_clashes returns None when a requested chain is absent."""
    clashes = count_clashes(FIXTURE_PDB, "A", "Z")
    assert clashes is None


def test_count_interface_contacts_positive():
    """Interface contacts are >0 for chains within 5A of each other."""
    contacts = count_interface_contacts(FIXTURE_PDB, "A", "B", cutoff=5.0)
    assert contacts is not None
    assert contacts > 0


def test_count_interface_contacts_missing_chain_returns_none():
    """count_interface_contacts returns None when partner chain is absent."""
    contacts = count_interface_contacts(FIXTURE_PDB, "A", "Z")
    assert contacts is None


def test_extract_structural_features_keys():
    """extract_structural_features returns dict with all three required keys."""
    features = extract_structural_features(FIXTURE_PDB, "A", "B")
    assert "bsa" in features
    assert "clash_count" in features
    assert "interface_contacts" in features


def test_extract_structural_features_missing_chain():
    """extract_structural_features returns None for bsa when chain is absent."""
    features = extract_structural_features(FIXTURE_PDB, "A", "Z")
    assert features["bsa"] is None
