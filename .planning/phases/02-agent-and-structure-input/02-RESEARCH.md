# Phase 2: Agent and Structure Input - Research

**Researched:** 2026-03-19
**Domain:** Claude tool-use agent, PDB normalization (BioPython), UniProt/RCSB REST APIs, React chat UI (shadcn/ui AI components), FastAPI SSE streaming
**Confidence:** HIGH (APIs verified against live endpoints and official docs)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Conversation UX**
- Chat interface (not wizard forms) — persistent chat thread where the agent controls the flow
- Inline action buttons AND free-text input — buttons for speed, text for nuance; agent handles both
- Structured review card before job launch — tool, target, key parameters, estimated cost; user clicks "Launch job" or "Edit"
- Session memory — agent remembers context within the current session; memory resets on page reload
- Inline file upload — drag-drop PDB files directly into chat input; no separate upload panel
- Typing indicator + status line during agent thinking ("Fetching structure from RCSB...", "Analyzing your design goal...")
- One active session at a time — previous session replaced when starting new one; job history is separate
- Agent greets first — opens with guidance on how to get started (describe goal, upload PDB, or paste accession)

**Structure Resolution**
- Natural language → UniProt search → PDB resolution path (UniProt API as source of truth, not direct PDB text search)
- Multi-PDB resolution: agent picks best structure (highest resolution, most complete) with plain-language explanation; user can override
- Chain selection: agent infers correct chain from design goal context, shows choice, asks confirmation; falls back to asking if ambiguous
- PDB storage: ephemeral, job-scoped only — not stored permanently in user's account
- Structure preview: summary card showing PDB ID, title, resolution, chain count, residue count, method
- PDB normalization: silent with summary — agent normalizes automatically, then reports what was changed (altloc, MSE, NMR model selection)

**Wizard Parameters**
- Essential parameters only (3-5 per tool) with Ranomics-curated smart defaults; no advanced settings in v1
- Tool-specific wizard questions — RFdiffusion, BindCraft, and Boltzgen each have their own tailored question set
- Adaptive grouping — agent groups related questions (e.g., chain length + number of designs) rather than asking one-by-one
- Brief rationale with each default — one sentence explaining why (encodes Ranomics domain expertise)

**Validation and Pre-flight**
- Tiered validation: hard blocks for critical issues (wrong format, empty chain, no target residues), warnings for minor issues (missing sidechains, low resolution) that user can acknowledge and proceed
- Hotspot validation: BioPython SASA surface accessibility check; flags buried residues with explanation
- Validation display: checklist card with pass/warn/fail icons per check
- JobSpec: structured JSON object stored in jobs table — tool, target PDB path, parameters, validation results, estimated cost; serves as the contract between Phase 2 (agent) and Phase 3 (dispatch)

### Claude's Discretion
- Exact chat message formatting and markdown usage
- Loading animation implementation details
- Error message wording for edge cases
- How to handle ambiguous natural language descriptions (when to ask for clarification vs. make a best guess)
- PDB normalization implementation details (which BioPython functions, error handling)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INPUT-01 | User can upload a PDB file as the target structure for a design job | python-multipart already in requirements.txt; FastAPI UploadFile pattern; BioPython PDBParser for validation |
| INPUT-02 | User can provide a PDB accession ID; system fetches structure from RCSB automatically | RCSB files.rcsb.org direct download URLs verified; mmCIF preferred format |
| INPUT-03 | User can provide a UniProt accession ID; system resolves it to PDB structure and fetches it | UniProt REST API `rest.uniprot.org/uniprotkb/{accession}` with xref_pdb fields |
| INPUT-04 | User can describe a target in natural language only; agent identifies and fetches the canonical structure | UniProt search API `rest.uniprot.org/uniprotkb/search?query=...&fields=accession,xref_pdb`; Claude tool use to execute the resolution chain |
| INPUT-05 | System normalizes all uploaded/fetched PDB files before use (NMR, insertion codes, altloc, MSE) | BioPython PDBParser + DisorderModel selection + PDBIO; MSE → MET mutation documented |
| AGENT-01 | Agent classifies user's design intent from natural language | Claude claude-sonnet-4-5 tool use with intent_classify tool; structured output with design_type enum |
| AGENT-02 | Agent recommends appropriate tool with rationale; user must explicitly confirm | Claude tool use; tool_choice="auto"; confirmation handled via inline action buttons in chat |
| AGENT-03 | Agent runs guided wizard to collect required parameters before launching | Multi-turn Claude conversation with tool use; wizard_questions tool per design tool type |
| AGENT-04 | Agent performs pre-flight validation on inputs | BioPython SASA (ShrakeRupley), PDB quality checks; validation_result structured output |
| AGENT-05 | Agent surfaces validation warnings and requires acknowledgment | Tiered result returned to frontend; hard-block vs. warn distinction in JobSpec |
</phase_requirements>

