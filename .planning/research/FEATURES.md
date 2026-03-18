# Feature Research

**Domain:** LLM-driven protein design SaaS (generative protein design via natural language)
**Researched:** 2026-03-18
**Confidence:** MEDIUM — platform internals inferred from competitor docs and community sources; LLM-to-workflow patterns from published agentic biology research

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features scientists assume exist before they trust a paid tool. Missing any of these ends the trial immediately.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| PDB file upload | All design runs require a target structure; no PDB = no job | LOW | Validate file format server-side on upload; reject malformed PDB before job dispatch |
| Accession ID input (PDB / UniProt) | Scientists think in accession IDs, not files; fetch-from-ID is the fast path | MEDIUM | Requires live fetch from RCSB and UniProt APIs; cache fetched structures per account |
| Job status monitoring | GPU jobs run 30 min–2 hrs; users need to know if it's running, stuck, or done | MEDIUM | Polling with periodic refresh is acceptable; WebSocket for real-time is nice but not required at launch |
| Email or in-app notification on completion | Users don't sit watching a dashboard for 2 hrs | LOW | Transactional email (Resend/SendGrid) on job state change; in-app badge is low-cost addition |
| PDB download of output structures | Core deliverable; without download the tool has no scientific value | LOW | Serve from object storage (S3/R2); signed URLs with expiry; zip for multi-file output |
| Design scoring display | Scientists need ranked outputs with numeric scores, not just files | MEDIUM | Surface pLDDT, ipTM, interface score, and ranking score per design; tabular view with sort |
| Interactive 3D structure viewer | Users expect to inspect structures in-browser before downloading | MEDIUM | Mol* is the standard (used by RCSB PDB and EBI PDBe); embed as React component; open-source, well-maintained |
| User authentication | Proprietary target structures require account isolation; no auth = no trust | LOW | Email/password + OAuth (Google); JWT session tokens; data segregation per account mandatory |
| Job history list | Scientists run multiple jobs; must be able to navigate past work | LOW | Simple chronological list with job name, tool used, status, date, and link to results |
| Pay-per-job billing | Scientists expect to pay for what they use on a tool with variable GPU cost | MEDIUM | Stripe metered billing; pre-authorize or pre-charge before GPU dispatch; surface cost estimate pre-launch |

---

### Differentiators (Competitive Advantage)

Features that distinguish this platform from Tamarind Bio (which offers form-based no-code access) and raw CLI tools. These are where the core value proposition lives.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Natural language goal intake | Scientists describe intent ("design a binder for the IL-6R extracellular domain that avoids the signal peptide region"); agent translates to tool + parameters | HIGH | Core LLM agent layer; requires Claude tool use with structured output; must handle ambiguous or underspecified goals gracefully |
| Agent-guided parameter wizard | Surfaces the right parameters with expert defaults encoded from Ranomics' operational knowledge; reduces parameter error rate | MEDIUM | Wizard is sequential, conversational; each question depends on prior answers and tool selected; not a static form |
| Automated tool selection with rationale | Agent recommends RFdiffusion vs BindCraft vs Boltzgen with a one-paragraph scientific explanation; user confirms or overrides | MEDIUM | Requires a classification prompt with tool comparison logic; the rationale display builds trust and is educationally valuable |
| Post-job next-step guidance | After results returned, agent explains what the scores mean, which designs to prioritize, and what to do next (e.g., AF2 validation, yeast display ordering) | MEDIUM | Templated but context-aware; uses job parameters and scores as context; positions Ranomics as expert guide, not just compute provider |
| Expert-default hotspot inference | If user doesn't specify hotspot residues, agent can suggest candidate hotspots from structure analysis (surface exposure, conservation, known functional sites) | HIGH | Requires SASA calculation or integration with structure annotation; defers to user confirmation; high scientific value |
| Conversation history within a job | Users can ask follow-up questions within the context of a running or completed job ("Why did you choose 80-residue binder length?") | MEDIUM | Persisted thread per job; Claude maintains context of all wizard answers and job parameters |
| Cost estimate before dispatch | Show GPU-hour estimate and dollar cost before user confirms launch | LOW | Based on tool, number of designs, and historical runtime data; sets user expectations and reduces billing disputes |
| Ranked design report (PDF/HTML) | Exportable report with parameters, scores, top-ranked structures, and methodology notes — usable in internal lab documents | MEDIUM | Template-rendered PDF; scientist can share with wet-lab team without giving them platform access |

