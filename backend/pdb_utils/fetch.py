"""RCSB PDB download and UniProt search/resolve functions.

Provides three async functions that serve as the entry points for the
three structure input paths: direct RCSB fetch, UniProt accession lookup,
and free-text UniProt search (natural language resolution path).

All functions accept an httpx.AsyncClient instance so callers can share
a single connection pool (e.g., in FastAPI endpoint handlers).
"""
import httpx

from config import settings


async def fetch_pdb_file(pdb_id: str, client: httpx.AsyncClient) -> bytes:
    """Download an mmCIF structure file from RCSB by PDB accession.

    Args:
        pdb_id: 4-character PDB accession string (e.g. '4ZS7'). Case-insensitive;
                converted to uppercase before constructing the URL.
        client: An open httpx.AsyncClient instance.

    Returns:
        Raw bytes of the mmCIF file from RCSB.

    Raises:
        httpx.HTTPStatusError: If RCSB returns a non-2xx response, including
                               404 for unknown accessions.
    """
    url = f"{settings.rcsb_base_url}/download/{pdb_id.upper()}.cif"
    response = await client.get(url, timeout=30.0)
    response.raise_for_status()
    return response.content


async def search_uniprot(query: str, client: httpx.AsyncClient) -> list[dict]:
    """Search UniProt for Swiss-Prot reviewed entries matching a free-text query.

    Used for the natural language input path: the agent calls this with the
    target protein name extracted from the user's description.

    Args:
        query: Free-text protein name (e.g. 'IL-6 receptor'). The query is
               appended with 'AND reviewed:true' to restrict to Swiss-Prot.
        client: An open httpx.AsyncClient instance.

    Returns:
        List of UniProt result dicts as returned by the REST API. Each dict
        has 'primaryAccession', 'proteinDescription', and optionally
        'uniProtKBCrossReferences' with PDB entries. Returns an empty list
        if no entries match.

    Raises:
        httpx.HTTPStatusError: If the UniProt API returns a non-2xx response.
    """
    url = f"{settings.uniprot_base_url}/uniprotkb/search"
    params = {
        "query": f"({query}) AND reviewed:true",
        "fields": "accession,protein_name,xref_pdb",
        "format": "json",
        "size": 5,
    }
    response = await client.get(url, params=params, timeout=10.0)
    response.raise_for_status()
    data = response.json()
    return data.get("results", [])


async def resolve_pdb_for_uniprot(
    uniprot_accession: str, client: httpx.AsyncClient
) -> list[dict]:
    """Resolve a UniProt accession to a ranked list of PDB cross-references.

    Fetches the UniProt entry for the given accession and extracts PDB
    cross-references. Results are sorted by: (1) experimental method priority
    (X-ray > EM > NMR > other), then (2) resolution in ascending order.

    Args:
        uniprot_accession: UniProt accession string (e.g. 'P08887').
        client: An open httpx.AsyncClient instance.

    Returns:
        List of PDB cross-reference dicts sorted by quality score, each with
        'database', 'id', and 'properties' keys. Returns an empty list if
        the entry has no PDB cross-references.

    Raises:
        httpx.HTTPStatusError: If the UniProt API returns a non-2xx response
                               (including 404 for unknown accessions).
    """
    url = f"{settings.uniprot_base_url}/uniprotkb/{uniprot_accession}"
    params = {"fields": "accession,protein_name,xref_pdb", "format": "json"}
    response = await client.get(url, params=params, timeout=10.0)
    response.raise_for_status()
    data = response.json()

    pdb_refs = [
        xref
        for xref in data.get("uniProtKBCrossReferences", [])
        if xref.get("database") == "PDB"
    ]

    METHOD_PRIORITY = {"X-ray": 0, "EM": 1, "NMR": 2}

    def _score_pdb(xref: dict) -> tuple:
        """Compute (method_priority, resolution) sort key for a PDB xref."""
        props = {p["key"]: p["value"] for p in xref.get("properties", [])}
        method = props.get("Method", "NMR")
        resolution_str = props.get("Resolution", "99.0 A")
        try:
            resolution = float(resolution_str.split()[0])
        except (ValueError, IndexError):
            resolution = 99.0
        return (METHOD_PRIORITY.get(method, 3), resolution)

    pdb_refs.sort(key=_score_pdb)
    return pdb_refs
