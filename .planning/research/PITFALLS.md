# Pitfalls Research

**Domain:** LLM-agent-driven scientific SaaS — NL-to-protein-design-config, async GPU job dispatch, pay-per-job billing
**Researched:** 2026-03-18
**Confidence:** MEDIUM-HIGH (domain-specific claims verified via official docs and GitHub issues; billing edge cases from Stripe docs; GPU cost patterns from RunPod/Modal documentation)

---

## Critical Pitfalls

### Pitfall 1: Agent Confirms Tool Selection Before Validating Input Feasibility

**What goes wrong:**
The agent picks RFdiffusion/BindCraft/Boltzgen from the user's natural language description and presents that selection to the user. The user confirms. The parameter wizard runs. The job dispatches. Then the job fails on the GPU because the user's input — a PDB missing the target chain, a UniProt accession with no solved structure, or a chain length outside the model's trained distribution — was never validated before dispatch. The GPU clock starts, the charge accrues, and the output is nothing.

**Why it happens:**
Teams treat tool selection and input validation as separate concerns. The agent layer handles language; the job runner handles compute. Nobody owns the gap between "user confirmed parameters" and "job is actually runnable." Input validation is deferred to the compute environment, which has no feedback loop back to the user in time to prevent the charge.

**How to avoid:**
Build a pre-flight validation step between wizard completion and job dispatch. This step runs synchronously (no GPU cost) and must pass before the job enters the queue. Checks should include:
- PDB file: parse with Biopython, verify expected chains exist, check for HETATM-only structures, confirm residue count is within the model's usable range
- Accession inputs: fetch structure from RCSB/UniProt, verify the fetch succeeded and the structure contains protein chains
- Chain length: confirm binder length range is within the model's trained distribution (RFdiffusion: roughly 50–400 residues; BindCraft has documented OOM thresholds by GPU tier)
- Hotspot residues: verify hotspot notation parses correctly and residue indices exist in the uploaded structure
- GPU tier vs. design count: flag if `num_designs` × estimated runtime exceeds a cost ceiling before the user authorizes spend

Pre-flight failures should return a specific error message that the agent can translate into plain language guidance, not a stack trace.

**Warning signs:**
- First failed jobs on the platform have no validation errors in logs — they fail inside the container with CUDA OOM or FileNotFoundError
- Users report "I uploaded my PDB but the job said it couldn't find chain A" after being charged
- QA testing shows jobs dispatch successfully on malformed inputs in staging

**Phase to address:**
Agent + wizard phase (before GPU integration). Pre-flight validation must be built and tested on real PDB edge cases — multi-model PDBs, NMR ensembles, structures with insertion codes, structures with altloc atoms — before the GPU dispatch layer is wired in.

---

### Pitfall 2: Prompt Drift Causes Silent Tool Misclassification

**What goes wrong:**
The agent maps a user's natural language description to a tool (RFdiffusion, BindCraft, Boltzgen) using a prompt. That prompt is tuned against a narrow set of test cases during development. In production, users phrase requests differently. "Design a cyclic peptide that binds EGFR" gets classified as a BindCraft binder design job, when Boltzgen is the correct tool. The agent confidently presents the wrong tool, the user — who may not know the distinction — confirms, and the job runs to completion producing scientifically incorrect output for the user's actual goal.

**Why it happens:**
Classification prompts are optimized against developer-written test cases, not the full vocabulary of how scientists actually describe protein design problems. The tool selection boundary between RFdiffusion (backbone scaffolding), BindCraft (binder design with automatic MPNN/AF2 pipeline), and Boltzgen (sequence-conditional generation) is subtle and the field-specific vocabulary is non-obvious to a general LLM. Multi-agent failure analysis (MAST taxonomy, 2025) identifies specification ambiguity as the leading cause of agent misclassification at 41–86% failure rates across production systems.