---

## Summary

Phase 2 builds two coupled subsystems: a Claude-powered agent conversation loop and a PDB ingest/normalization pipeline. These combine to produce a validated JobSpec JSON object that Phase 3 dispatches.

The agent subsystem uses the Anthropic Python SDK (v0.86.0, installed as 0.84.0) with multi-turn `messages.create` calls and tool use. The agent is not a one-shot call — it runs a stateful conversation loop across multiple turns, maintaining message history in the FastAPI session. The frontend communicates with the backend via a persistent POST/SSE pattern: the frontend POSTs a user message, the backend streams back agent responses token-by-token using FastAPI `StreamingResponse` with `text/event-stream`.

The PDB ingest pipeline has three entry points (file upload, PDB accession, UniProt accession or NL description) that all converge on a single normalization function. BioPython 1.86 covers all required normalization: model selection for NMR (pick model 0), altloc disambiguation (select highest-occupancy), MSE-to-MET mutation, insertion code handling, and SASA-based hotspot feasibility via `ShrakeRupley`. The UniProt REST API (`rest.uniprot.org`) provides the source-of-truth lookup for natural language → UniProt accession → PDB IDs, with Swiss-Prot reviewed entries preferred. The RCSB file download service (`files.rcsb.org`) is the canonical source for structure files, using direct URL downloads without a Python client library.

**Primary recommendation:** Build the agent as a stateful backend service that maintains `messages[]` history per session in Redis, streams responses via SSE, and uses Claude tool use with strictly defined tools (resolve_structure, classify_intent, collect_parameters, validate_preflight). The frontend renders responses with shadcn/ui AI components from `shadcn.io/ai`.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | 0.86.0 | Claude API: tool use, multi-turn conversation, streaming | Official SDK; installed 0.84.0 — update to 0.86.0 |
| biopython | 1.86 | PDB parsing, normalization, SASA calculation | Canonical scientific Python library for structural biology; already installed |
| httpx | 0.28.1 | Async HTTP for UniProt/RCSB API calls | Already in requirements.txt; async-native, matches FastAPI async pattern |
| redis | 7.3.0 | Session message history storage (messages[] per session) | Already in Docker Compose; prevents memory leaks from long chat sessions |
| python-multipart | 0.0.22 | PDB file upload via multipart/form-data | Already in requirements.txt; required for FastAPI UploadFile |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| shadcn/ui AI components | latest (shadcn.io/ai) | Chat UI primitives: Message, Conversation, PromptInput, Loader | Add via `npx shadcn@latest add` as needed for chat layout |
| pydantic v2 | (via fastapi) | JobSpec schema, tool input/output validation | All agent tool inputs and JobSpec validated as Pydantic models |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct `messages.create` tool loop | Claude Agent SDK (query function) | Agent SDK is designed for autonomous agent tasks, not guided wizard flows where the human must confirm each step; direct tool use gives fine-grained control |
| shadcn/ui AI components | Custom chat components | shadcn/ui AI is purpose-built for this pattern; custom components waste time on scroll behavior, typing indicators, file drop zones |
| RCSB files.rcsb.org direct download | rcsb-api Python client | Direct URL downloads have no rate limiting per RCSB docs; the Python client adds a dependency without benefit for a simple fetch-by-ID use case |
| Redis session storage | In-process dict / database | Redis already deployed; database is overkill for ephemeral chat state; in-process dict doesn't survive container restart |

**Installation (new packages only — all others already in requirements.txt):**
```bash
pip install anthropic==0.86.0
# biopython, httpx, redis, python-multipart already pinned in requirements.txt
```

**Version verification:**
```
anthropic: 0.86.0 (verified from GitHub releases page, 2026-03-18)
biopython: 1.86 (verified via pip index versions)
```

---

## Architecture Patterns

### Recommended Project Structure
```
backend/
├── agent/
│   ├── router.py          # FastAPI router: POST /agent/message, GET /agent/stream/{session_id}
│   ├── session.py         # Redis-backed session: load/save messages[] history
│   ├── tools.py           # Claude tool definitions (JSON schema) + Python handlers
│   ├── wizard.py          # Tool-specific wizard question sets per design tool
│   └── jobspec.py         # JobSpec Pydantic model; serialization to jobs table JSONB
├── pdb/
│   ├── router.py          # FastAPI router: POST /pdb/upload, GET /pdb/{pdb_id}
│   ├── fetch.py           # RCSB download + UniProt search/resolve
│   ├── normalize.py       # BioPython normalization pipeline
│   └── validate.py        # Pre-flight checks: SASA hotspot, quality, parameter sanity
supabase/
└── migrations/
    └── 20260319000001_jobspec.sql  # jobs table: add job_spec JSONB, pdb_path TEXT columns
frontend/src/
├── components/
│   └── chat/
│       ├── ChatPage.tsx         # Full-width layout (different from AuthLayout centered card)
│       ├── ChatInput.tsx        # PromptInput with PDB drag-drop
│       ├── MessageList.tsx      # Conversation component with auto-scroll
│       ├── AgentMessage.tsx     # Assistant bubble with structured card rendering
│       ├── ReviewCard.tsx       # Pre-launch confirmation card
│       └── ValidationCard.tsx   # Checklist card: pass/warn/fail per check
└── lib/
    └── agent.ts                 # Typed API calls for agent endpoints + SSE EventSource
```

