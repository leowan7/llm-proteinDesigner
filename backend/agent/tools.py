"""Claude tool definitions and Python dispatch handlers for the agent.

Exports:
    TOOL_DEFINITIONS: JSON schemas sent to the Claude API tools parameter.
    dispatch_tool: Async handler that executes a tool call server-side and
        returns a JSON string for the tool_result content block.
"""

import json

from agent.wizard import WIZARD_PARAMS

TOOL_DEFINITIONS = [
    {
        "name": "resolve_structure",
        "description": (
            "Fetch a target protein structure from RCSB PDB or UniProt. "
            "Use when the user provides a PDB accession (4 characters, e.g. '4ZS7'), "
            "a UniProt accession (6-10 characters, e.g. 'P08887'), or a plain protein name. "
            "Do NOT use for user-uploaded PDB files -- those are handled separately via the upload endpoint. "
            "Returns a structure summary card for display."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "PDB accession, UniProt accession, or protein name.",
                },
                "query_type": {
                    "type": "string",
                    "enum": ["pdb_accession", "uniprot_accession", "natural_language"],
                },
            },
            "required": ["query", "query_type"],
        },
    },
    {
        "name": "classify_intent",
        "description": (
            "Classify the user's protein design intent and recommend the appropriate tool. "
            "Design types: minibinder (small de novo binder), vhh_nanobody (single-domain antibody-like), "
            "de_novo_backbone (new fold without binding target), motif_scaffolding (embed motif in new scaffold), "
            "conformational_ensemble (sample conformational landscape), structure_prediction (predict/validate 3D structure). "
            "Use AFTER asking the user what type of protein they want to design and receiving their answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_type": {
                    "type": "string",
                    "enum": [
                        "minibinder",
                        "vhh_nanobody",
                        "de_novo_backbone",
                        "motif_scaffolding",
                        "conformational_ensemble",
                        "structure_prediction",
                    ],
                },
                "recommended_tool": {
                    "type": "string",
                    "enum": ["rfdiffusion", "rfantibody", "bindcraft", "boltzgen"],
                },
                "rationale": {
                    "type": "string",
                    "description": "One plain-language sentence explaining the tool recommendation.",
                },
            },
            "required": ["design_type", "recommended_tool", "rationale"],
        },
    },
    {
        "name": "collect_parameters",
        "description": (
            "Collect tool-specific parameters for the design job. "
            "Returns the parameter schema with Ranomics-curated defaults for the selected tool. "
            "Use after the user has confirmed the recommended tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "enum": ["rfdiffusion", "rfantibody", "bindcraft", "boltzgen"],
                },
                "target_chain": {
                    "type": "string",
                    "description": "The chain ID to target (e.g. 'A').",
                },
                "hotspot_residues": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Residue numbers for binder contact points. Empty for non-binder designs.",
                },
                "user_overrides": {
                    "type": "object",
                    "description": "User-specified parameter overrides (name: value pairs).",
                },
            },
            "required": ["tool", "target_chain"],
        },
    },
    {
        "name": "validate_preflight",
        "description": (
            "Run pre-flight validation checks on the design inputs. "
            "Checks PDB quality, hotspot accessibility (SASA), and parameter sanity. "
            "Returns a checklist of pass/warn/fail results. "
            "Use after parameters are collected, before presenting the review card."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pdb_path": {
                    "type": "string",
                    "description": "Path to the normalized PDB file on server.",
                },
                "chain_id": {
                    "type": "string",
                },
                "hotspot_residues": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "tool": {
                    "type": "string",
                    "enum": ["rfdiffusion", "rfantibody", "bindcraft", "boltzgen"],
                },
                "parameters": {
                    "type": "object",
                    "description": "Collected parameter values.",
                },
            },
            "required": ["pdb_path", "chain_id", "tool", "parameters"],
        },
    },
]