**How to avoid:**
- Do not rely on a single LLM turn for tool selection. After the agent proposes a tool and rationale, require the user to read the rationale and explicitly confirm before the wizard starts. Make the rationale specific: "I'm recommending BindCraft because you described designing a protein binder against a known target structure — BindCraft integrates backbone diffusion, MPNN sequence design, and AlphaFold2 validation in a single pipeline. RFdiffusion would be appropriate if you needed unconditional backbone scaffolding."
- Encode the tool selection logic as structured rules in the system prompt, not free-form reasoning. Define the decision boundary explicitly: BindCraft = binder design against a known target with automated scoring; RFdiffusion = backbone scaffolding with manual downstream steps; Boltzgen = sequence-conditional structure generation.
- Build a test suite of 30+ classification cases covering ambiguous phrasing. Run this suite against prompt changes before deploying any system prompt update.
- Log every tool selection decision with the user's original NL input. Review misclassifications weekly in early operation.

**Warning signs:**
- User support requests describing "the job ran but the output doesn't match what I asked for"
- A/B testing of prompt variants shows >10% disagreement on ambiguous inputs
- Users frequently override the agent's tool recommendation during wizard confirmation

**Phase to address:**
Agent design phase. The classification logic and its test suite should be locked before the wizard is built, because the wizard questions are tool-specific. Rebuilding the wizard after discovering systematic misclassification is expensive.

---

### Pitfall 3: GPU Cost Runaway from Stuck or Crashed Jobs

**What goes wrong:**
A BindCraft job dispatched to RunPod or Modal hangs — CUDA context is alive, the process is not making progress, but it is not erroring. The container stays warm. The GPU meter runs. The job never emits a heartbeat. The platform's job status shows "running." The user waits. Hours pass. The user contacts support. The charge has already accumulated. Alternatively, a job crashes partway through (OOM on a long chain, network partition mid-run) and the process exits without cleaning up the pod, which RunPod bills until an explicit termination is sent.

