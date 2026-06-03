"""Tests for PDB/UniProt fetch operations (INPUT-02, INPUT-03, INPUT-04)."""
import httpx
import pytest
import respx

from pdb_utils.fetch import fetch_pdb_file, search_uniprot, resolve_pdb_for_uniprot


# ---------------------------------------------------------------------------
# Fake response payloads (shaped from real API responses per RESEARCH.md)
# ---------------------------------------------------------------------------

FAKE_UNIPROT_SEARCH_RESPONSE = {
    "results": [
        {
            "primaryAccession": "Q14627",
            "proteinDescription": {
                "recommendedName": {
                    "fullName": {"value": "Interleukin-13 receptor subunit alpha-2"}
                }
            },
            "uniProtKBCrossReferences": [
                {
                    "database": "PDB",
                    "id": "3LB6",
                    "properties": [
                        {"key": "Method", "value": "X-ray"},
                        {"key": "Resolution", "value": "2.00 A"},
                        {"key": "Chains", "value": "A=27-315"},
                    ],
                }
            ],
        }
    ]
}

FAKE_UNIPROT_ACCESSION_RESPONSE = {
    "primaryAccession": "P08887",
    "proteinDescription": {
        "recommendedName": {
            "fullName": {"value": "Interleukin-6 receptor subunit alpha"}
        }
    },
    "uniProtKBCrossReferences": [
        {
            "database": "PDB",
            "id": "1N26",
            "properties": [
                {"key": "Method", "value": "X-ray"},
                {"key": "Resolution", "value": "2.40 A"},
                {"key": "Chains", "value": "A=1-323"},
            ],
        },
        {
            "database": "PDB",
            "id": "4CNI",
            "properties": [
                {"key": "Method", "value": "X-ray"},
                {"key": "Resolution", "value": "3.00 A"},
                {"key": "Chains", "value": "A=1-323"},
            ],
        },
    ],
}


class TestRCSBFetch:
    """INPUT-02: User provides PDB accession; system fetches from RCSB."""

    @pytest.mark.anyio
    @respx.mock
    async def test_fetch_by_pdb_id(self):
        """Fetching a valid PDB ID (e.g. '4ZS7') returns non-empty PDB bytes."""
        respx.get("https://files.rcsb.org/download/4ZS7.pdb").mock(
            return_value=httpx.Response(200, content=b"HEADER    TEST\nATOM      1  N   ALA A   1\n")
        )
        async with httpx.AsyncClient() as client:
            content = await fetch_pdb_file("4ZS7", client)
        assert isinstance(content, bytes)
        assert len(content) > 0

    @pytest.mark.anyio
    @respx.mock
    async def test_fetch_invalid_pdb_id_raises(self):
        """Fetching a nonexistent PDB ID raises httpx.HTTPStatusError (404)."""
        respx.get("https://files.rcsb.org/download/XXXX.pdb").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with httpx.AsyncClient() as client:
                await fetch_pdb_file("XXXX", client)
        assert exc_info.value.response.status_code == 404


class TestUniProtResolve:
    """INPUT-03: User provides UniProt accession; system resolves to PDB."""

    @pytest.mark.anyio
    @respx.mock
    async def test_uniprot_to_pdb(self):
        """UniProt accession P08887 resolves to at least one PDB cross-reference."""
        respx.get("https://rest.uniprot.org/uniprotkb/P08887").mock(
            return_value=httpx.Response(200, json=FAKE_UNIPROT_ACCESSION_RESPONSE)
        )
        async with httpx.AsyncClient() as client:
            pdb_refs = await resolve_pdb_for_uniprot("P08887", client)

        assert isinstance(pdb_refs, list)
        assert len(pdb_refs) >= 1
        # First result should have a PDB ID
        first = pdb_refs[0]
        assert "id" in first or "database" in first

    @pytest.mark.anyio
    @respx.mock
    async def test_uniprot_unknown_accession(self):
        """Unknown UniProt accession returns 404 which propagates as HTTPStatusError."""
        respx.get("https://rest.uniprot.org/uniprotkb/X99999").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with httpx.AsyncClient() as client:
                await resolve_pdb_for_uniprot("X99999", client)
        assert exc_info.value.response.status_code == 404


class TestNaturalLanguageResolve:
    """INPUT-04: User describes target in natural language; agent resolves to PDB."""

    @pytest.mark.anyio
    @respx.mock
    async def test_nl_to_pdb(self):
        """Query 'IL-6 receptor' returns reviewed UniProt entry with PDB refs."""
        # The search URL uses query params — use a pattern match
        respx.get(url__regex=r"https://rest\.uniprot\.org/uniprotkb/search.*").mock(
            return_value=httpx.Response(200, json=FAKE_UNIPROT_SEARCH_RESPONSE)
        )
        async with httpx.AsyncClient() as client:
            results = await search_uniprot("IL-6 receptor", client)

        assert isinstance(results, list)
        assert len(results) >= 1
        first = results[0]
        assert "primaryAccession" in first

    @pytest.mark.anyio
    @respx.mock
    async def test_nl_no_results(self):
        """Nonsense query returns empty results list."""
        respx.get(url__regex=r"https://rest\.uniprot\.org/uniprotkb/search.*").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        async with httpx.AsyncClient() as client:
            results = await search_uniprot("xyzzy_nonexistent_protein_1234", client)

        assert results == []