---

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Full free-text parameter override (no wizard) | Power users want control and may find wizard condescending | Removes the guardrails that prevent costly failed runs; expert-only parameters (e.g., `weights_pae_inter`) require understanding of loss landscapes — wrong values produce no results | Offer an "Advanced settings" collapsible section with a clear "modify at your own risk" warning; don't surface these in the wizard flow |
| Real-time streaming log output during job | Scientists want to know what's happening inside the job | GPU jobs run in isolated containers on Modal/RunPod; streaming stderr to the browser requires persistent WebSocket plumbing across the provider abstraction layer; adds significant infra complexity for low diagnostic value | Show job stage milestones (queued → initializing → designing → scoring → complete) instead of raw logs |
| Shared workspaces / team collaboration at launch | Teams want shared job history and joint projects | Adds multi-tenant data model complexity before core workflow is validated; auth and data isolation become dramatically more complex | Single-user accounts at v1; export and share via the ranked report PDF; revisit after PMF |
| Sequence-only input (no structure) | Users may only have a FASTA sequence | Ambiguous for binder design without a 3D structure — cannot define hotspots or interface geometry from sequence alone | Prompt users to provide a PDB ID, upload a structure, or use AlphaFold Server to fold their target first; link to AF Server directly |
| Mobile-native app | Scientists may want phone access | Protein design is a desktop workflow (file upload, 3D viewer, parameter entry); mobile UX requires near-complete redesign of core interactions | Responsive web is sufficient; ensure dashboard and job status are mobile-readable even if the wizard is not |
| Full AlphaFold2/AF3 integration as a design tool | Users may request structure prediction from sequence | AF is a prediction tool, not a design tool; conflating them creates scientific confusion and scope complexity; AF3 API terms restrict some commercial uses | Surface AF validation in post-job guidance as an external link; do not run AF predictions internally in v1 |
| Auto-retry failed jobs | Reduces friction when jobs fail | Silent retries accumulate GPU cost and can mask configuration errors; a user with bad hotspot parameters will burn budget silently | Show failure reason clearly with agent diagnosis; prompt user to fix parameters and relaunch manually |
| Self-hosted / bring-your-own-cluster option | Power users with HPC access may not want to pay GPU markup | Massive operational complexity: auth, data transfer, security, GPU provider abstraction breaks | Out of scope for v1; note as v3+ enterprise feature |

---

## Feature Dependencies

```
[User Authentication]
    └──required by──> [Job Dispatch]
    └──required by──> [Job History]
    └──required by──> [Billing]
    └──required by──> [Data Isolation]

[PDB Upload / Accession Fetch]
    └──required by──> [Natural Language Intake]
    └──required by──> [Parameter Wizard]
    └──required by──> [Job Dispatch]

[Natural Language Intake]
    └──required by──> [Automated Tool Selection]
    └──feeds──> [Parameter Wizard]

[Parameter Wizard]
    └──required by──> [Job Dispatch]
    └──feeds──> [Cost Estimate]

[Job Dispatch]
    └──required by──> [Job Status Monitoring]
    └──required by──> [Notification on Completion]
    └──required by──> [Output Download]
    └──required by──> [Design Scoring Display]
    └──required by──> [3D Structure Viewer]

[Design Scoring Display]
    └──enhances──> [3D Structure Viewer]
    └──feeds──> [Post-Job Next-Step Guidance]

[Job History]
    └──enhances──> [Ranked Design Report]

[Billing]
    └──requires──> [GPU Cost Tracking per Job]
    └──enhances──> [Cost Estimate Before Dispatch]

[Expert Hotspot Inference]
    └──requires──> [SASA Calculation or Structure Annotation]
    └──enhances──> [Parameter Wizard]

[Conversation History within Job]
    └──requires──> [Natural Language Intake]
    └──requires──> [Job History]
```