**Why it happens:**
Scientific computing jobs are long-running (30 min–2 hrs per PROJECT.md) and do not emit natural heartbeat signals the way web services do. Teams wire up job dispatch but not job health monitoring. The GPU provider bills by the second (RunPod's documented billing model) with no automatic timeout on their side for user-defined jobs. Spot instance interruptions (RunPod gives 2 minutes notice) can leave jobs in an ambiguous state that looks "running" to the application.

**How to avoid:**
- Implement a watchdog: each dispatched job must emit a heartbeat to the application database every N minutes (suggest N=5). If no heartbeat is received for 2×N minutes, the watchdog marks the job as "suspected hung" and sends a termination signal to the GPU provider.
- Set hard wallclock timeouts per tool. RFdiffusion: 90 min max. BindCraft: 3 hrs max (BindCraft runs are documented as potentially expensive and long). Boltzgen: set conservatively until runtime data is collected. These are configurable but enforced, not advisory.
- On job termination (any cause: crash, timeout, watchdog), the termination handler must explicitly call the GPU provider's pod-kill or container-stop API. Never assume the provider cleans up on process exit.
- Set a per-account hourly spend alert via the GPU provider's API. If a single account's running GPU cost exceeds a threshold (e.g., 2× the most expensive tier's expected job cost), page an admin.
- Track all GPU cost via the provider's cost API, not by estimating from job start time. RunPod and Modal both expose per-job cost endpoints; use them as ground truth for billing, not internal clocks.

**Warning signs:**
- Jobs in "running" state for longer than the configured wallclock maximum
- GPU provider invoice significantly higher than sum of completed job costs
- User reports "my job has been running for 4 hours with no output"
- Cost anomaly between what was dispatched and what was billed by the provider

**Phase to address:**
GPU integration phase. The watchdog, heartbeat, and termination handler must be built and integration-tested before the billing layer is wired in. Testing should include intentionally killing GPU containers mid-job and verifying termination is confirmed and cost stops.

---

### Pitfall 4: Billing a User for a Job That Produced No Usable Output

**What goes wrong:**
A job runs to completion. The GPU cost accrues. The job exits cleanly. But the output is empty: no PDB files were written because the design filters rejected every candidate (BindCraft requires candidates to pass ipTM, pAE, SASA, and interface energy thresholds — it is common to screen hundreds of candidates and produce zero passing designs in a poorly configured run). The user is charged for GPU time. They have nothing to download. They dispute the charge.

**Why it happens:**
Pay-per-job billing models in scientific computing are priced on compute time, not on scientific output. This is the correct model — the compute was consumed regardless. But users conflate "the job finished" with "I got results." BindCraft is explicitly documented to run screening loops that frequently produce zero passing candidates, especially with misconfigured hotspots or overly strict thresholds. This is a foreseeable, routine outcome, not a software failure. Teams that do not communicate this upfront create billing disputes by default.

**How to avoid:**
- In the wizard, before launching any BindCraft job, display an explicit note: "BindCraft applies structural and energetic quality filters. Runs with overly strict thresholds or poorly chosen hotspots may produce zero passing designs. Compute cost is charged on GPU time regardless of output."
- Surface the filter thresholds in the wizard and default them to the values documented in BindCraft's own benchmarks, not maximally strict values.
- When a job completes with zero passing designs, the completion notification must include the reason: "0 of 847 candidates passed filters. Top rejection reasons: ipTM below threshold (92%), interface area too small (8%)." This turns a frustrating outcome into a diagnostic one.
- Define a refund policy before launch and publish it. Suggested starting position: full refund for platform errors (job crashed due to infra fault); no refund for jobs that ran to completion with zero designs (compute was consumed and design outcome is inherently probabilistic). Make this explicit in the ToS and at checkout.
- Consider a "pilot run" tier: 10–20 designs at low cost before committing to a full run, to validate parameter settings.

**Warning signs:**
- High zero-output job rate in early logs (>20% of BindCraft jobs returning zero designs)
- User support requests disproportionately referencing "I paid but got nothing"
- Refund requests citing "the tool didn't work"

**Phase to address:**
Billing design phase, before any public access. The billing policy, refund handling, and wizard copy must be written and reviewed together. The billing system must be able to distinguish "job ran and produced output" from "job ran and produced nothing" for support purposes.

---

### Pitfall 5: NL Ambiguity Leading to Wrong Parameter Defaults That Scale Unexpectedly

**What goes wrong:**
A user says "design a binder for my target." The agent defaults to `num_designs=100` because that is the standard BindCraft screening number. On an A100, that is approximately $8–12 in GPU cost. A power user says "design lots of binders for a thorough screen" — the agent interprets "lots" as `num_designs=1000`, dispatches the job, and the user receives a $80–120 charge they did not anticipate. Alternatively, the user says "quick test" and the agent sets `num_designs=10`, which is too low to produce passing designs, wasting even the small cost.

**Why it happens:**
Natural language quantity terms ("a few," "thorough," "lots," "quick") do not map to specific parameter values without a cost-anchored interpretation. The agent has no incentive in its prompt to ask for clarification before committing to a number, and developers testing with small `num_designs` values never see the cost implications of liberal quantity interpretation.

**How to avoid:**
- The wizard must always show the estimated cost before job dispatch. The cost estimate should be calculated from the actual parameters, not a flat rate. If `num_designs=500` on an A100 is estimated at $45, the user sees "$45 estimated — approve?" before the job enters the queue.
- Set hard per-job cost caps per account tier. Free tier: $15 max per job. Paid tier: $100 max per job. Enterprise: configurable. If a parameter set would exceed the cap, the wizard must block and explain.
- Never let the agent resolve quantity ambiguity silently. If the user uses a vague quantity term, the wizard must ask: "How many designs would you like to generate? More designs increase the chance of finding high-quality candidates but cost more. [10 designs ~$1 | 100 designs ~$10 | 500 designs ~$48]"
- Log every case where an agent-resolved quantity term leads to a cost estimate above the median job cost. Review weekly.

**Warning signs:**
- Wide variance in job cost for similar task descriptions in early logs
- Users expressing surprise at invoice amounts in support tickets
- A/B tests on "estimate before dispatch" show users frequently reducing num_designs when shown the cost

**Phase to address:**
Wizard + billing integration phase. The cost estimator must be built before the wizard goes to any external user. Estimates do not need to be exact — ±20% is acceptable — but must be shown before any authorization.

---

### Pitfall 6: PDB File Edge Cases That Silently Corrupt Job Input

**What goes wrong:**
A user uploads a PDB file. The application accepts it. The job dispatches. The tool (RFdiffusion or BindCraft) parses the PDB internally and either crashes or silently uses the wrong structure. Common edge cases: multi-model NMR PDBs where only MODEL 1 is valid for design but the parser uses the concatenated file; structures with insertion codes (e.g., residue 100A, 100B) where integer-based hotspot indexing skips or mis-indexes residues; alternate conformations (altloc) where the tool uses a partial atom set; HETATM-only entries (ligands, glycans) parsed as protein chains; structures with non-standard amino acids (selenomethionine, MSE) that crash MPNN sequence design.

**Why it happens:**
The PDB format is 50 years old and carries extensive legacy complexity. The Biopython PDB parser warns on many of these cases but does not fail, so applications using Biopython as the validation gate see no error. Tool-level parsers (RFdiffusion, BindCraft) have their own parsing logic that may handle edge cases differently. The discrepancy between what the application thinks it accepted and what the tool actually reads is the root cause.

**How to avoid:**
- Use Biopython's `PDBParser` with `QUIET=False` and capture all warnings. Treat any warning as a gate: log the warning and surface it to the user with a plain-language description.
- On ingest, normalize the PDB: (1) extract MODEL 1 only for multi-model files, (2) retain only the first altloc for disordered atoms, (3) convert MSE to MET and other common non-standard residue substitutions, (4) strip HETATM records unless they are explicitly requested for motif scaffolding. Write the normalized PDB back to storage as the canonical job input.
- Verify the normalized structure contains at least one protein chain with >20 residues before accepting the upload.
- For hotspot residue input, validate residue indices against the normalized PDB's actual residue list — including insertion codes — before the wizard proceeds. Use the exact (chain, resseq, icode) tuple, not just integer index.
- Test the ingest pipeline against the following real-world cases before any public release: NMR ensemble, crystal structure with two identical chains in the ASU, a structure with insertion codes (e.g., antibody CDR loops), a glycoprotein with heavy HETATM content, a structure containing MSE.

**Warning signs:**
- Uploaded PDB produces different chain counts in the wizard vs. in the job log
- Jobs fail inside the container with messages referencing missing residues or invalid chain IDs
- Users report "I selected chain A but the job used chain B"

**Phase to address:**
PDB ingest and wizard phase. The normalization pipeline must be built and tested against real edge-case PDB files before the wizard uses PDB data to populate its fields. A corpus of edge-case PDBs should be part of the test fixtures.

---

### Pitfall 7: User Distrust from Opaque Agent Decisions

**What goes wrong:**
The agent selects a tool and sets default parameters. The user sees: "I recommend BindCraft. Launching wizard." They do not understand why BindCraft was chosen over RFdiffusion. They do not see what assumptions were made about their request. They do not know which parts of their description were used and which were ignored. When the job produces poor results, the user blames the agent but cannot diagnose the failure because the reasoning is invisible. Trust erodes. Users either stop using the platform or demand human review of every job before dispatch.

**Why it happens:**
Teams optimize for frictionless UX — fewer clicks, faster to launch. Rationale display is treated as noise. Research on LLM agent trust (Nature Machine Intelligence, 2026; Trustworthy LLM Agents survey, 2025) consistently shows that appropriate transparency increases user trust and appropriate use of automation. Hiding reasoning does not increase trust — it eliminates the feedback loop that would let users correct the agent's assumptions.

**How to avoid:**
- Every agent decision that affects job configuration must be accompanied by an explicit rationale statement, shown before the user confirms. The rationale must be specific, not generic: "Recommending BindCraft because: you described a binder design goal against a specific target (IL-6R), you provided a PDB structure, and you want ranked candidates with scores. RFdiffusion alone does not include sequence design or scoring." Not: "BindCraft is suitable for your goals."
- Before the wizard begins, show a plain-language summary of what the agent understood from the user's request: "I understood: Target = IL-6 receptor (chain A of uploaded PDB), Goal = de novo binder design, No motif constraints specified. Is this correct?" Allow correction before any wizard questions.
- Make the parameter wizard visible even for defaulted parameters. Show what was defaulted and why. Users should never be surprised by a parameter value when the job starts.
- Log agent interpretations and actual job parameters together. When a user reports a bad job, support should be able to show them exactly what the agent understood and what parameters it chose.

**Warning signs:**
- Users reaching the wizard review screen and going back to rephrase their original request (indicates the agent's summary does not match their intent)
- Support tickets that contain "I didn't ask for that" or "why did it choose that"
- Low wizard completion rate (users abandon after seeing the agent's parameter summary)

**Phase to address:**
Agent design and wizard phase, jointly. Rationale display must be designed into the agent interaction flow from the beginning — adding it retroactively requires restructuring the UX and the prompt.

---

### Pitfall 8: Job Retry Without Idempotency Creates Double Charges

**What goes wrong:**
A job is dispatched to the GPU provider. The application loses connectivity during dispatch (network blip, application restart, provider API timeout). The application retries the dispatch. The GPU provider received the first request, started the job, and billed for it. The retry starts a second job. The application tracks only the second job ID. The first job runs to completion on the provider's infrastructure, consuming GPU-hours that appear on the provider's invoice but not in the application's job accounting. The user is billed once. Ranomics pays twice.

**Why it happens:**
Dispatch retry logic is added for reliability but without idempotency keys. The Celery/RQ literature identifies this as a documented production failure mode: late acknowledgment combined with retry creates double execution. GPU job dispatch is a non-idempotent operation unless the provider supports idempotency keys.

**How to avoid:**
- Assign a `job_id` (UUID) to every job before dispatch, generated by the application, not by the provider. Use this as the idempotency key in the provider API call where supported (Modal supports this; RunPod does not natively — use a pre-dispatch existence check against the provider API instead).
- Store the job's dispatch state machine in the database with transitions: `pending → dispatching → dispatched → running → completed/failed`. The `dispatching` state with the application-generated `job_id` must be written to the database before any provider API call. On retry, check if a provider job already exists for this `job_id` before dispatching a new one.
- Never auto-retry a job dispatch more than once without a human-readable reason in the logs.
- Reconcile application job costs against provider invoices monthly. Any provider-billed job that has no corresponding application job record is a double-dispatch incident.

**Warning signs:**
- Provider invoice total diverges from sum of application-tracked job costs
- Database contains jobs in `dispatching` state that are older than 10 minutes
- Provider API shows active jobs that have no matching record in the application job table

**Phase to address:**
GPU integration phase. Idempotency must be designed into the dispatch flow before the billing layer is wired in. Retrofitting idempotency into a dispatch system that has already processed real jobs is high risk.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Hardcode `num_designs` defaults per tool | Faster wizard implementation | Silent cost surprises for power users; refund disputes | Never — cost implications require user visibility |
| Use PDB chain letter alone (not full residue tuple) for hotspot indexing | Simpler wizard UI | Residue mis-indexing on insertion code structures | Never in production; test with a single antibody structure first |
| Poll job status by wallclock estimate instead of heartbeat | Simpler job monitoring | Missed stuck jobs, runaway GPU cost | Only in MVP with a 90-minute hard cap and human review of all jobs |
| Treat all job failures as "infrastructure error" for billing | Reduces refund friction | Trains users to dispute any bad run; no accountability signal | Never — always distinguish infra error from zero-output run |
| Single prompt for both tool classification and parameter extraction | Fewer API calls | Prompt confusion; two concerns competing for context; harder to test each in isolation | Prototyping only |
| Skip pre-flight PDB validation | Faster wizard | OOM crashes, wrong-chain jobs, opaque failures | Never — one real PDB edge case in production will cost more than validation sprint |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Claude tool use / structured outputs | Assuming JSON schema compliance means values are semantically valid (e.g., a residue index that is syntactically an integer but does not exist in the structure) | Validate semantic constraints after schema validation; JSON schema cannot check "this residue ID exists in this PDB" |
| RunPod API | Assuming process exit = pod termination = billing stop | Call the pod-kill endpoint explicitly on job end; monitor provider cost API not just process status |
| Modal | Assuming serverless container teardown is immediate on function return | Use Modal's `@app.function(timeout=...)` parameter to enforce hard job timeouts; do not rely on application-level timeouts alone |
| Stripe metered billing | Reporting usage at job start rather than job end | Report confirmed GPU cost from provider invoice, not estimated cost at start; use Stripe's usage reporting API at job completion |
| RCSB PDB API | Assuming a UniProt accession always resolves to a single PDB structure | The UniProt-to-PDB mapping is many-to-many; implement a selection step that shows the user available structures and asks which to use |
| Biopython PDBParser | Using `QUIET=True` to suppress warnings | Suppress nothing in production ingest; capture all warnings as structured data and gate on them |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Synchronous PDB fetch from RCSB inside the request/response cycle | Wizard hangs for 5–30 seconds during accession lookup; timeout errors on slow RCSB responses | Fetch asynchronously; show a loading indicator; cache fetched structures by accession | Any accession input, especially during RCSB load spikes |
| Database polling for job status from the frontend | High DB query volume at scale; lag in status updates | Use WebSockets or SSE for real-time job status push; poll GPU provider only from the backend watchdog, not from user sessions | ~50 concurrent active jobs |
| Storing full PDB files in the application database as BLOBs | Slow queries, high DB storage cost | Store PDB files in object storage (S3/GCS); store only the file path and metadata in the database | First week with real users uploading large complexes |
| Generating all design reports synchronously at job completion | Job "completion" takes minutes after the GPU job ends; user sees delayed results | Generate reports asynchronously as a post-processing step; return structures immediately, scores when ready | Any job with >50 output designs |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing user-uploaded PDB files in a shared bucket without per-account prefix isolation | Competitor's proprietary target structure accessible via a guessed URL or leaked signed URL | Use per-account UUID prefix in object storage keys; generate time-limited signed URLs for all structure downloads; never expose public bucket URLs |
| Passing user-supplied hotspot residue strings directly to the tool CLI without sanitization | Shell injection if the tool is invoked via subprocess; path traversal if the residue string is used in a filename | Validate hotspot input against a strict regex before any use; construct CLI arguments as a list (not a string) to prevent shell injection |
| Including PDB file content in Claude API requests without redacting proprietary structure data | Anthropic processes the structural data as training context unless opted out | Confirm Anthropic API usage under a no-training agreement before processing client PDB files; never send raw PDB coordinates to the LLM — send only metadata (chain IDs, residue count, resolution) |
| Single JWT secret across all environments | Development token grants production API access | Separate secrets per environment; rotate secrets before first public user |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No cost estimate before job launch | Users discover cost on invoice; disputes follow | Always show estimated cost and require explicit confirmation before dispatching any job |
| "Job running" is the only status state | Users have no sense of progress during a 90-minute BindCraft run | Emit stage-level progress events: "Running backbone diffusion (step 1/4)", "Running MPNN sequence design (step 2/4)" |
| Job failure message is a stack trace | Scientists cannot diagnose what went wrong; they escalate to support | Translate all container exit states into scientist-readable messages: "The job ran out of GPU memory. Try reducing num_designs or switching to a larger GPU tier." |
| Success is defined as "job completed" | A zero-output BindCraft run looks like success | Success must be defined as "job completed and produced N passing designs." Surface this distinction immediately on the results page. |
| No mechanism to re-run a job with adjusted parameters | After a zero-output run, users must restart the entire wizard from scratch | Offer "re-run with same target, adjust parameters" as a one-click option on the results page, pre-filling the wizard with the previous job's settings |

---

## "Looks Done But Isn't" Checklist

- [ ] **PDB ingest:** Normalized structure written back to storage and verified against the original upload — verify by confirming chain IDs match what the wizard displayed
- [ ] **Cost estimate:** Estimate is shown and user-confirmed before job enters the queue — not just displayed, but gating: job cannot dispatch without confirmation
- [ ] **Job watchdog:** Hard wallclock timeout fires and explicitly kills the provider pod — test by submitting a job that hangs intentionally and confirming provider billing stops
- [ ] **Termination handler:** Provider pod-kill is confirmed with a success response, not just called — check provider dashboard manually for the first 10 test terminations
- [ ] **Zero-output completion:** Job that produces zero passing designs is correctly distinguished from a job failure in the billing system — test with BindCraft thresholds set to reject everything
- [ ] **Idempotent dispatch:** Retrying a failed dispatch does not create a second provider job — test by killing the application mid-dispatch and retrying
- [ ] **Stripe usage reporting:** Usage is reported at job completion with provider-confirmed cost, not at job start with estimated cost — verify Stripe dashboard matches provider invoice for 10 test jobs
- [ ] **Data isolation:** User A cannot access User B's PDB uploads by any URL pattern — verify with a logged-in User B attempting to access User A's signed URL

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Runaway GPU job (stuck, billing accruing) | LOW (if watchdog exists), HIGH (if no watchdog) | Kill pod via provider API directly; refund the GPU cost above expected job cost; add the job to the watchdog gap report |
| Double-dispatched job | MEDIUM | Kill the orphaned provider job; reconcile provider invoice against application records; issue credit if user was billed for both |
| Systematic tool misclassification discovered post-launch | HIGH | Audit all jobs using the misclassified prompt version; contact affected users; offer free re-runs; update classification prompt and test suite |
| Billing dispute over zero-output job | LOW | Reference ToS on probabilistic output; provide job log showing filters applied and rejection reasons; offer a partial credit as goodwill if the parameter misconfiguration was influenced by agent defaults |
| PDB parsing edge case corrupts job input | MEDIUM | Identify affected jobs by PDB provenance; re-run with corrected normalization pipeline; refund or credit jobs that produced no output due to the corruption |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Input feasibility not validated before dispatch | Agent + wizard phase | Pre-flight test suite passes on 10 real edge-case PDBs before GPU integration begins |
| Tool misclassification from prompt drift | Agent design phase | Classification test suite of 30+ cases; <5% misclassification rate before wizard is built |
| GPU cost runaway from stuck/crashed jobs | GPU integration phase | Intentional hang test confirms watchdog fires within 2×heartbeat interval and pod is killed |
| Billing for zero-output jobs | Billing design phase | ToS, refund policy, and wizard copy reviewed and signed off before any external user access |
| NL quantity terms causing unexpected costs | Wizard + billing integration phase | Cost estimator displayed and gating; smoke test with "design lots of binders" confirms cap fires |
| PDB edge cases silently corrupting input | PDB ingest phase | Test suite includes NMR ensemble, insertion codes, altloc, MSE, HETATM-heavy structures |
| Opaque agent decisions eroding user trust | Agent design phase | Rationale display reviewed by a scientist unfamiliar with the tools; all assumptions shown before confirmation |
| Non-idempotent dispatch causing double charges | GPU integration phase | Kill-during-dispatch test confirms no duplicate provider job on retry |

---

## Sources

- MAST taxonomy (Multi-Agent System Failure Taxonomy), March 2025 — 41–86.7% failure rates across 7 frameworks, 1,642 execution traces: https://www.marktechpost.com/2025/03/25/understanding-and-mitigating-failure-modes-in-llm-based-multi-agent-systems/
- Anthropic Claude structured outputs (strict mode for tool parameter validation), released November 2025: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Anthropic hallucination reduction techniques: https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations
- BindCraft OOM and parameter configuration (reduce num_models, use L40S): https://australian-protein-design-initiative.github.io/binder-design-workshop/bindcraft_scoring.html
- BindCraft GitHub (martinpacesa/BindCraft): https://github.com/martinpacesa/BindCraft
- RFdiffusion GitHub issues (hotspot configuration): https://github.com/RosettaCommons/RFdiffusion/issues/178
- RunPod per-second billing and spot interruption model: https://flexprice.io/blog/runprod-pricing-guide-with-gpu-costs
- RunPod cloud GPU mistake avoidance: https://www.runpod.io/articles/guides/cloud-gpu-mistakes-to-avoid
- Celery idempotency and late acknowledgment pitfalls: https://www.vintasoftware.com/blog/celery-wild-tips-and-tricks-run-async-tasks-real-world
- Python RQ interrupted async task problem: https://medium.com/picus-security-engineering/the-interrupted-asynchronous-task-problem-and-solution-with-python-rq-435f1a597631
- Stripe metered billing and dispute handling: https://docs.stripe.com/disputes/how-disputes-work
- Stripe best practices for SaaS billing: https://stripe.com/resources/more/best-practices-for-saas-billing
- Biopython PDB parser edge cases: https://biopython.org/wiki/The_Biopython_Structural_Bioinformatics_FAQ
- PDBeCIF mmCIF edge case complexity: https://bmcbioinformatics.biomedcentral.com/articles/10.1186/s12859-021-04271-9
- Multi-agent AI systems transparency (Nature Machine Intelligence, 2026): https://www.nature.com/articles/s42256-026-01183-2
- Trustworthy LLM Agents survey (2025): https://arxiv.org/abs/2503.09648
- SaaS data isolation in multi-tenant systems: https://redis.io/blog/data-isolation-multi-tenant-saas/

---
*Pitfalls research for: LLM-agent-driven protein design SaaS (NL-to-config, async GPU dispatch, pay-per-job billing)*
*Researched: 2026-03-18*
