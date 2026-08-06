"""FastAPI router for PDB structure upload and fetch.

Exposes four endpoints that cover all three structure input paths:
  POST /pdb/upload   — upload a PDB/mmCIF file directly
  POST /pdb/fetch    — fetch a structure from RCSB by PDB accession
  POST /pdb/search   — search UniProt reviewed entries by free-text query
  POST /pdb/resolve  — resolve a UniProt accession to ranked PDB structures

All endpoints require a valid JWT (via get_current_user dependency).
All upload/fetch paths run the structure through normalize_structure before
returning so callers always receive a normalized file path.
"""
import os
import tempfile

import httpx
from auth.dependencies import get_current_user
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from pdb_utils.fetch import fetch_pdb_file, resolve_pdb_for_uniprot, search_uniprot
from pdb_utils.metadata import extract_structure_metadata
from pdb_utils.normalize import normalize_structure

router = APIRouter(prefix="/pdb", tags=["pdb"])

# Accepted upload extensions
_VALID_EXTENSIONS = (".pdb", ".cif", ".mmcif", ".ent")


class FetchRequest(BaseModel):
    """Request body for /pdb/fetch."""

    pdb_id: str  # 4-character PDB accession (e.g. '4ZS7')


class UniProtRequest(BaseModel):
    """Request body for /pdb/resolve."""

    accession: str  # UniProt accession (e.g. 'P08887')


class SearchRequest(BaseModel):
    """Request body for /pdb/search."""

    query: str  # Free-text protein name or description


@router.post("/upload")
async def upload_pdb(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    """Upload a PDB or mmCIF file, normalize it, and return a structure summary.

    The uploaded file must have a .pdb, .cif, .mmcif, or .ent extension.
    The normalization step converts MSE residues, selects NMR model 0, and
    reports any changes applied.

    Returns:
        JSON with 'normalized_path' (server-side path to normalized file)
        and 'changes' (list of normalization change strings).
    """
    filename = file.filename or ""
    if not any(filename.lower().endswith(ext) for ext in _VALID_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="File must have a .pdb, .cif, or .mmcif extension",
        )

    # Enforce max upload size (50 MB).
    max_upload_bytes = 50 * 1024 * 1024
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = os.path.join(tmpdir, filename)
        content = await file.read()
        if len(content) > max_upload_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({len(content)} bytes). Maximum is 50 MB.",
            )
        with open(raw_path, "wb") as f:
            f.write(content)

        try:
            result = normalize_structure(raw_path, tmpdir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            meta = extract_structure_metadata(result.output_path)
        except Exception:
            meta = {}

        return {
            "normalized_path": result.output_path,
            "changes": result.changes,
            "chain_count": meta.get("chain_count", 0),
            "chain_ids": meta.get("chain_ids", []),
            "selected_chain": meta.get("selected_chain", "A"),
            "residue_count": meta.get("residue_count", 0),
        }


@router.post("/fetch")
async def fetch_pdb(
    req: FetchRequest,
    user_id: str = Depends(get_current_user),
):
    """Fetch a PDB structure from RCSB by accession, normalize it, and return a summary.

    The mmCIF file is downloaded from files.rcsb.org. A 404 from RCSB is
    surfaced as a 404 with a user-friendly message. Other HTTP errors from
    RCSB are returned as 502.

    Returns:
        JSON with 'pdb_id', 'normalized_path', and 'changes'.
    """
    async with httpx.AsyncClient() as client:
        try:
            data = await fetch_pdb_file(req.pdb_id, client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Could not find '{req.pdb_id}' in the RCSB PDB. "
                        f"Verify the accession and try again."
                    ),
                )
            raise HTTPException(status_code=502, detail="RCSB fetch failed")

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = os.path.join(tmpdir, f"{req.pdb_id.upper()}.cif")
        with open(raw_path, "wb") as f:
            f.write(data)
        try:
            result = normalize_structure(raw_path, tmpdir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {
            "pdb_id": req.pdb_id.upper(),
            "normalized_path": result.output_path,
            "changes": result.changes,
        }


@router.post("/search")
async def search_proteins(
    req: SearchRequest,
    user_id: str = Depends(get_current_user),
):
    """Search UniProt reviewed entries by free-text query.

    Returns up to 5 Swiss-Prot entries matching the query. If no entries
    match, returns 404 with an actionable message.

    Returns:
        JSON with 'results' (list of UniProt entry dicts).
    """
    async with httpx.AsyncClient() as client:
        results = await search_uniprot(req.query, client)
    if not results:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No reviewed UniProt entries matched '{req.query}'. "
                f"Try a more specific protein name or provide a PDB or UniProt accession directly."
            ),
        )
    return {"results": results}


@router.post("/resolve")
async def resolve_uniprot(
    req: UniProtRequest,
    user_id: str = Depends(get_current_user),
):
    """Resolve a UniProt accession to a ranked list of PDB structures.

    PDB cross-references are sorted by method priority (X-ray > EM > NMR)
    then by resolution. A 404 from UniProt is surfaced as a 404.

    Returns:
        JSON with 'accession' and 'pdb_references' (ranked list of PDB xref dicts).
    """
    async with httpx.AsyncClient() as client:
        try:
            pdb_refs = await resolve_pdb_for_uniprot(req.accession, client)
        except httpx.HTTPStatusError:
            raise HTTPException(
                status_code=404,
                detail=f"UniProt accession '{req.accession}' not found.",
            )
    return {"accession": req.accession, "pdb_references": pdb_refs}