### Dependency Notes

- **Job Dispatch requires Auth:** Data isolation between accounts is a hard security requirement; job dispatch without auth is a billing and IP protection risk.
- **Natural Language Intake requires PDB/Accession:** Agent cannot select tool or guide wizard without a structure reference; intake and structure acquisition must be co-designed.
- **Billing requires GPU cost tracking:** Pay-per-job pricing requires per-job cost attribution at the infrastructure layer; this must be designed into the GPU provider abstraction from the start, not retrofitted.
- **Expert hotspot inference is independent but high complexity:** Can be deferred to v1.x without blocking core flow; the wizard can ask for hotspots as a manual input with examples.
- **Ranked design report enhances but does not require job history:** Report generation can be triggered per-job; history list is a separate navigation feature.

---

## MVP Definition

### Launch With (v1)

Minimum viable product sufficient to validate the core thesis: "scientists can go from natural language goal to downloadable ranked PDB structures without writing a config file."

- [ ] User authentication (email/password + Google OAuth) — without this, no paying users
- [ ] PDB upload and accession ID fetch (PDB/UniProt) — primary structure input paths
- [ ] Natural language goal intake via Claude agent — the core differentiator
- [ ] Automated tool selection (RFdiffusion / BindCraft / Boltzgen) with rationale and user confirmation — builds trust, reduces errors
- [ ] Guided parameter wizard (conversational, expert defaults encoded) — prevents parameter mistakes that waste GPU budget
- [ ] Cost estimate before dispatch — required for user trust in pay-per-job model
- [ ] Async job dispatch to GPU (Modal or RunPod) — core infrastructure
- [ ] Job status monitoring (milestone stages: queued / initializing / designing / scoring / complete) — users need to know job state
- [ ] Email notification on job completion — users won't sit watching the dashboard
- [ ] Design scoring display (pLDDT, ipTM, interface score, ranked table) — required to interpret results
- [ ] Interactive 3D structure viewer (Mol*) — scientists will not trust output they cannot inspect in browser
- [ ] PDB download (individual and batch zip) — core deliverable
- [ ] Post-job next-step guidance from agent — positions Ranomics expertise, drives engagement
- [ ] Job history list with status and links — basic navigation requirement
- [ ] Stripe pay-per-job billing — revenue model

### Add After Validation (v1.x)

Add once core workflow is validated and real users are running jobs.

- [ ] Conversation history within a job (follow-up questions to agent) — valuable but requires persistent thread storage; validate demand first
- [ ] Ranked design report export (PDF) — users will ask for this once they start sharing results with teams; trigger: first user request
- [ ] Expert hotspot inference / suggestion from structure — high value but requires SASA integration; add when users report hotspot specification as a friction point
- [ ] Job parameter replay (copy settings from a past job) — once job history is used regularly, replay becomes natural; low implementation cost

### Future Consideration (v2+)

Defer until product-market fit is established and usage patterns are clear.

- [ ] Team / shared workspace features — adds multi-tenant complexity; validate whether users share with colleagues or prefer PDF export
- [ ] REST API for power users — when users want to integrate the platform into their own pipelines; likely demanded by pharma IT teams
- [ ] Custom tool parameter profiles (saved parameter sets) — useful for repeat users running standardized design campaigns; defer until repeat usage is observed
- [ ] Self-hosted GPU / bring-your-own-cluster — enterprise feature; defer to v3+

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| User authentication | HIGH | LOW | P1 |
| PDB upload / accession fetch | HIGH | LOW | P1 |
| Natural language intake (Claude agent) | HIGH | HIGH | P1 |
| Automated tool selection with rationale | HIGH | MEDIUM | P1 |
| Guided parameter wizard | HIGH | MEDIUM | P1 |
| Async job dispatch | HIGH | HIGH | P1 |
| Job status monitoring (milestones) | HIGH | MEDIUM | P1 |
| Email notification on completion | HIGH | LOW | P1 |
| Design scoring display | HIGH | MEDIUM | P1 |
| 3D structure viewer (Mol*) | HIGH | MEDIUM | P1 |
| PDB download | HIGH | LOW | P1 |
| Stripe billing + cost tracking | HIGH | MEDIUM | P1 |
| Post-job next-step guidance | MEDIUM | LOW | P1 |
| Job history list | MEDIUM | LOW | P1 |
| Cost estimate before dispatch | MEDIUM | LOW | P1 |
| Conversation history within job | MEDIUM | MEDIUM | P2 |
| Ranked design report (PDF export) | MEDIUM | MEDIUM | P2 |
| Expert hotspot inference | HIGH | HIGH | P2 |
| Job parameter replay | MEDIUM | LOW | P2 |
| Team / shared workspaces | MEDIUM | HIGH | P3 |
| REST API for power users | MEDIUM | HIGH | P3 |
| Custom parameter profiles | LOW | LOW | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