### Pattern 1: Multi-Turn Tool Use Loop (Backend)

**What:** The agent backend maintains `messages[]` in Redis per session. On each user message, it appends the user turn, calls `client.messages.create` with the tool set, executes any requested tools, appends the `tool_result`, and calls again until `stop_reason == "end_turn"`.

**When to use:** Every agent response cycle.

```python
# Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use
# Pattern: backend/agent/router.py

async def run_agent_turn(session_id: str, user_message: str) -> str:
    """Run one user turn through the Claude tool-use loop.

    Loads message history from Redis, appends user message, runs tool
    loop until end_turn, saves updated history, returns final text.
    """
    messages = await session.load(session_id)
    messages.append({"role": "user", "content": user_message})

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=AGENT_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})

    await session.save(session_id, messages)
    # Extract final text from response.content
    return next(b.text for b in response.content if hasattr(b, "text"))
```

### Pattern 2: FastAPI SSE Streaming Response

**What:** Stream agent responses token-by-token to the frontend using `StreamingResponse` with `text/event-stream`. This drives the typing indicator without requiring WebSockets.

**When to use:** Every `/agent/message` response.

```python
# Source: https://fastapi.tiangolo.com/tutorial/server-sent-events/
# Pattern: backend/agent/router.py

from fastapi.responses import StreamingResponse

@router.post("/agent/message")
async def agent_message(req: AgentMessageRequest, user=Depends(get_current_user)):
    """Stream agent response as SSE events."""

    async def event_generator():
        # Emit status events during tool execution
        yield f"data: {json.dumps({'type': 'status', 'text': 'Thinking...'})}\n\n"

        async with client.messages.stream(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=AGENT_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield f"data: {json.dumps({'type': 'text', 'text': text})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Pattern 3: BioPython PDB Normalization Pipeline

**What:** A single normalization function that accepts a `Structure` object and returns a cleaned structure along with a summary of changes made. Called after every PDB ingest regardless of source.

**When to use:** After every PDB parse (upload, RCSB fetch, UniProt-resolved fetch).

```python
# Source: https://biopython.org/docs/latest/Tutorial/chapter_pdb.html
# Pattern: backend/pdb/normalize.py

from Bio.PDB import PDBParser, PDBIO, Select
from Bio.PDB.Polypeptide import is_aa