async def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool call and return the result as a JSON string.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters from Claude's tool_use block.

    Returns:
        JSON string result to be sent back as tool_result content.
    """
    if tool_name == "resolve_structure":
        return await _handle_resolve_structure(tool_input)
    elif tool_name == "classify_intent":
        return await _handle_classify_intent(tool_input)
    elif tool_name == "collect_parameters":
        return await _handle_collect_parameters(tool_input)
    elif tool_name == "validate_preflight":
        return await _handle_validate_preflight(tool_input)
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


async def _handle_resolve_structure(tool_input: dict) -> str:
    """Handle resolve_structure tool call.

    Delegates to pdb_utils.fetch for PDB/UniProt lookups. Handles three
    query types: pdb_accession, uniprot_accession, and natural_language.
    """
    import httpx

    query = tool_input["query"]
    query_type = tool_input["query_type"]

    try:
        from pdb_utils.fetch import fetch_pdb_file, search_uniprot, resolve_pdb_for_uniprot
    except ImportError:
        # pdb_utils.fetch not yet available (Plan 02-02 not yet run)
        return json.dumps({
            "status": "error",
            "message": "Structure resolution service not yet available.",
        })

    async with httpx.AsyncClient() as client:
        if query_type == "pdb_accession":
            try:
                data = await fetch_pdb_file(query, client)
                return json.dumps({
                    "status": "success",
                    "pdb_id": query.upper(),
                    "file_size_bytes": len(data),
                    "message": f"Structure {query.upper()} fetched from RCSB ({len(data)} bytes).",
                })
            except httpx.HTTPStatusError:
                return json.dumps({
                    "status": "error",
                    "message": f"Could not fetch {query} from RCSB. Verify the accession and try again.",
                })

        elif query_type == "uniprot_accession":
            try:
                pdb_refs = await resolve_pdb_for_uniprot(query, client)
                if not pdb_refs:
                    return json.dumps({
                        "status": "no_results",
                        "message": f"UniProt accession {query} has no PDB cross-references.",
                    })
                return json.dumps({
                    "status": "success",
                    "uniprot_accession": query,
                    "pdb_references": pdb_refs[:5],
                    "total_structures": len(pdb_refs),
                })
            except httpx.HTTPStatusError:
                return json.dumps({
                    "status": "error",
                    "message": f"UniProt accession '{query}' not found.",
                })

        elif query_type == "natural_language":
            results = await search_uniprot(query, client)
            if not results:
                return json.dumps({
                    "status": "no_results",
                    "message": (
                        f"No reviewed UniProt entries matched '{query}'. "
                        "Try a more specific name or provide a PDB accession directly."
                    ),
                })
            # Return top result with PDB cross-refs
            top = results[0]
            return json.dumps({
                "status": "success",
                "uniprot_accession": top.get("primaryAccession"),
                "protein_name": (
                    top.get("proteinDescription", {})
                    .get("recommendedName", {})
                    .get("fullName", {})
                    .get("value", "Unknown")
                ),
                "pdb_cross_references": [
                    xref
                    for xref in top.get("uniProtKBCrossReferences", [])
                    if xref.get("database") == "PDB"
                ][:5],
            })

    return json.dumps({"status": "error", "message": "Invalid query_type"})


async def _handle_classify_intent(tool_input: dict) -> str:
    """Handle classify_intent tool call.

    This is a passthrough: Claude has already done the classification.
    We echo back the values so the frontend can render the intent card.
    """
    return json.dumps({
        "design_type": tool_input["design_type"],
        "recommended_tool": tool_input["recommended_tool"],
        "rationale": tool_input["rationale"],
    })


async def _handle_collect_parameters(tool_input: dict) -> str:
    """Handle collect_parameters tool call.

    Returns the wizard parameter schema with Ranomics-curated defaults,
    merged with any user-supplied overrides.
    """
    tool = tool_input["tool"]
    params = WIZARD_PARAMS.get(tool, [])

    # Merge user overrides with defaults
    overrides = tool_input.get("user_overrides") or {}
    collected = {}
    for param in params:
        if param.name in overrides:
            collected[param.name] = overrides[param.name]
        else:
            collected[param.name] = param.default

    return json.dumps({
        "tool": tool,
        "target_chain": tool_input.get("target_chain", "A"),
        "hotspot_residues": tool_input.get("hotspot_residues") or [],
        "parameters": collected,
        "parameter_descriptions": {
            p.name: {
                "label": p.label,
                "description": p.description,
                "default": p.default,
            }
            for p in params
        },
    })


async def _handle_validate_preflight(tool_input: dict) -> str:
    """Handle validate_preflight tool call.

    Runs hotspot SASA checks (if residues provided) and parameter sanity
    checks (min/max against wizard definitions). Returns a structured list
    of pass/warn/fail results for the review card.
    """
    import asyncio

    pdb_path = tool_input["pdb_path"]
    chain_id = tool_input["chain_id"]
    hotspot_residues = tool_input.get("hotspot_residues") or []

    results = []

    # Hotspot accessibility check (if residues provided)
    if hotspot_residues:
        try:
            from pdb_utils.validate import check_hotspot_accessibility

            loop = asyncio.get_event_loop()
            hotspot_checks = await loop.run_in_executor(
                None, check_hotspot_accessibility, pdb_path, chain_id, hotspot_residues
            )
            for check in hotspot_checks:
                status = "pass" if check.accessible else "warn"
                results.append({
                    "check_name": f"hotspot_{check.residue_number}",
                    "status": status,
                    "message": (
                        check.warning
                        or (
                            f"Residue {check.residue_number} ({check.residue_name}) "
                            f"is surface-accessible (SASA={check.sasa} Angstrom^2)."
                        )
                    ),
                })
        except ImportError:
            results.append({
                "check_name": "hotspot_accessibility",
                "status": "warn",
                "message": "Hotspot SASA check not yet available (pdb_utils not installed).",
            })
        except Exception as exc:
            results.append({
                "check_name": "hotspot_accessibility",
                "status": "warn",
                "message": f"Could not compute SASA: {exc}",
            })

    # Parameter sanity checks against wizard min/max definitions
    tool = tool_input["tool"]
    params = tool_input.get("parameters") or {}
    wizard_defs = {p.name: p for p in WIZARD_PARAMS.get(tool, [])}
    for name, value in params.items():
        defn = wizard_defs.get(name)
        if defn and defn.min_value is not None and value < defn.min_value:
            results.append({
                "check_name": f"param_{name}",
                "status": "fail",
                "message": f"{defn.label} ({value}) is below minimum ({defn.min_value}).",
            })
        elif defn and defn.max_value is not None and value > defn.max_value:
            results.append({
                "check_name": f"param_{name}",
                "status": "fail",
                "message": f"{defn.label} ({value}) exceeds maximum ({defn.max_value}).",
            })
        else:
            results.append({
                "check_name": f"param_{name}",
                "status": "pass",
                "message": f"{defn.label if defn else name}: {value} (within range).",
            })

    has_fail = any(r["status"] == "fail" for r in results)
    has_warn = any(r["status"] == "warn" for r in results)

    if not has_fail and not has_warn:
        summary = "Pre-flight checks passed. Ready to launch."
    elif not has_fail:
        summary = "Warnings found -- user acknowledgment required."
    else:
        summary = "Cannot proceed -- fix the following issues."

    return json.dumps({
        "validation_results": results,
        "can_proceed": not has_fail,
        "has_warnings": has_warn,
        "summary": summary,
    })
