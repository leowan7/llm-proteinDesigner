---
phase: 08-post-run-analysis-agent
created: 2026-04-10
status: complete
decisions: 19
deferred: 0
---

# Phase 8: Post-Run Analysis Agent — Context

## Domain Boundary

After a design job completes, the agent assists the scientist in analyzing results — ranking candidates, explaining metrics with domain expertise, identifying best designs to order for experimental validation, launching follow-up refolding jobs, and generating downloadable reports.

## Canonical Refs

- `backend/agent/router.py` — existing agent SSE streaming + tool dispatch
- `backend/jobs/router.py` — job API with candidate fetching (line 517-532)
- `backend/jobs/models.py` — status/stage/result enums, CandidateResult model
- `frontend/src/pages/JobPage.tsx` — job results display
- `frontend/src/components/jobs/CandidateCard.tsx` — score rendering (pLDDT, binding_energy, iPAE, pAE, confidence)
- `backend/storage/client.py` — presigned URL generation for PDB downloads
- `supabase/migrations/20260319000002_billing_and_results.sql` — job_candidates schema (rank, pdb_key, scores JSONB)

## Decisions

### Analysis Trigger & Flow

D-01: **User-initiated analysis** — user asks about results in chat; agent does not auto-prompt when job completes. Keeps interaction conversational and non-noisy.

D-02: **Same session** — analysis happens in the chat session where the job was launched. Agent has full context of what the user asked for (target, tool choice, parameters).

D-03: **Tool-based data loading** — agent gets a new `load_job_results` tool that fetches candidates + scores from DB on demand. Does NOT preload into system prompt (BindCraft can produce 100+ candidates, would blow context).

D-04: **PDB structural feature parsing** — agent gets a tool that extracts interface residues, contact counts, clash scores, and buried surface area from PDB files using BioPython. Returns structured data, not raw coordinates. Enough to say "this design has 14 interface contacts and low clash score."

D-05: **No cross-tool comparison** — different tools (RFdiffusion, BindCraft, BoltzGen) design fundamentally different proteins, so comparing across tools is irrelevant. Instead, the analysis workflow guides users to take top hits and refold with independent structure predictors (AF2-multimer, Boltz2) for orthogonal validation.

D-06: **Refolding follow-up jobs** — agent can launch refolding jobs (AF2-multimer, Boltz2) directly from the analysis conversation. Agent recommends which candidates to refold, user confirms, then agent auto-assembles the complex input (original target + designed binder) and submits the job.

D-07: **Recommend + confirm** — agent proposes a shortlist for refolding (e.g., "I'd refold your top 5 by ipTM + the 2 with best shape complementarity") and asks user to confirm or adjust before launching. Never auto-launches without confirmation.

D-08: **Diagnostic mode for zero/low-output jobs** — when a job produces zero or very few candidates, agent shifts to diagnostic reasoning: what likely went wrong based on input parameters and target properties, what to try next.

### Metric Intelligence

D-09: **Hardcoded metric profiles** — each tool has a predefined metric profile authored by Leo: name, range, interpretation thresholds (e.g., ipTM > 0.8 = strong), red flags, and contextual meaning. Stored as reference files included in agent system prompt.

D-10: **Absolute + relative thresholds** — agent uses both absolute thresholds from literature/Ranomics expertise AND relative position within the job's result distribution. "This candidate has ipTM 0.85 (strong) and is in the top 5% of your run."

D-11: **Leo authors metric profiles** — these are proprietary Ranomics/Kendrew interpretation layers, not generic LLM knowledge. Leo writes the reference files with domain-expert thresholds and red flag rules.

D-12: **Proactive red flag flagging** — when loading results, agent automatically identifies and flags red flags: high ipTM but low shape complementarity, aggregation-prone sequences, unusually high clash scores. Scientists expect this from a good analyst.

### Report & Export

D-13: **Three export formats** — PDF (with Kendrew branding, not Ranomics), CSV for data import, and markdown for documentation. User gets all three options.

D-14: **Full analysis package** — report includes ranked shortlist table, metric explanations, red flags, refolding recommendations, experimental next steps, and original design parameters. A complete handoff document.

D-15: **Both trigger methods** — user can say "generate a report" in chat OR click an "Export Report" button on the job page. Button pre-populates a chat prompt to the agent.

D-16: **Text and tables only** — PDF does not include 3D structure thumbnails. Text, metric tables, and rationale only. User views structures in the Mol* viewer separately.

D-17: **Download links, not bundled files** — report contains presigned download links for each shortlisted candidate's PDB. Keeps report file small.

### Next-Step Guidance

D-18: **Structured protocol-level recommendations** — agent recommends specific approaches: "Express in HEK293, purify via Ni-NTA + SEC, validate binding by SPR at 3 concentrations." Backed by Leo's authored guidance profiles.

D-19: **Leo authors guidance profiles** — structured guidance for each tool + target class combination. Agent applies the right profile based on job context. Proprietary Kendrew expertise, not generic LLM knowledge.

D-20: **Yeast display when criteria are met** — agent recommends yeast display library construction when the design run produced enough viable candidates and the target is suitable. Triggering criteria defined in the authored guidance profiles.

D-21: **No cost/timeline estimates** — agent recommends what to do experimentally, not how much it costs or how long it takes. That's a sales/project management concern.

## Specifics

- Metric profiles and guidance profiles are authored content files that Leo will write — the phase implementation creates the infrastructure to load and apply them, plus placeholder/template files for Leo to populate
- Kendrew branding on PDF reports (not Ranomics)
- BioPython for PDB structural feature extraction (interface residues, contacts, clashes, BSA)
- The refolding workflow is: select top hits → assemble complex (target + binder) → submit AF2-multimer or Boltz2 job → user analyzes refolding results in a new analysis cycle

## Claude's Discretion

- Exact tool function signatures and parameter schemas
- How metric profiles are stored (JSON, YAML, Python dicts) — whatever works best
- PDF generation library choice (weasyprint, reportlab, etc.)
- How the "Export Report" button integrates with the chat agent flow
- Caching strategy for loaded job results within a session
- How to handle very large candidate sets (100+) in the load_job_results tool response

## Deferred Ideas

(none)