def normalize_structure(pdb_path: str) -> tuple[str, dict]:
    """Normalize a PDB file and return (output_path, change_summary).

    Handles: NMR multi-model (keep model 0), altloc (keep highest occupancy,
    which BioPython DisorderedAtom does by default), MSE→MET residue mutation,
    insertion codes (preserved in residue IDs — no normalization needed).

    Args:
        pdb_path: Path to raw PDB file.

    Returns:
        Tuple of (normalized_pdb_path, {changes: [str]}).

    Raises:
        ValueError: If structure contains no standard amino acid residues.
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("target", pdb_path)
    changes = []

    # NMR: select only the first model
    models = list(structure.get_models())
    if len(models) > 1:
        changes.append(f"NMR structure: selected model 1 of {len(models)}")
        # PDBIO with model 0 selector handles this

    # MSE (selenomethionine) → MET mutation
    mse_count = 0
    for residue in structure.get_residues():
        if residue.get_resname() == "MSE":
            residue.resname = "MET"
            for atom in residue:
                if atom.get_name() == "SE":
                    atom.name = "SD"
                    atom.element = "S"
            mse_count += 1
    if mse_count:
        changes.append(f"Converted {mse_count} MSE (selenomethionine) residues to MET")

    # altloc: BioPython DisorderedAtom.disordered_select automatically forwards
    # calls to highest-occupancy atom — no explicit action needed unless we want
    # to strip all non-A altlocs from the output file.

    return output_path, {"changes": changes}
```

### Pattern 4: UniProt → PDB Resolution Chain

**What:** Three-step resolution: search UniProt by text, rank results by Swiss-Prot reviewed status + completeness, extract PDB cross-references, score structures by resolution, return ranked list.

**When to use:** INPUT-03 (UniProt accession) and INPUT-04 (natural language description).

```python
# Source: UniProt REST API verified via live endpoint 2026-03-19
# Pattern: backend/pdb/fetch.py

async def search_uniprot(query: str, http_client: httpx.AsyncClient) -> list[dict]:
    """Search UniProt by protein name, return ranked entries with PDB accessions.

    Args:
        query: Free-text protein name or UniProt accession.

    Returns:
        List of dicts with keys: uniprot_accession, protein_name, pdb_ids (sorted by resolution).
    """
    url = "https://rest.uniprot.org/uniprotkb/search"
    params = {
        "query": f"({query}) AND reviewed:true",
        "fields": "accession,protein_name,xref_pdb",
        "format": "json",
        "size": 5,
    }
    response = await http_client.get(url, params=params)
    response.raise_for_status()
    return response.json()["results"]


async def fetch_pdb_file(pdb_id: str, http_client: httpx.AsyncClient) -> bytes:
    """Download a PDB structure file from RCSB.

    Args:
        pdb_id: 4-character PDB accession (e.g. '4ZS7').

    Returns:
        Raw bytes of the mmCIF file.

    Raises:
        httpx.HTTPStatusError: If the PDB ID is not found (404).
    """
    # mmCIF preferred; legacy PDB format to be discontinued with extended PDB IDs
    url = f"https://files.rcsb.org/download/{pdb_id.upper()}.cif"
    response = await http_client.get(url)
    response.raise_for_status()
    return response.content
```

### Pattern 5: SASA Hotspot Feasibility Check

**What:** Compute per-residue SASA for user-specified hotspot residues. Flag residues with SASA < threshold (buried) as infeasible binder contact points.

**When to use:** AGENT-04, whenever hotspot residues are provided.

```python
# Source: https://biopython.org/docs/dev/api/Bio.PDB.SASA.html
# Pattern: backend/pdb/validate.py

from Bio.PDB.SASA import ShrakeRupley

def check_hotspot_accessibility(
    structure, chain_id: str, residue_numbers: list[int], sasa_threshold: float = 1.0
) -> list[dict]:
    """Check whether hotspot residues are surface-accessible.

    Args:
        structure: Bio.PDB Structure object (normalized).
        chain_id: Chain containing the hotspot residues.
        residue_numbers: List of residue sequence numbers.
        sasa_threshold: Minimum per-residue SASA (Å²) to be considered accessible.
            Default 1.0 Å² is conservative; buried residues typically < 0.5 Å².

    Returns:
        List of dicts: {residue_number, residue_name, sasa, accessible: bool, warning: str|None}
    """
    sr = ShrakeRupley()
    sr.compute(structure, level="R")  # per-residue SASA

    chain = structure[0][chain_id]
    results = []
    for resnum in residue_numbers:
        try:
            residue = chain[(" ", resnum, " ")]
            sasa = residue.sasa
            accessible = sasa >= sasa_threshold
            results.append({
                "residue_number": resnum,
                "residue_name": residue.get_resname(),
                "sasa": round(sasa, 2),
                "accessible": accessible,
                "warning": None if accessible else f"Residue {resnum} appears buried (SASA={sasa:.1f} Å²); may not be a productive binder contact point",
            })
        except KeyError:
            results.append({
                "residue_number": resnum,
                "residue_name": "UNKNOWN",
                "sasa": 0.0,
                "accessible": False,
                "warning": f"Residue {resnum} not found in chain {chain_id}",
            })
    return results
```

### Pattern 6: JobSpec Pydantic Model

**What:** A typed Pydantic model that represents the complete contract between the agent (Phase 2) and the job dispatch layer (Phase 3). Stored as JSONB in the `jobs.job_spec` column.

```python
# Pattern: backend/agent/jobspec.py

from pydantic import BaseModel
from typing import Literal

class ValidationResult(BaseModel):
    check_name: str
    status: Literal["pass", "warn", "fail"]
    message: str

class JobSpec(BaseModel):
    tool: Literal["rfdiffusion", "bindcraft", "boltzgen"]
    target_pdb_path: str           # MinIO path: users/{uid}/jobs/{jid}/inputs/target.cif
    target_chain: str              # e.g. "A"
    hotspot_residues: list[int]    # e.g. [45, 48, 52]
    parameters: dict               # tool-specific; validated per tool in wizard.py
    validation_results: list[ValidationResult]
    estimated_cost_usd: float
    rationale: str                 # plain-language explanation of tool choice
```

### Anti-Patterns to Avoid

- **Storing full message history in the jobs table:** The `messages[]` array for Claude conversations can become large. Store it in Redis (ephemeral, session-scoped), not Postgres. Only the final JobSpec goes to Postgres.
- **Calling UniProt search API on every chat turn:** Cache UniProt and RCSB responses in Redis with a short TTL (e.g., 1 hour). Repeated calls for the same protein during a session will hit the cache.
- **Using `tool_choice="any"` for all turns:** Only force tool use when you explicitly need a structured output (e.g., the intent classification turn). For conversational turns, use `tool_choice="auto"` to allow Claude to respond naturally.
- **Blocking the FastAPI event loop on BioPython SASA:** ShrakeRupley computation is CPU-bound. Run it with `asyncio.get_event_loop().run_in_executor(None, compute_fn)` to avoid blocking other requests.
- **Passing raw PDB bytes to Claude as text:** PDB files are large (100+ KB), consume tokens, and Claude cannot parse structural geometry. Always run the BioPython pipeline server-side and pass structured summaries (chain count, residue count, resolution) to the agent context.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PDB file parsing | Custom parser | `Bio.PDB.PDBParser` + `MMCIFParser` | PDB format has 50+ edge cases (ANISOU, SSBOND, LINK records, extended PDB IDs); BioPython handles them all |
| SASA calculation | Geometric sphere packing code | `Bio.PDB.SASA.ShrakeRupley` | Shrake-Rupley requires correct van der Waals radii, probe rolling, and atom overlap handling — non-trivial to implement correctly |
| altloc disambiguation | Custom atom selection | BioPython `DisorderedAtom` default (highest occupancy) | Default behavior is already correct; hand-rolled logic will miss edge cases like equal-occupancy atoms |
| Chat message streaming | Custom WebSocket server | FastAPI `StreamingResponse` + `text/event-stream` | SSE is unidirectional (server → client) which is all that's needed; simpler than WebSocket, works over HTTP/1.1, reconnects automatically |
| Chat UI components | Custom React chat layout | shadcn/ui AI component registry (`shadcn.io/ai`) | Auto-scroll, typing indicators, file attachment zones, and message bubble patterns are all solved; copy-paste ownership model matches project style |
| UniProt text search | Elasticsearch clone | `rest.uniprot.org/uniprotkb/search` | UniProt's own search index handles synonym resolution, organism filtering, and review-status ranking; reproducing this is months of work |

**Key insight:** In structural bioinformatics, nearly every "simple" parsing task conceals specification complexity. BioPython has 20+ years of community-contributed edge case handling for PDB formats. The same applies to the UniProt/RCSB APIs — the external services have already solved the hard data-quality problems.

---

## Common Pitfalls

### Pitfall 1: UniProt Returns Many-to-Many PDB Matches

**What goes wrong:** A single UniProt accession (e.g., P08887 for IL-6R) has 15+ PDB cross-references at varying resolutions, completeness, and experimental methods. Picking the "best" one requires scoring logic, not just taking the first result.

**Why it happens:** The PDB contains structures from many labs at different resolutions, with different bound partners, crystal forms, and chain completeness.

**How to avoid:** Score PDB cross-references by: (1) experimental method priority (X-ray > EM > NMR), (2) resolution (lower Å is better), (3) chain completeness (% of UniProt sequence covered). Present the top result with a plain-language explanation and allow the user to override.

**Warning signs:** If the agent always picks the first cross-reference, it may select an NMR structure (model ensemble) over a 1.5 Å X-ray structure.

### Pitfall 2: Insertion Code Residue Numbering Breaks SASA Lookups

**What goes wrong:** A residue at PDB position `100A` (insertion code "A") is accessed in BioPython as `chain[(" ", 100, "A")]`, not `chain[100]`. The user specifies hotspot residues as plain integers. The lookup `chain[(" ", 100, " ")]` will fail silently or raise `KeyError` if the structure has insertion code residues near the hotspot.

**Why it happens:** PDB residue IDs are tuples `(hetfield, resseq, icode)`. BioPython's shorthand `chain[100]` only works when `icode == " "`.

**How to avoid:** In `check_hotspot_accessibility`, build a mapping of `resseq → full_residue_id` for all standard amino acids in the chain before attempting lookups. If multiple residues share the same `resseq` (due to insertion codes), surface an explicit warning.

**Warning signs:** `KeyError` on residue lookup despite the residue visually present in the PDB file.

### Pitfall 3: Claude Tool Use History Must Be Exact

**What goes wrong:** When reconstructing the message history from Redis for a session, any malformed `tool_result` message causes Claude to return a 400 error. The most common mistake is sending the `tool_result` block inside the wrong role, or sending text before tool results in a user message.

**Why it happens:** The Anthropic API requires that in any user-role message containing `tool_result` blocks, the tool_result blocks must appear FIRST in the content array, before any text blocks.

**How to avoid:** Always construct tool result messages as:
```python
{"role": "user", "content": [{"type": "tool_result", "tool_use_id": id, "content": result}]}
```
Never append text to the same message as a `tool_result`. Validate message history shape before API calls.

**Warning signs:** `400 BadRequest` or `invalid_request_error` from the Anthropic API after the first tool call.

### Pitfall 4: MSE Residues Not Recognized as Standard Amino Acids

**What goes wrong:** Many selenomethionine-labelled crystal structures use MSE (HETATM) instead of MET. BioPython's `is_aa()` and standard chain iteration return False/skip MSE residues. The normalized structure appears truncated or the SASA calculation is incorrect.

**Why it happens:** MSE is classified as a HETATM in PDB format, not ATOM. BioPython's residue classification follows this distinction.

**How to avoid:** Apply the MSE → MET mutation (rename residue + SE → SD atom) in the normalization step before any downstream analysis. Document in the normalization summary.

**Warning signs:** Chain residue count substantially lower than expected; gaps at methionine positions in sequence alignment.

### Pitfall 5: Claude Context Window in Long Sessions

**What goes wrong:** The `messages[]` history grows with each agent turn, each tool call, and each tool result. A single session with structure preview, multi-step wizard, and validation can easily accumulate 20,000+ tokens. The context window (200K for claude-sonnet-4-5) is rarely the issue, but token cost per turn compounds quickly.

**Why it happens:** Full message history is replayed on every API call. Tool results can be verbose (e.g., full PDB metadata JSON).

**How to avoid:** Keep tool results compact. Return structured summaries (dict with 5-10 fields) rather than raw API responses. The STATE.md blocker about summarization vs. truncation policy can be resolved with: truncate only if history exceeds 150K tokens, keeping the system prompt + first 3 messages + last N messages. For v1 with 3-5 wizard steps, this limit will not be reached.

**Warning signs:** Latency increases noticeably per turn in a session; cost per session exceeds estimate by 2-3×.

---

## Code Examples

Verified patterns from official sources:

### Claude Tool Definition Format
```python
# Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use
TOOL_DEFINITIONS = [
    {
        "name": "resolve_structure",
        "description": (
            "Fetch a target protein structure from RCSB PDB or UniProt. "
            "Use when the user provides a PDB accession (4 characters, e.g. '4ZS7'), "
            "a UniProt accession (6-10 characters, e.g. 'P08887'), or a plain protein name. "
            "Do NOT use for user-uploaded PDB files — those are handled separately. "
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
            "Classify the user's protein design intent into one of three categories: "
            "binder_design (designing a protein that binds a target), "
            "de_novo_backbone (designing a new protein backbone from scratch), or "
            "motif_scaffolding (embedding a functional motif into a new scaffold). "
            "Use after the target structure is resolved and the user has described their goal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_type": {
                    "type": "string",
                    "enum": ["binder_design", "de_novo_backbone", "motif_scaffolding"],
                },
                "recommended_tool": {
                    "type": "string",
                    "enum": ["rfdiffusion", "bindcraft", "boltzgen"],
                },
                "rationale": {
                    "type": "string",
                    "description": "One plain-language sentence explaining the tool recommendation.",
                },
            },
            "required": ["design_type", "recommended_tool", "rationale"],
        },
    },
]
```

### UniProt REST Search (Verified Live Endpoint)
```python
# Source: live endpoint test 2026-03-19
# GET https://rest.uniprot.org/uniprotkb/search?query=(il6r)+AND+reviewed:true&fields=accession,protein_name,xref_pdb&format=json&size=5

import httpx

async def uniprot_search(query: str, client: httpx.AsyncClient) -> list[dict]:
    """Search UniProt for reviewed entries matching query, return with PDB cross-refs."""
    response = await client.get(
        "https://rest.uniprot.org/uniprotkb/search",
        params={
            "query": f"({query}) AND reviewed:true",
            "fields": "accession,protein_name,xref_pdb",
            "format": "json",
            "size": 5,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("results", [])

# Response shape per entry (verified):
# {
#   "primaryAccession": "P08887",
#   "proteinDescription": {"recommendedName": {"fullName": {"value": "Interleukin-6 receptor subunit alpha"}}},
#   "uniProtKBCrossReferences": [
#     {"database": "PDB", "id": "1N26", "properties": [
#       {"key": "Method", "value": "X-ray"},
#       {"key": "Resolution", "value": "2.40 A"},
#       {"key": "Chains", "value": "A=20-344"}
#     ]}
#   ]
# }
```

### RCSB Structure File Download (Verified)
```python
# Source: https://www.rcsb.org/docs/programmatic-access/file-download-services
# mmCIF format preferred; legacy PDB URL format will be discontinued with extended PDB IDs

async def fetch_structure_cif(pdb_id: str, client: httpx.AsyncClient) -> bytes:
    """Download mmCIF structure file for a given PDB accession."""
    url = f"https://files.rcsb.org/download/{pdb_id.upper()}.cif"
    response = await client.get(url, timeout=30.0)  # large files; longer timeout
    response.raise_for_status()
    return response.content
```

### Frontend SSE EventSource Pattern
```typescript
// Pattern: frontend/src/lib/agent.ts
// Source: https://developer.mozilla.org/en-US/docs/Web/API/EventSource

export function streamAgentMessage(
  sessionId: string,
  onText: (chunk: string) => void,
  onStatus: (status: string) => void,
  onDone: () => void
): EventSource {
  const es = new EventSource(`/api/agent/stream/${sessionId}`, {
    withCredentials: true,  // send HTTP-only auth cookies
  });

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "text") onText(data.text);
    if (data.type === "status") onStatus(data.text);
    if (data.type === "done") { onDone(); es.close(); }
  };

  es.onerror = () => es.close();
  return es;
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| LangChain agent orchestration | Direct Anthropic SDK tool use loop | 2024 | Fewer abstractions, easier debugging, no framework lock-in |
| PDB legacy format for downloads | mmCIF (PDBx/CIF) as primary | 2023 (ongoing migration) | PDB IDs will exceed 4 characters; legacy PDB format URLs being discontinued |
| DSSP for SASA (requires external binary) | `Bio.PDB.SASA.ShrakeRupley` (pure Python) | BioPython 1.79 | No system dependency; simpler deployment in Docker container |
| Separate UniProt ID mapping service | Direct UniProt REST search with `reviewed:true` filter | 2022 (REST API v2) | Single API call instead of two-step lookup |
| shadcn/ui v2/v3 component patterns | shadcn/ui v4 (Tailwind v4 required) | 2024 | Already on Tailwind v4 per Phase 1; no migration needed |

**Deprecated/outdated:**
- `Bio.PDB.DSSP`: Requires external DSSP binary; use `ShrakeRupley` for SASA instead
- `requests` library for API calls: Project uses `httpx` (async); do not add `requests` as a dependency
- UniProt legacy API (`www.uniprot.org/uniprot?format=tab`): Use `rest.uniprot.org` (REST v2) — old API endpoint deprecated
- `python-jose`: Already excluded in Phase 1 (unmaintained); do not introduce for any JWT handling

---

## Open Questions

1. **NMR structure quality for binder design**
   - What we know: NMR structures are multi-model ensembles; model 0 is selected by normalization
   - What's unclear: Whether NMR structures should be a hard block or a warning for binder design tools (RFdiffusion and BindCraft are trained primarily on X-ray structures)
   - Recommendation: Treat NMR as a warning, not a hard block; surface the warning in the validation card with text like "NMR ensemble — using model 1; X-ray structures may give better results"

2. **Claude model selection for agent**
   - What we know: STATE.md references `Anthropic SDK 0.43.x + Instructor 1.x` as a pending decision; actual installed SDK is 0.84.0; no Instructor installed
   - What's unclear: Whether Instructor is still intended — Instructor is used for structured output via Pydantic, but Claude's native tool use with JSON Schema achieves the same result
   - Recommendation: Use native tool use (no Instructor dependency); model `claude-sonnet-4-5` for the agent (balance of capability and cost); avoid Instructor to keep dependencies minimal

3. **Session ID and auth linkage**
   - What we know: Session memory resets on page reload (locked decision); one active session per user
   - What's unclear: How the session_id is generated and whether it needs to be tied to the authenticated user_id to prevent session hijacking
   - Recommendation: Generate session_id as a UUID; store in Redis as `session:{user_id}:{session_id}` keyed under user_id; validate that the authenticated user_id matches the session prefix before loading history

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Backend framework | pytest 8.3.5 + pytest-asyncio 0.24.0 (asyncio_mode = auto) |
| Backend config file | `backend/pytest.ini` |
| Frontend framework | vitest 4.x (jsdom environment) |
| Frontend config | `frontend/vite.config.ts` (test section) |
| Quick run (backend) | `cd backend && pytest tests/ -x -q` |
| Quick run (frontend) | `cd frontend && npx vitest run` |
| Full suite | `cd backend && pytest tests/ && cd ../frontend && npx vitest run` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INPUT-01 | PDB file upload accepted, parsed without error | unit | `pytest tests/pdb/test_normalize.py::test_upload_valid_pdb -x` | ❌ Wave 0 |
| INPUT-02 | PDB accession fetch returns valid CIF bytes | integration | `pytest tests/pdb/test_fetch.py::test_fetch_by_pdb_id -x` | ❌ Wave 0 |
| INPUT-03 | UniProt accession resolves to PDB entries | integration | `pytest tests/pdb/test_fetch.py::test_uniprot_to_pdb -x` | ❌ Wave 0 |
| INPUT-04 | NL query "IL-6 receptor" returns reviewed UniProt entry | integration | `pytest tests/pdb/test_fetch.py::test_nl_to_pdb -x` | ❌ Wave 0 |
| INPUT-05 | MSE residues → MET, NMR model 0 selected, altloc handled | unit | `pytest tests/pdb/test_normalize.py::test_mse_conversion -x` | ❌ Wave 0 |
| AGENT-01 | Intent classification tool returns valid design_type | unit | `pytest tests/agent/test_tools.py::test_classify_intent -x` | ❌ Wave 0 |
| AGENT-02 | Tool recommendation includes rationale string | unit | `pytest tests/agent/test_tools.py::test_tool_recommendation -x` | ❌ Wave 0 |
| AGENT-03 | Wizard collects required parameters, produces JobSpec | integration | `pytest tests/agent/test_session.py::test_wizard_completion -x` | ❌ Wave 0 |
| AGENT-04 | Buried hotspot residue (SASA < 1.0) flagged as warn | unit | `pytest tests/pdb/test_validate.py::test_buried_hotspot -x` | ❌ Wave 0 |
| AGENT-05 | Validation result with warn status blocks dispatch | unit | `pytest tests/agent/test_jobspec.py::test_warn_blocks_dispatch -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `cd backend && pytest tests/ -x -q`
- **Per wave merge:** `cd backend && pytest tests/ && cd ../frontend && npx vitest run`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `backend/tests/pdb/__init__.py` + `backend/tests/pdb/test_normalize.py` — covers INPUT-01, INPUT-05
- [ ] `backend/tests/pdb/test_fetch.py` — covers INPUT-02, INPUT-03, INPUT-04; requires `respx` (httpx mock library) for mocking RCSB/UniProt calls
- [ ] `backend/tests/pdb/test_validate.py` — covers AGENT-04; requires a test PDB fixture file
- [ ] `backend/tests/agent/__init__.py` + `backend/tests/agent/test_tools.py` — covers AGENT-01, AGENT-02; mock Anthropic client responses
- [ ] `backend/tests/agent/test_session.py` — covers AGENT-03; requires Redis test fixture
- [ ] `backend/tests/agent/test_jobspec.py` — covers AGENT-05
- [ ] `backend/tests/conftest.py` update — add Redis mock fixture and test PDB fixture file path
- [ ] Framework install: `pip install respx` — httpx mock library needed for external API tests

---

## Sources

### Primary (HIGH confidence)
- Anthropic tool use docs — `https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use` — tool definitions, tool_use response format, tool_result message format, multi-turn loop pattern
- BioPython PDB tutorial — `https://biopython.org/docs/latest/Tutorial/chapter_pdb.html` — altloc, insertion codes, NMR model selection, HETATM residue handling
- BioPython SASA module docs — `https://biopython.org/docs/dev/api/Bio.PDB.SASA.html` — ShrakeRupley API, compute() method, per-residue access pattern
- RCSB file download services — `https://www.rcsb.org/docs/programmatic-access/file-download-services` — mmCIF and PDB URL patterns, rate limit policy
- UniProt REST API (live endpoint) — `https://rest.uniprot.org/uniprotkb/search` — verified response shape for accession, protein_name, xref_pdb fields
- Anthropic Python SDK GitHub — `https://github.com/anthropics/anthropic-sdk-python` — current version 0.86.0, streaming interface
- shadcn/ui AI components — `https://www.shadcn.io/ai` — available chat UI components and their features

### Secondary (MEDIUM confidence)
- RCSB PDB Data API overview — `https://data.rcsb.org/` — data API endpoint structure (metadata retrieval pattern)
- FastAPI SSE tutorial — `https://fastapi.tiangolo.com/tutorial/server-sent-events/` — StreamingResponse pattern with text/event-stream
- UniProt API queries help — `https://www.uniprot.org/help/api_queries` — query syntax including reviewed:true filter

### Tertiary (LOW confidence)
- UniProt coding biologist article — `https://thecodingbiologist.com/posts/Accessing-UniProt-via-its-REST-API` — older API URL format; use `rest.uniprot.org` (v2) not `www.uniprot.org/uniprot` (legacy)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified from npm registry, PyPI, and GitHub releases page
- Architecture: HIGH — all API patterns verified from official docs and live endpoints
- Pitfalls: HIGH — insertion code and MSE pitfalls from BioPython source and FAQ; Claude message history requirement from official API docs
- Test map: MEDIUM — test file paths are proposed (Wave 0 gaps), not yet created

**Research date:** 2026-03-19
**Valid until:** 2026-04-19 (stable APIs; BioPython SASA API stable since 1.79; Anthropic SDK tool use format stable since claude-3 launch)
