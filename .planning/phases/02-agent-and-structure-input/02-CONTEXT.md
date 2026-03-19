# Phase 2: Agent and Structure Input - Context

**Gathered:** 2026-03-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the Claude-powered agent chat interface and PDB ingest pipeline. A scientist can describe a protein design goal in natural language, provide a target structure by any supported method (PDB upload, PDB accession, UniProt accession, or text description), and receive a validated JobSpec ready to dispatch — without touching a config file.

Requirements covered: INPUT-01 through INPUT-05, AGENT-01 through AGENT-05.

</domain>

<decisions>
## Implementation Decisions

### Conversation UX
- Chat interface (not wizard forms) — persistent chat thread where the agent controls the flow
- Inline action buttons AND free-text input — buttons for speed, text for nuance; agent handles both
- Structured review card before job launch — tool, target, key parameters, estimated cost; user clicks "Launch job" or "Edit"
- Session memory — agent remembers context within the current session; memory resets on page reload
- Inline file upload — drag-drop PDB files directly into chat input; no separate upload panel
- Typing indicator + status line during agent thinking ("Fetching structure from RCSB...", "Analyzing your design goal...")
- One active session at a time — previous session replaced when starting new one; job history is separate
- Agent greets first — opens with guidance on how to get started (describe goal, upload PDB, or paste accession)

### Structure Resolution
- Natural language → UniProt search → PDB resolution path (UniProt API as source of truth, not direct PDB text search)
- Multi-PDB resolution: agent picks best structure (highest resolution, most complete) with plain-language explanation; user can override
- Chain selection: agent infers correct chain from design goal context, shows choice, asks confirmation; falls back to asking if ambiguous
- PDB storage: ephemeral, job-scoped only — not stored permanently in user's account
- Structure preview: summary card showing PDB ID, title, resolution, chain count, residue count, method
- PDB normalization: silent with summary — agent normalizes automatically, then reports what was changed (altloc, MSE, NMR model selection)

### Wizard Parameters
- Essential parameters only (3-5 per tool) with Ranomics-curated smart defaults; no advanced settings in v1
- Tool-specific wizard questions — RFdiffusion, BindCraft, and Boltzgen each have their own tailored question set
- Adaptive grouping — agent groups related questions (e.g., chain length + number of designs) rather than asking one-by-one
- Brief rationale with each default — one sentence explaining why (encodes Ranomics domain expertise)

### Validation & Pre-flight
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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context
- `.planning/PROJECT.md` — Core value, constraints, key decisions (Claude as LLM, GPU provider abstraction)
- `.planning/REQUIREMENTS.md` — INPUT-01 through INPUT-05, AGENT-01 through AGENT-05 acceptance criteria

### Phase 1 outputs (integration points)
- `.planning/phases/01-foundation/01-01-SUMMARY.md` — Dev environment: Supabase, Docker Compose, backend config
- `.planning/phases/01-foundation/01-02-SUMMARY.md` — FastAPI auth backend: router pattern, JWT validation, CSRF
- `.planning/phases/01-foundation/01-03-SUMMARY.md` — Frontend scaffold: React + shadcn/ui, API client, dark theme
- `.planning/phases/01-foundation/01-04-SUMMARY.md` — Auth screens: routing, hash redirect handler, verification fixes

### External APIs (research needed)
- UniProt REST API — for natural language → protein accession resolution
- RCSB PDB REST API — for PDB accession → structure file fetching
- Anthropic Claude API — for agent tool use, structured outputs, scientific reasoning
- BioPython PDB module — for PDB parsing, normalization, SASA calculation

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/auth/router.py` — FastAPI router pattern with Pydantic request/response models; same pattern for agent endpoints
- `backend/config.py` — Pydantic Settings; add Claude API key, UniProt/RCSB base URLs here
- `frontend/src/lib/api.ts` — Typed API client with cookie auth; extend for agent chat endpoints
- `frontend/src/components/ui/` — shadcn button, input, card, form components; use card for review/validation cards
- `frontend/src/components/auth/AuthLayout.tsx` — Centered layout pattern; chat interface will need a different full-width layout

### Established Patterns
- FastAPI + Pydantic for request validation and typed responses
- React + TypeScript with shadcn/ui components and Tailwind v4 (oklch colors)
- HTTP-only cookie auth with CSRF protection (sensitive_cookies pattern)
- Docker Compose for local dev services

### Integration Points
- `frontend/src/App.tsx` — Add chat route (e.g., `/` or `/design`) as the main authenticated landing page
- `backend/main.py` — Add agent router alongside auth router
- `docker-compose.yml` — No new services needed for Phase 2; Claude API is external
- `supabase/migrations/` — May need to extend jobs table schema for JobSpec storage
- `.env.local` — Add ANTHROPIC_API_KEY

</code_context>

<specifics>
## Specific Ideas

- The chat should feel like talking to a knowledgeable scientist colleague, not a chatbot
- Review card before launch is the critical trust-building moment — user needs to see everything the agent understood before GPU costs are incurred
- Ranomics expertise is encoded in the defaults and brief rationales, not in lengthy explanations
- PDB normalization summary builds trust by showing the system is doing the right things silently

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-agent-and-structure-input*
*Context gathered: 2026-03-18*