---

## Competitor Feature Analysis

| Feature | Tamarind Bio | Adaptyv Bio | Cradle | Our Approach |
|---------|--------------|-------------|--------|--------------|
| Input method | Form-based parameter entry per tool | Sequence submission via portal | Sequence + experimental data upload | Natural language → agent → wizard; lower floor, same ceiling |
| Tool selection | User selects tool manually from catalog of 200+ | Wet-lab platform, not design tool | AI optimization models | Agent recommends tool with rationale; user confirms |
| Parameter guidance | Tool-specific forms; expert knowledge not encoded | N/A | AI-assisted; no public details | Conversational wizard with Ranomics expert defaults baked in |
| Structure viewer | Not documented (likely external) | Not applicable | Not applicable | Mol* embedded in results page |
| Output scoring | ipTM, pLDDT per design (inferred from pipeline) | Experimental binding data | Model confidence + experimental metrics | pLDDT, ipTM, interface score, ranking score in sortable table |
| Next-step guidance | None observed | Experimental ordering CTA | Round-based iteration workflow | Agent-generated guidance contextual to scores and design goals |
| Billing model | Subscription tiers (inferred from YC positioning) | Pay-per-experiment | Enterprise contract | Pay-per-job with cost estimate shown before dispatch |
| Collaboration | Not observed at launch | Team portal for experiment tracking | Team round management | Single-user v1; shared report PDF as collaboration proxy |
| API access | Not public | Available (results API) | Enterprise | v2+ consideration |

---

## Sources

- Tamarind Bio platform and docs: https://www.tamarind.bio/ and https://docs.tamarind.bio/tools/rfdiffusion
- BindCraft GitHub and paper: https://github.com/martinpacesa/BindCraft and https://www.nature.com/articles/s41586-025-09429-6
- RFdiffusion GitHub: https://github.com/RosettaCommons/RFdiffusion
- Mol* Viewer (structure visualization standard): https://molstar.org/ and https://academic.oup.com/nar/article/49/W1/W431/6270780
- Adaptyv Bio platform: https://www.adaptyvbio.com/ and https://docs.adaptyvbio.com/introduction
- Cradle platform: https://www.cradle.bio/
- LatchBio platform features: https://latch.bio/product
- Stripe usage-based billing: https://stripe.com/billing/usage-based-billing
- AlphaFold confidence scoring (ipTM, pLDDT context): https://www.ebi.ac.uk/training/online/courses/alphafold/inputs-and-outputs/evaluating-alphafolds-predicted-structures-using-confidence-scores/confidence-scores-in-alphafold-multimer/
- ipSAE scoring for binder design ranking: https://pmc.ncbi.nlm.nih.gov/articles/PMC11844409/
- Talk2Biomodels (agentic biology LLM reference): https://bmcbioinformatics.biomedcentral.com/articles/10.1186/s12859-025-06310-1
- AI copilot UX best practices: https://www.letsgroto.com/blog/mastering-ai-copilot-design
- Biopharma cloud compute platforms overview: https://www.genengnews.com/insights/biopharma-soars-to-the-computational-clouds/

---

*Feature research for: LLM-driven protein design SaaS*
*Researched: 2026-03-18*
