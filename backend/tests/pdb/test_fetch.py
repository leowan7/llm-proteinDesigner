"""Tests for PDB/UniProt fetch operations (INPUT-02, INPUT-03, INPUT-04)."""
import pytest


class TestRCSBFetch:
    """INPUT-02: User provides PDB accession; system fetches from RCSB."""

    @pytest.mark.anyio
    async def test_fetch_by_pdb_id(self):
        """Fetching a valid PDB ID (e.g. '4ZS7') returns non-empty CIF bytes."""
        pytest.skip("STUB — implementation in Plan 02-02")

    @pytest.mark.anyio
    async def test_fetch_invalid_pdb_id_raises(self):
        """Fetching a nonexistent PDB ID raises httpx.HTTPStatusError (404)."""
        pytest.skip("STUB — implementation in Plan 02-02")


class TestUniProtResolve:
    """INPUT-03: User provides UniProt accession; system resolves to PDB."""

    @pytest.mark.anyio
    async def test_uniprot_to_pdb(self):
        """UniProt accession P08887 resolves to at least one PDB cross-reference."""
        pytest.skip("STUB — implementation in Plan 02-02")

    @pytest.mark.anyio
    async def test_uniprot_unknown_accession(self):
        """Unknown UniProt accession returns empty results list."""
        pytest.skip("STUB — implementation in Plan 02-02")


class TestNaturalLanguageResolve:
    """INPUT-04: User describes target in natural language; agent resolves to PDB."""

    @pytest.mark.anyio
    async def test_nl_to_pdb(self):
        """Query 'IL-6 receptor' returns reviewed UniProt entry with PDB refs."""
        pytest.skip("STUB — implementation in Plan 02-02")

    @pytest.mark.anyio
    async def test_nl_no_results(self):
        """Nonsense query returns empty results."""
        pytest.skip("STUB — implementation in Plan 02-02")
