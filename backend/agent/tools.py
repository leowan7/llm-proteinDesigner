"""Claude tool definitions and Python dispatch handlers for the agent.

Exports:
    TOOL_DEFINITIONS: JSON schemas sent to the Claude API tools parameter.
    dispatch_tool: Async handler that executes a tool call server-side and
        returns a JSON string for the tool_result content block.
"""

import json
import logging

import sentry_sdk

from agent.wizard import WIZARD_PARAMS

logger = logging.getLogger(__name__)

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
            "Design types: minibinder, vhh_nanobody, full_antibody, cyclic_peptide, "
            "small_molecule_binder, de_novo_backbone, motif_scaffolding, symmetric_assembly. "
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
                        "full_antibody",
                        "cyclic_peptide",
                        "small_molecule_binder",
                        "de_novo_backbone",
                        "motif_scaffolding",
                        "symmetric_assembly",
                    ],
                },
                "recommended_tool": {
                    "type": "string",
                    "enum": ["rfdiffusion", "rfantibody", "bindcraft", "boltzgen", "pxdesign"],
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
            "Returns the parameter schema with Kendrew-curated defaults for the selected tool. "
            "Use after the user has confirmed the recommended tool. "
            "ALWAYS pass user_overrides for any parameter the user has named explicitly "
            "(e.g. user says '100 designs' -> {\"num_designs\": 100}; user says 'noise 0.5' -> "
            "{\"noise_scale\": 0.5}). Defaults are only correct when the user has not specified "
            "a value. "
            "Call this tool AGAIN with updated user_overrides if the user later asks to change "
            "any parameter on the rendered ReviewCard -- the new call replaces the previous "
            "ReviewCard with the updated values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "enum": ["rfdiffusion", "rfantibody", "bindcraft", "boltzgen", "pxdesign"],
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
                    "description": (
                        "User-specified parameter overrides as name:value pairs. "
                        "Valid parameter names per tool: "
                        "rfdiffusion = {num_designs, binder_length, noise_scale}; "
                        "bindcraft = {num_designs, design_cycles, mpnn_sampling_temp, filter_score_threshold}; "
                        "rfantibody = {num_designs, antibody_type, cdr_loops}; "
                        "boltzgen = {num_designs, budget, protocol}; "
                        "pxdesign = {num_designs, mode, generator}. "
                        "Unknown keys are silently ignored, so when in doubt include the value -- "
                        "skipping a user-named value is worse than including an extra one."
                    ),
                },
            },
            "required": ["tool", "target_chain"],
        },
    },
    {
        "name": "extract_interface",
        "description": (
            "Extract interface residues from a co-crystal structure. Given a PDB with two chains, "
            "finds all residues on the target chain within 5 Angstroms of the partner chain. "
            "Use this when the user's PDB contains a known binding partner and you need hotspot residues. "
            "Returns a list of interface residues with distances."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pdb_path": {
                    "type": "string",
                    "description": "Path to the PDB file on server.",
                },
                "target_chain": {
                    "type": "string",
                    "description": "Chain ID of the target protein (the chain you want to design binders against).",
                },
                "partner_chain": {
                    "type": "string",
                    "description": "Chain ID of the known binding partner in the co-crystal.",
                },
                "distance_cutoff": {
                    "type": "number",
                    "description": "Distance threshold in Angstroms. Default 5.0.",
                },
            },
            "required": ["pdb_path", "target_chain", "partner_chain"],
        },
    },
    {
        "name": "load_job_results",
        "description": (
            "Load completed job results (candidates, scores, metrics) for analysis. "
            "Call this first when the user asks about job results. "
            "Returns top candidates with distribution statistics. "
            "For jobs with zero output, returns diagnostic information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "UUID of the completed job to load results for.",
                },
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "analyze_candidates",
        "description": (
            "Rank and filter job candidates by specific metrics. "
            "Requires load_job_results to have been called first for this job. "
            "Returns ranked candidates with threshold annotations (strong/passable/red_flag) "
            "and percentile positions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "UUID of the job (must have been loaded via load_job_results).",
                },
                "sort_by": {
                    "type": "string",
                    "description": "Metric name to rank candidates by (e.g. 'ipTM', 'dG', 'pLDDT').",
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "Optional filter criteria as metric:operator:value pairs. "
                        "Example: {\"pLDDT\": {\">\":  0.85}, \"Relaxed_Clashes\": {\"<\": 1}}. "
                        "Supported operators: >, <, >=, <=, between (value is [low, high])."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of ranked candidates to return. Default 10.",
                },
            },
            "required": ["job_id", "sort_by"],
        },
    },
    {
        "name": "flag_red_flags",
        "description": (
            "Scan all candidates from a loaded job for known problematic metric combinations. "
            "Call proactively after load_job_results to surface issues early."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "UUID of the job (must have been loaded via load_job_results).",
                },
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "generate_report",
        "description": (
            "Generate a downloadable analysis report (PDF, CSV, Markdown) for a completed job. "
            "Includes shortlisted candidates with metric tables, red flags, metric interpretation, "
            "experimental next steps, and PDB download links. "
            "Call after analyzing candidates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "UUID of the completed job to generate a report for.",
                },
                "shortlist_ranks": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Specific candidate ranks to include in the shortlist. "
                        "If omitted, uses top 10 by rank."
                    ),
                },
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "submit_refolding_job",
        "description": (
            "Create refolding validation jobs for selected candidates. "
            "Submits AF2-multimer or Boltz2 refolding to validate designed structures independently. "
            "Creates draft jobs that the user can launch. "
            "Always recommend a shortlist and ask for user confirmation before calling this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parent_job_id": {
                    "type": "string",
                    "description": "UUID of the completed design job whose candidates to refold.",
                },
                "candidate_ranks": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ranks of the candidates to create refolding jobs for.",
                },
                "refolding_tool": {
                    "type": "string",
                    "enum": ["boltzgen", "alphafold2_multimer"],
                    "description": "Refolding tool to use. Default: 'boltzgen'.",
                },
            },
            "required": ["parent_job_id", "candidate_ranks"],
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
                    "enum": ["rfdiffusion", "rfantibody", "bindcraft", "boltzgen", "pxdesign"],
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


