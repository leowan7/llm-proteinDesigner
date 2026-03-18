# LLM Protein Designer

## What This Is

A SaaS web application that lets biotech scientists design proteins using natural language. Users describe their design goal in plain text; a Claude-powered agent interprets the request, selects the appropriate design tool (RFdiffusion, BindCraft, or Boltzgen), walks the user through a guided parameter wizard, then launches the job on cloud GPU infrastructure and returns ranked design candidates with structures, scores, and next-step guidance.

## Core Value

A scientist should be able to go from "I want to design a binder for IL-6 receptor" to downloadable, scored PDB structures without writing a single config file.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] User can describe a protein design goal in natural language
- [ ] Agent recommends the appropriate tool (RFdiffusion / BindCraft / Boltzgen) with rationale; user confirms
- [ ] Agent guides user through a parameter wizard (chain length, number of designs, binding site, etc.) before launching
- [ ] User can provide inputs via PDB file upload, UniProt/PDB accession, or text description alone
- [ ] Agent fetches structure from PDB/UniProt when accession is provided
- [ ] Job is dispatched to cloud GPU (Modal or RunPod via abstract provider interface)
- [ ] User can monitor job status in real time
- [ ] Completed job returns ranked PDB structure files for download
- [ ] Completed job returns a design report (parameters, scores, ranked candidates)
- [ ] Completed job includes interactive 3D structure viewer
- [ ] Agent provides next-step guidance after job completion (e.g. AlphaFold validation, synthesis ordering)
- [ ] Users authenticate and have accounts
- [ ] Billing is pay-per-job (priced by GPU compute time)

### Out of Scope

- Mobile-native app — web-first; responsive is sufficient
- Self-hosted GPU (user-owned clusters) — not in v1
- AlphaFold2/AF3 integration as a design tool — may surface as a validation step only
- Real-time collaboration / shared workspaces — defer to v2
- Sequence-only inputs (no structure) — too ambiguous for v1; require structure source or PDB ID

## Context

- Built and operated by Ranomics; target market is the broader biotech and biopharma community
- All three supported tools (RFdiffusion, BindCraft, Boltzgen) require GPU; typical runtimes 30 min–2 hrs
- GPU provider layer must be abstracted to support both Modal (serverless, Python-native) and RunPod (dedicated pods, cost-efficient for long runs)
- LLM layer is Claude via Anthropic API (tool use, structured outputs, scientific reasoning)
- Ranomics team has deep domain expertise in all three design tools — the agent's parameter defaults and wizard questions should encode that expertise

## Constraints

- **Tech stack**: Python backend (FastAPI or similar), Claude API for agent layer, abstract GPU provider interface
- **GPU runtime**: Jobs can run 30 min–2 hrs; async job handling is mandatory
- **Billing**: Stripe or equivalent for pay-per-job; must track GPU cost per job to set pricing
- **Security**: User-uploaded PDB files and generated structures may be proprietary; data isolation between accounts is required

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Claude as LLM backend | Strong tool use, structured outputs, scientific reasoning; aligned with Ranomics stack | — Pending |
| Abstract GPU provider interface | Avoid vendor lock-in between Modal and RunPod; allows cost optimization as usage scales | — Pending |
| Guided wizard over full abstraction | Advanced users want visibility into parameters; wizard balances accessibility with control | — Pending |
| Pay-per-job billing | SaaS with variable GPU cost; subscription tiers deferred until usage patterns are known | — Pending |

---
*Last updated: 2026-03-18 after initialization*
