"""RCSB PDB download and UniProt search/resolve functions.

Provides three async functions that serve as the entry points for the
three structure input paths: direct RCSB fetch, UniProt accession lookup,
and free-text UniProt search (natural language resolution path).

All functions accept an httpx.AsyncClient instance so callers can share
a single connection pool (e.g., in FastAPI endpoint handlers).
"""
import httpx

from config import settings


async def fetch_pdb_metadata(pdb_id: str, client: httpx.AsyncClient) -> dict:
    """Fetch entry metadata from RCSB REST API for a PDB accession.

    Returns a dict with protein_name, resolution, method, and per-chain
    details (chain ID, protein name, residue count) for every polymer entity.

    Args:
        pdb_id: 4-character PDB accession (case-insensitive).
        client: An open httpx.AsyncClient instance.

    Returns:
        Dict with keys: protein_name, resolution, method, chain_count,
        chains (list of {id, name, residue_count}), deposited_residue_count.
    """
    pdb_id = pdb_id.upper()
    result: dict = {
        "protein_name": "Unknown protein",
        "resolution": None,
        "method": "",
        "chain_count": 0,
        "chains": [],
        "deposited_residue_count": 0,
    }

    try:
        # Entry-level metadata (resolution, method, title)
        entry_url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
        entry_resp = await client.get(entry_url, timeout=10.0)
        entry_resp.raise_for_status()
        entry = entry_resp.json()

        # Resolution
        res_list = entry.get("rcsb_entry_info", {}).get("resolution_combined", [None])
        result["resolution"] = res_list[0] if res_list else None

        # Experimental method
        methods = entry.get("exptl", [])
        if methods:
            result["method"] = methods[0].get("method", "")

        # Title as fallback
        title = entry.get("struct", {}).get("title", "")
        result["deposited_residue_count"] = entry.get(
            "rcsb_entry_info", {}
        ).get("deposited_polymer_monomer_count", 0)

        # Iterate over polymer entities to get per-chain info
        chains = []
        first_entity_name = ""
        polymer_entity_ids = entry.get(
            "rcsb_entry_container_identifiers", {}
        ).get("polymer_entity_ids", [])

        for eid in polymer_entity_ids:
            ent_url = f"https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/{eid}"
            ent_resp = await client.get(ent_url, timeout=10.0)
            if ent_resp.status_code != 200:
                continue

            ent = ent_resp.json()
            entity_desc = ent.get("rcsb_polymer_entity", {}).get("pdbx_description", "")
            if not first_entity_name and entity_desc:
                first_entity_name = entity_desc

            # Extract organism/species from source organism annotation
            source_organisms = ent.get("rcsb_entity_source_organism", [])
            organism_name = ""
            if source_organisms:
                organism_name = source_organisms[0].get("ncbi_scientific_name", "")

            # Get auth chain IDs for this entity
            auth_chains = ent.get(
                "rcsb_polymer_entity_container_identifiers", {}
            ).get("auth_asym_ids", [])

            # Get residue count from entity sequence length
            seq_length = ent.get("entity_poly", {}).get("rcsb_sample_sequence_length", 0)

            for chain_id in auth_chains:
                chains.append({
                    "id": chain_id,
                    "name": entity_desc or "Unknown",
                    "residue_count": seq_length,
                    "organism": organism_name,
                })

        result["chains"] = chains
        result["chain_count"] = len(chains)
        result["protein_name"] = first_entity_name or title or "Unknown protein"

    except Exception:
        pass

    return result


async def fetch_pdb_file(pdb_id: str, client: httpx.AsyncClient) -> bytes:
    """Download a PDB-format structure file from RCSB by PDB accession.

    Args:
        pdb_id: 4-character PDB accession string (e.g. '4ZS7'). Case-insensitive;
                converted to uppercase before constructing the URL.
        client: An open httpx.AsyncClient instance.

    Returns:
        Raw bytes of the PDB-format file from RCSB.

    Raises:
        httpx.HTTPStatusError: If RCSB returns a non-2xx response, including
                               404 for unknown accessions. (Very large
                               structures with >62 chains or >100k residues
                               are only served as .cif and will 404 here;
                               this is a known follow-up.)

    History: Previously fetched .cif (mmCIF) format but saved with a .pdb
    extension at the resolve_structure call site. The downstream container's
    PDBParser then crashed on mmCIF tokens like 'U' from `_entry.id`, which
    blocked Phase 11 SC 6 close-out on 2026-06-03. Switched to .pdb so the
    file is genuinely PDB format and downstream parsing works.
    """
    url = f"{settings.rcsb_base_url}/download/{pdb_id.upper()}.pdb"
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