async def dispatch_tool(tool_name: str, tool_input: dict, user_id: str = "") -> str:
    """Execute a tool call and return the result as a JSON string.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters from Claude's tool_use block.
        user_id: Authenticated user ID (needed for job creation).

    Returns:
        JSON string result to be sent back as tool_result content.
    """
    if tool_name == "resolve_structure":
        return await _handle_resolve_structure(tool_input)
    elif tool_name == "classify_intent":
        return await _handle_classify_intent(tool_input)
    elif tool_name == "collect_parameters":
        return await _handle_collect_parameters(tool_input)
    elif tool_name == "extract_interface":
        return await _handle_extract_interface(tool_input)
    elif tool_name == "validate_preflight":
        return await _handle_validate_preflight(tool_input, user_id=user_id)
    elif tool_name == "load_job_results":
        from agent.analysis.tools import handle_load_job_results
        return await handle_load_job_results(tool_input, user_id=user_id)
    elif tool_name == "analyze_candidates":
        from agent.analysis.tools import handle_analyze_candidates
        return await handle_analyze_candidates(tool_input, user_id=user_id)
    elif tool_name == "flag_red_flags":
        from agent.analysis.tools import handle_flag_red_flags
        return await handle_flag_red_flags(tool_input, user_id=user_id)
    elif tool_name == "generate_report":
        from agent.analysis.report import handle_generate_report
        return await handle_generate_report(tool_input, user_id=user_id)
    elif tool_name == "submit_refolding_job":
        from agent.analysis.refolding import handle_submit_refolding_job
        return await handle_submit_refolding_job(tool_input, user_id=user_id)
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
        from pdb_utils.fetch import fetch_pdb_file, fetch_pdb_metadata, search_uniprot, resolve_pdb_for_uniprot
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
                metadata = await fetch_pdb_metadata(query, client)

                # Persist PDB to disk so validate_preflight can read it
                import os
                pdb_dir = "/tmp/structures"
                os.makedirs(pdb_dir, exist_ok=True)
                pdb_path = os.path.join(pdb_dir, f"{query.upper()}.pdb")
                with open(pdb_path, "w" if isinstance(data, str) else "wb") as fh:
                    fh.write(data)

                chains = metadata.get("chains", [])
                selected_chain = chains[0]["id"] if chains else "A"

                return json.dumps({
                    "status": "success",
                    "pdb_id": query.upper(),
                    "pdb_path": pdb_path,
                    "protein_name": metadata.get("protein_name", "Unknown protein"),
                    "resolution": metadata.get("resolution"),
                    "method": metadata.get("method", ""),
                    "chain_count": metadata.get("chain_count", 0),
                    "selected_chain": selected_chain,
                    "residue_count": metadata.get("deposited_residue_count", 0),
                    "chains": chains,
                    "normalization_changes": [],
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
            # Return top result — auto-resolve best PDB if available
            top = results[0]
            protein_name = (
                top.get("proteinDescription", {})
                .get("recommendedName", {})
                .get("fullName", {})
                .get("value", "Unknown")
            )
            pdb_xrefs = [
                xref
                for xref in top.get("uniProtKBCrossReferences", [])
                if xref.get("database") == "PDB"
            ][:5]

            # Return UniProt result with PDB options — let the agent pick the best one
            # and call resolve_structure again with query_type=pdb_accession.
            # Do NOT auto-resolve — the first PDB cross-ref is often wrong.
            pdb_options = []
            for xref in pdb_xrefs:
                pdb_id = xref.get("id", "")
                # Extract method and resolution from xref properties
                props = {p.get("key"): p.get("value") for p in xref.get("properties", [])}
                pdb_options.append({
                    "pdb_id": pdb_id,
                    "method": props.get("Method", ""),
                    "resolution": props.get("Resolution", ""),
                    "chains": props.get("Chains", ""),
                })

            return json.dumps({
                "status": "success",
                "uniprot_accession": top.get("primaryAccession"),
                "protein_name": protein_name,
                "pdb_options": pdb_options,
                "message": (
                    f"Found {protein_name} ({top.get('primaryAccession')}). "
                    f"{len(pdb_options)} PDB structures available. "
                    "Pick the best one and call resolve_structure with query_type='pdb_accession' to load it."
                ),
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

    Returns the wizard parameter schema with Kendrew-curated defaults,
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


async def _handle_extract_interface(tool_input: dict) -> str:
    """Extract interface residues from a co-crystal structure.

    Uses Biopython's NeighborSearch to find residues on the target chain
    within a distance cutoff of the partner chain.
    """
    import asyncio

    pdb_path = tool_input["pdb_path"]
    target_chain = tool_input["target_chain"]
    partner_chain = tool_input["partner_chain"]
    distance_cutoff = tool_input.get("distance_cutoff", 5.0)

    try:
        from pdb_utils.interface import extract_interface_residues

        loop = asyncio.get_event_loop()
        residues = await loop.run_in_executor(
            None, extract_interface_residues, pdb_path, target_chain, partner_chain, distance_cutoff,
        )

        return json.dumps({
            "status": "success",
            "target_chain": target_chain,
            "partner_chain": partner_chain,
            "distance_cutoff": distance_cutoff,
            "interface_residue_count": len(residues),
            "interface_residues": [
                {
                    "residue_number": r.residue_number,
                    "residue_name": r.residue_name,
                    "min_distance": r.min_distance,
                }
                for r in residues
            ],
            "message": (
                f"Found {len(residues)} interface residues on chain {target_chain} "
                f"within {distance_cutoff} A of chain {partner_chain}."
            ),
        })
    except ImportError:
        return json.dumps({
            "status": "error",
            "message": "Interface extraction not available (pdb_utils.interface not installed).",
        })
    except ValueError as exc:
        return json.dumps({
            "status": "error",
            "message": str(exc),
        })
    except Exception as exc:
        logger.exception("extract_interface failed for pdb=%s chains=%s/%s",
                         pdb_path, target_chain, partner_chain)
        sentry_sdk.capture_exception(exc)
        return json.dumps({
            "status": "error",
            "message": f"Interface extraction failed: {exc}",
        })


async def _handle_validate_preflight(tool_input: dict, user_id: str = "") -> str:
    """Handle validate_preflight tool call.

    Runs hotspot SASA checks (if residues provided) and parameter sanity
    checks (min/max against wizard definitions). Creates a draft job row
    in the database so the ReviewCard can reference it for launch.
    Returns a structured list of pass/warn/fail results for the review card.
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
                "status": "pass",
                "message": "Hotspot SASA check skipped (pdb_utils not installed).",
            })
        except Exception as exc:
            # SASA computation failed — never block launch for this (the
            # ReviewCard still gets a "pass" so the user can proceed), but
            # Sentry-capture so we know when SASA is silently skipping. A
            # surge of these means pdb_utils.validate is broken in prod.
            logger.exception("Hotspot SASA check failed for pdb=%s chain=%s residues=%s",
                             pdb_path, chain_id, hotspot_residues)
            sentry_sdk.capture_exception(exc)
            results.append({
                "check_name": "hotspot_accessibility",
                "status": "pass",
                "message": f"Hotspot SASA check skipped: {exc}",
            })

    # ---------- Structural pre-flight (chain continuity + hotspot presence)
    # Catches the class of bug that crashed Phase 11 SC 6 close-out on
    # 2026-06-03 inside the RFdiffusion container: 1ALU has a disorder gap
    # at residues 52-60, and RFdiffusion's contig builder asserts every
    # residue in the contig range exists in the PDB. Surfacing the gap here
    # turns a $1.65/hr GPU crash into a synchronous validation fail.
    tool = tool_input["tool"]
    try:
        from pdb_utils.validate import scan_chain_gaps, check_hotspots_present
        import asyncio as _asyncio

        loop = _asyncio.get_event_loop()
        gaps = await loop.run_in_executor(None, scan_chain_gaps, pdb_path, chain_id)
        missing_hotspots = await loop.run_in_executor(
            None, check_hotspots_present, pdb_path, chain_id, hotspot_residues
        )

        # RFdiffusion's contig builder asserts every residue exists; ANY gap
        # in the chain is fatal. Other tools are more permissive — gap is
        # just a warn unless it overlaps a hotspot.
        gap_is_fatal_for_tool = tool == "rfdiffusion"
        hotspot_in_gap = any(
            any(start <= r <= end for start, end in gaps)
            for r in hotspot_residues
        )
        if gaps:
            gap_strs = ", ".join(f"{s}-{e}" if s != e else f"{s}" for s, e in gaps)
            severity = "fail" if (gap_is_fatal_for_tool or hotspot_in_gap) else "warn"
            results.append({
                "check_name": "chain_continuity",
                "status": severity,
                "message": (
                    f"Chain {chain_id} has {len(gaps)} numbering gap(s) at "
                    f"residue(s) {gap_strs}. "
                    + (
                        "RFdiffusion's contig builder requires contiguous "
                        "residues and will assert at runtime. "
                        if gap_is_fatal_for_tool
                        else ""
                    )
                    + (
                        "Hotspot residue(s) fall inside a gap. "
                        if hotspot_in_gap
                        else ""
                    )
                    + "Pick a different PDB or a chain without disorder regions."
                ),
            })
        else:
            results.append({
                "check_name": "chain_continuity",
                "status": "pass",
                "message": f"Chain {chain_id} has contiguous residue numbering.",
            })

        if missing_hotspots:
            results.append({
                "check_name": "hotspot_present",
                "status": "fail",
                "message": (
                    f"Hotspot residue(s) {missing_hotspots} are not present in "
                    f"chain {chain_id} (disordered, non-standard, or out of range). "
                    f"Pick hotspots from residues that exist in the model."
                ),
            })
    except ImportError:
        # pdb_utils.validate not available (Plan 02-02 not yet run)
        pass
    except Exception as exc:
        logger.exception("Structural pre-flight failed for pdb=%s chain=%s",
                         pdb_path, chain_id)
        sentry_sdk.capture_exception(exc)
        # Don't block launch on the CHECK itself failing — just surface it.
        results.append({
            "check_name": "structural_preflight",
            "status": "warn",
            "message": f"Structural pre-flight check skipped: {exc}",
        })

    # Parameter sanity checks against wizard min/max definitions
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

    # Create a draft job row so the ReviewCard can reference it for launch.
    job_id = None
    if not has_fail and user_id:
        try:
            import uuid
            from db.connection import get_db_pool
            from storage.client import ensure_pdb_in_s3

            job_id = str(uuid.uuid4())

            # Hoist PDB into S3 here, in the BACKEND container where the local
            # /tmp file from resolve_structure actually exists. The agent's
            # resolve_structure writes to /tmp/structures/<id>.pdb inside the
            # backend container, but the arq worker that processes /jobs/launch
            # runs in a separate Railway service with a separate /tmp. Without
            # this hoist, worker/tasks.py:ensure_pdb_in_s3 would always raise
            # FileNotFoundError and the job would fail before reaching Modal,
            # which is exactly what blocked SC 6 close-out on 2026-06-03.
            #
            # ensure_pdb_in_s3 is idempotent: if pdb_path already looks like an
            # S3 key (e.g. resolve_structure was updated in a future change to
            # upload directly), it returns the key unchanged.
            s3_pdb_path = ensure_pdb_in_s3(pdb_path, user_id=user_id, job_id=job_id)

            job_spec = {
                "tool": tool,
                "target_pdb_path": s3_pdb_path,
                "target_chain": chain_id,
                "hotspot_residues": hotspot_residues,
                "parameters": params,
                "validation_results": results,
                "estimated_cost_usd": 0,
                "rationale": "",
            }

            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO public.jobs (id, user_id, tool, status, job_spec, created_at)
                       VALUES ($1, $2::uuid, $3, 'draft', $4::jsonb, NOW())""",
                    job_id,
                    user_id,
                    tool,
                    json.dumps(job_spec),
                )
        except Exception as exc:
            # Job creation failed — log but don't block the validation
            # result. The ReviewCard renders without a launchable job_id;
            # the user sees the validation but the Launch button is dead
            # until the next call. Sentry capture is critical because
            # silent draft-job failures break the launch flow.
            logger.exception("validate_preflight: draft job creation failed for user=%s tool=%s",
                             user_id, tool)
            sentry_sdk.capture_exception(exc)
            job_id = None

    return json.dumps({
        "validation_results": results,
        "can_proceed": not has_fail,
        "has_warnings": has_warn,
        "summary": summary,
        "job_id": job_id,
    })
