# Phase 8: Post-Run Analysis Agent — Research

**Researched:** 2026-04-10
**Domain:** Claude agent tool extension, BioPython structural analysis, PDF generation, protein binder scoring metrics
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Analysis Trigger & Flow**
- D-01: User-initiated analysis — no auto-prompt when job completes
- D-02: Same session as job launch — agent retains full design context
- D-03: `load_job_results` tool fetches candidates + scores from DB on demand; no preloading into system prompt
- D-04: PDB structural feature extraction via BioPython: interface residues, contact counts, clash scores, BSA — returns structured data
- D-05: No cross-tool comparison (RFdiffusion vs BindCraft designs are not comparable)
- D-06: Agent can launch refolding jobs (AF2-multimer, Boltz2) from analysis conversation
- D-07: Recommend + confirm pattern — agent proposes shortlist, user confirms before any job launch
- D-08: Diagnostic mode when job produces zero/few candidates

**Metric Intelligence**
- D-09: Hardcoded metric profiles authored by Leo — stored as reference files loaded into system prompt
- D-10: Absolute + relative thresholds — literature-based thresholds AND percentile rank within the run
- D-11: Leo authors metric profiles — proprietary Kendrew interpretation layer
- D-12: Proactive red flag flagging on `load_job_results` call

**Report & Export**
- D-13: Three export formats — PDF (Kendrew branding), CSV, Markdown
- D-14: Full analysis package in report (shortlist table, metric explanations, red flags, refolding recs, next steps, original params)
- D-15: Two trigger methods — "generate a report" in chat OR "Export Report" button on job page
- D-16: Text and tables only in PDF — no 3D structure thumbnails
- D-17: Presigned download links for PDBs in report — no bundled files

**Next-Step Guidance**
- D-18: Structured protocol-level recommendations (expression system, purification, binding assay)
- D-19: Leo authors guidance profiles — tool + target class combinations
- D-20: Yeast display recommendation when criteria met (defined in guidance profiles)
- D-21: No cost/timeline estimates from agent

### Claude's Discretion

- Exact tool function signatures and parameter schemas
- How metric profiles are stored (JSON, YAML, Python dicts)
- PDF generation library choice
- How "Export Report" button integrates with agent chat flow
- Caching strategy for loaded job results within a session
- How to handle large candidate sets (100+) in load_job_results

### Deferred Ideas (OUT OF SCOPE)

(none)
</user_constraints>

---

## Summary

Phase 8 extends the existing Claude agent with five new tools and two authored reference-file layers. The agent infrastructure (SSE streaming, tool dispatch loop, system prompt loading from `reference/` directory) is fully established — this phase slots new tools into the existing `TOOL_DEFINITIONS` list and `dispatch_tool` switch.

The two most technically novel components are: (1) the BioPython PDB analysis tool that computes clash scores and buried surface area (BSA) — both calculable from the existing `Bio.PDB.SASA` module and `NeighborSearch` already used in Phase 2; and (2) PDF generation using `fpdf2`, a pure-Python library with no system dependencies that installs cleanly in Docker.

The authored content layer (metric profiles + guidance profiles) is the highest-value part of the phase. The implementation creates the file infrastructure and placeholder YAML/JSON files; Leo populates them with domain thresholds and protocol recommendations. The agent loads these at import time exactly as it already loads `01_tool_selection_guide.md` and `02_technical_setup_guide.md` from `backend/agent/reference/`.

**Primary recommendation:** Follow the existing tool-dispatch pattern precisely. New tools are `load_job_results`, `analyze_candidates`, `flag_red_flags`, `submit_refolding_job`, and `generate_report`. PDF via `fpdf2`. Metric profiles as YAML files in `backend/agent/reference/`. Caching via in-memory session dict keyed by job_id.

---

## Standard Stack

### Core (all already in requirements.txt)

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| `anthropic` | 0.86.0 | Claude tool-use loop — no changes needed | [VERIFIED: requirements.txt] |
| `biopython` | 1.86 | PDB parsing, SASA, NeighborSearch for structural features | [VERIFIED: requirements.txt] |
| `asyncpg` | 0.31.0 | DB fetch for job candidates (existing pool pattern) | [VERIFIED: requirements.txt] |
| `boto3` | 1.42.71 | Presigned GET URLs for PDB links in reports | [VERIFIED: requirements.txt] |
| `pandas` | 2.2.3 | Candidate DataFrame for ranking, filtering, CSV export | [VERIFIED: requirements.txt] |

### New Additions Required

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `fpdf2` | 2.8.7 | PDF report generation | Pure-Python, no system deps, installable in Docker with `pip install fpdf2` |
| `pyyaml` | (stdlib) | Load metric/guidance profile YAML files | Standard; no new dep needed |

**Note on pyyaml:** PyYAML is a transitive dependency of several existing packages. Verify with `pip show pyyaml` before adding to requirements.txt. If absent, add `pyyaml==6.0.2`. [ASSUMED — not verified in this Docker container]

**Installation (new):**
```bash
pip install fpdf2==2.8.7
```

**fpdf2 choice rationale:** WeasyPrint requires native system libraries (Pango, Cairo) — incompatible with the slim Docker base images used for the backend. ReportLab adds complexity for what is a text+table report. fpdf2 v2 is actively maintained (py-pdf org on GitHub), pure Python, and handles tables natively via its `Table` class. [VERIFIED: pypi.org, GitHub py-pdf/fpdf2]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| fpdf2 | ReportLab | ReportLab has better complex layouts; overkill for text+table reports and adds ~20MB to image |
| fpdf2 | WeasyPrint | WeasyPrint requires Pango/Cairo system libs — Docker build complexity, not worth it |
| YAML profiles | Python dicts in code | YAML is editable by Leo without touching Python; clear win |
| pandas for ranking | raw Python sort | pandas makes multi-column sort, percentile calculation, and CSV export trivial |

---

## Architecture Patterns

### Existing Pattern: Tool Dispatch (copy exactly)

The agent tool loop in `backend/agent/router.py` calls `dispatch_tool(block.name, block.input)`. New tools follow the identical pattern — add entries to `TOOL_DEFINITIONS` and new branches in `dispatch_tool`. No changes to `router.py`. [VERIFIED: codebase read]

### Existing Pattern: Reference Files Loaded at Import Time

`backend/agent/system_prompt.py` loads `reference/01_tool_selection_guide.md` and `reference/02_tool_selection_guide.md` at import time via `_load_reference()`. Metric profiles and guidance profiles use the same mechanism — add new `_load_reference("03_metric_profiles.md")` etc. [VERIFIED: codebase read]

### Existing Pattern: Job Candidates DB Query

The job router already fetches candidates with:
```sql
SELECT rank, pdb_key, scores FROM public.job_candidates
WHERE job_id = $1 ORDER BY rank
```
[VERIFIED: backend/jobs/router.py line 521]

The `load_job_results` tool uses this exact query via the existing `get_db_pool()` pattern.

### New Pattern: Tool-Side Result Caching

100+ BindCraft candidates must not be re-fetched on every tool call. Cache loaded candidates in a module-level dict keyed by `job_id`. Simple Python dict is sufficient — sessions are single-process, and arq worker is separate. Invalidation: not needed (job results are immutable after completion).

```python
# backend/agent/analysis_cache.py
_CANDIDATE_CACHE: dict[str, list[dict]] = {}

def get_cached(job_id: str) -> list[dict] | None:
    return _CANDIDATE_CACHE.get(job_id)

def set_cached(job_id: str, candidates: list[dict]) -> None:
    _CANDIDATE_CACHE[job_id] = candidates
```

**Pitfall:** Module-level dict is process-scoped. With uvicorn workers > 1, cache is not shared. Acceptable for v1 — cache miss just re-fetches from DB. [ASSUMED — production worker count not confirmed]

### New Pattern: Large Candidate Set Handling

When a job has 100+ candidates, `load_job_results` should return a summary page not all candidates:
- Return top 20 by primary metric + distribution statistics (min, max, mean, p25, p75, p95) for all metrics
- Claude interprets the summary; user can ask for specific candidate details
- Full dataset is available in CSV export

This prevents context window overflow from raw candidate dumps. [ASSUMED — token math not verified, but a 100-candidate JSON blob with 15 score fields is ~15KB, which is material but not catastrophic at Sonnet's 200K context]

### Recommended File Structure

```
backend/
├── agent/
│   ├── reference/
│   │   ├── 01_tool_selection_guide.md      # existing
│   │   ├── 02_technical_setup_guide.md     # existing
│   │   ├── 03_metric_profiles.md           # NEW — Leo authors thresholds
│   │   └── 04_guidance_profiles.md         # NEW — Leo authors protocols
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── cache.py                        # In-memory candidate cache
│   │   ├── pdb_features.py                 # BioPython clash/BSA/contacts
│   │   ├── ranking.py                      # Pandas-based ranking + filtering
│   │   └── report.py                       # fpdf2 PDF + CSV + Markdown export
│   ├── tools.py                            # Add 5 new tool definitions + handlers
│   └── system_prompt.py                    # Add _load_reference for profiles 03/04
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Buried Surface Area | Custom BSA geometry | `Bio.PDB.SASA.ShrakeRupley` | Implements Shrake-Rupley algorithm; handles all edge cases; project already imports biopython |
| Interface contact detection | Custom distance loops | `Bio.PDB.NeighborSearch` | Already used in `pdb_utils/interface.py`; spatial index is O(n log n) not O(n²) |
| Clash score detection | Custom van der Waals check | BioPython NeighborSearch with VdW radii dict | Approach established in literature; atom-pair distance < sum(VdW radii) is the standard algorithm |
| Multi-column candidate ranking | Custom sort logic | `pandas.DataFrame.sort_values()` + `rank()` | Percentile calculation is one line; multi-key sort is trivial; already in requirements.txt |
| CSV export | Manual string building | `pandas.DataFrame.to_csv()` | Handles quoting, encoding, headers correctly |
| PDF generation | HTML templating + Chrome/Puppeteer | `fpdf2` | Server-side PDF with no headless browser; Docker-safe |

**Key insight:** All structural analysis tools are already in the dependency tree (biopython 1.86). The BioPython `SASA.ShrakeRupley` class is available in biopython 1.79+. Never compute BSA by hand — it requires handling of probe radius, atomic radii per element, and surface point sampling correctly.

---

## Metric Intelligence: Scoring Profiles Reference

This section documents what Leo's metric profile files MUST contain. Leo authors the actual values; this section defines the schema and pre-populates known thresholds from the literature. [CITED: Australian Protein Design Initiative BindCraft workshop, GitHub martinpacesa/BindCraft]

### BindCraft Metrics

| Metric | Range | Green Threshold | Red Flag | Notes |
|--------|-------|----------------|----------|-------|
| `ipTM` (i_pTM) | 0–1 | > 0.7 strong; > 0.5 passable | < 0.45 | Good binary predictor of binding; NOT affinity predictor |
| `i_pAE` (interface PAE) | 0–1 normalized | < 0.4 | > 0.6 | Lower = better positional certainty at interface |
| `pLDDT` | 0–1 | > 0.8 strong | < 0.7 | AF2 confidence in backbone; correlates with foldability |
| `dG` (binding energy) | kcal/mol (negative) | < -30 | > -10 | Rosetta dG; more negative = more favorable |
| `dSASA` | Å² | > 800 | < 400 | Interface buried surface area; larger = more buried |
| `ShapeComplementarity` | 0–1 | > 0.65 | < 0.5 | Geometric fit; < 0.5 suggests poor packing |
| `Unrelaxed_Clashes` | count | 0 | > 5 | Steric clashes before Rosetta relaxation |
| `Relaxed_Clashes` | count | 0 | > 2 | Clashes after relaxation — critical; nonzero = structural problem |
| `Surface_Hydrophobicity` | fraction | < 0.4 | > 0.6 | High value predicts aggregation risk |
| `n_InterfaceResidues` | count | > 10 | < 6 | Too few contacts = weak binding surface |

[CITED: github.com/martinpacesa/BindCraft, Australian Protein Design Initiative workshop]

**Red flag combos Leo must define:**
1. High ipTM + low ShapeComplementarity — designs that score well on confidence but have geometric mismatch; likely false positive
2. Low dG + high Surface_Hydrophobicity — energetically favorable but aggregation-prone
3. Any Relaxed_Clashes > 0 — structural problem that survives Rosetta; deprioritize

### RFdiffusion / RFantibody Metrics

These tools output pAE and pLDDT (via ProteinMPNN + AF2 scoring). BindCraft metrics do not apply.

| Metric | Range | Green | Red Flag |
|--------|-------|-------|----------|
| `pLDDT` | 0–100 (raw AF2 scale) | > 80 | < 70 |
| `pAE` | Å | < 5 | > 10 |

[ASSUMED — RFdiffusion/RFantibody metric ranges based on AlphaFold2 standard outputs; Leo should verify against actual tool output format before authoring profile]

### BoltzGen / PXDesign Metrics

| Metric | Range | Green | Notes |
|--------|-------|-------|-------|
| `confidence` | 0–1 | > 0.8 | Boltz2 predicted confidence (analogous to ipTM) |

[ASSUMED — Boltz2 confidence metric interpretation; verify against Boltz2 documentation]

---

## BioPython Structural Feature Extraction

### BSA (Buried Surface Area) Calculation Pattern

BSA = SASA(target alone) + SASA(binder alone) - SASA(complex)

```python
# Source: Bio.PDB.SASA documentation, biopython.org/docs/latest/api/Bio.PDB.SASA.html
from Bio.PDB.SASA import ShrakeRupley

sr = ShrakeRupley(probe_radius=1.4, n_points=100)

# Calculate for complex, then each chain alone
sr.compute(complex_structure, level="R")  # residue-level SASA
sr.compute(target_structure, level="R")
sr.compute(binder_structure, level="R")

# BSA per residue = SASA_alone - SASA_complex
```

[CITED: biopython.org/docs/latest/api/Bio.PDB.SASA.html]

### Clash Score Calculation Pattern

```python
# Standard approach: atom-pair distance < sum of VdW radii => clash
# Source: literature standard (Haddox steric_clashing_metric on GitHub)
from Bio.PDB import NeighborSearch

VDW_RADII = {"C": 1.7, "N": 1.55, "O": 1.52, "S": 1.8}  # simplified

def count_clashes(chain_a_atoms, chain_b_atoms, tolerance=0.4):
    ns = NeighborSearch(chain_b_atoms)
    clashes = 0
    for atom_a in chain_a_atoms:
        radius_a = VDW_RADII.get(atom_a.element, 1.5)
        # Check neighbors within max possible clash distance
        nearby = ns.search(atom_a.coord, radius_a + max(VDW_RADII.values()) + tolerance)
        for atom_b in nearby:
            radius_b = VDW_RADII.get(atom_b.element, 1.5)
            dist = atom_a - atom_b
            if dist < (radius_a + radius_b - tolerance):
                clashes += 1
    return clashes // 2  # each pair counted twice
```

[ASSUMED — VdW radii dict is simplified; production implementation should use complete CHARMM/Amber radii table]

### Contact Count Pattern

Already implemented in `backend/pdb_utils/interface.py`. Reuse `extract_interface_residues()` directly — it returns count of residues within distance cutoff. [VERIFIED: codebase read]

---

## PDB Download Pattern for Analysis

The `load_job_results` tool needs to download PDB files from MinIO/R2 for structural analysis. Pattern:

```python
# Use boto3 client already configured in storage/client.py
import boto3
from storage.client import get_s3_client
from config import settings

def download_pdb_to_tmp(pdb_key: str) -> str:
    """Download PDB from object storage to /tmp for BioPython analysis."""
    client = get_s3_client()
    local_path = f"/tmp/analysis_{pdb_key.replace('/', '_')}.pdb"
    client.download_file(settings.s3_bucket_name, pdb_key, local_path)
    return local_path
```

[VERIFIED: storage/client.py pattern + boto3 docs]

---

## PDF Report Generation

### fpdf2 Table Pattern

```python
# Source: py-pdf.github.io/fpdf2 Table documentation
from fpdf import FPDF

class KendrewReport(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Kendrew Design Analysis Report", align="C")
        self.ln(5)

pdf = KendrewReport()
pdf.add_page()
pdf.set_font("Helvetica", size=10)

# Table via fpdf2 Table context manager (fpdf2 >= 2.7.0)
with pdf.table(col_widths=(20, 30, 30, 30, 30)) as table:
    header_row = table.row()
    for col in ["Rank", "ipTM", "pLDDT", "dG", "Shape Comp"]:
        header_row.cell(col)
    for candidate in shortlisted:
        data_row = table.row()
        data_row.cell(str(candidate["rank"]))
        # ... other fields

pdf.output("/tmp/report.pdf")
```

[CITED: py-pdf.github.io/fpdf2] — fpdf2 2.7.0+ includes Table class; version 2.8.7 is current as of 2025.

### Report Storage & Download

Generated reports are ephemeral (generated on-demand, not stored permanently):
1. Agent generates report to `/tmp/report_{job_id}_{timestamp}.pdf`
2. Upload to MinIO/R2 under `users/{user_id}/reports/{job_id}/report.pdf`
3. Generate presigned GET URL (expires in 1 hour)
4. Return presigned URL to Claude, which embeds it in its response text
5. Frontend renders as a plain download link in the chat message

[ASSUMED — this is the simplest path that avoids a new DB table for report storage; presigned URL expiry is acceptable for ephemeral reports]

---

## Refolding Job Integration

### Job Submission Pattern (existing)

Refolding jobs (AF2-multimer, Boltz2) submit via the existing job dispatch path. The agent assembles:
1. Complex PDB = target chain (from original job_spec) + binder chain (from candidate pdb_key)
2. Submits as a new job with tool = "boltzgen" or similar validation tool
3. Returns job_id for user to track

**Critical:** The target PDB from the original job must still be accessible. It is stored in `job_spec.target_pdb_path` (a local container path during the original run). For refolding, the agent must fetch the target PDB from RCSB using the original PDB accession stored in `job_spec` — or from the upload if user-uploaded.

**Action for planner:** The `submit_refolding_job` tool must re-fetch the target structure using RCSB accession from `job_spec`. Do NOT attempt to reuse a container-local path from the original job — it no longer exists.

[VERIFIED: backend/agent/jobspec.py pattern + backend/jobs/router.py job_spec storage]

### Complex Assembly

Merge target + binder into a combined PDB:
- Target chain kept as-is (chain A or whatever was selected)
- Binder chain assigned chain B (or next available chain letter)
- Write merged PDB to `/tmp/complex_{job_id}_{rank}.pdb`
- Upload to MinIO, get presigned PUT URL via existing `generate_presigned_put_url`
- Submit refolding job with this PDB path in job_spec

```python
# Pattern using BioPython
from Bio.PDB import PDBParser, PDBIO, Select

class ChainSelect(Select):
    def __init__(self, chain_id):
        self.chain_id = chain_id
    def accept_chain(self, chain):
        return chain.get_id() == self.chain_id
```

---

## Export Report Button Integration

D-15 specifies the "Export Report" button on the job page pre-populates a chat prompt. Pattern:

Frontend: Button on `JobPage.tsx` → injects prompt text into `ChatInput` using the existing `injectedValue` prop pattern (established in Phase 6, `ChatInput injectedValue prop pattern`). [VERIFIED: STATE.md accumulated context]

Prompt to inject: `"Generate a full analysis report for job {job_id} with shortlisted candidates, metric explanations, and next steps."`

The agent handles this like any other user message — calls `load_job_results`, `analyze_candidates`, `flag_red_flags`, then `generate_report` in sequence.

---

## Common Pitfalls

### Pitfall 1: Context Window Overflow from Raw Candidate Dumps

**What goes wrong:** `load_job_results` returns all 100+ BindCraft candidates as a JSON blob. At ~150 tokens per candidate (15 score fields), 100 candidates = ~15K tokens. With system prompt + conversation history, this can push against the 200K limit after several turns.
**Why it happens:** Tool results are injected into the Claude message array verbatim.
**How to avoid:** Implement the summary-page pattern: return top 20 + distribution stats (min/max/mean/p25/p75). User can drill down with follow-up queries.
**Warning signs:** Claude's responses become truncated, or API returns `context_window_exceeded` error.

### Pitfall 2: Presigned URL Expiry in Reports

**What goes wrong:** Report PDF contains presigned PDB download links that expire in 1 hour. User opens report 2 hours later and links are dead.
**Why it happens:** Default presigned URL expiry in `generate_presigned_get_url` is 3600s.
**How to avoid:** For report PDB links, use `expires_in=86400` (24 hours) when generating links for report inclusion. Or document this limitation clearly in the report footer.
**Warning signs:** User reports "download link expired" from reports.

### Pitfall 3: BioPython SASA on Malformed Designed PDB

**What goes wrong:** Designed PDB files from BindCraft/RFdiffusion may have non-standard residue names (BFP, MSE, etc.) or missing OXT atoms. `ShrakeRupley.compute()` raises `KeyError` or silently assigns zero SASA.
**Why it happens:** VdW radii dict in BioPython SASA uses standard amino acid atoms only.
**How to avoid:** Wrap SASA computation in try/except; fall back to `None` for BSA if computation fails. The existing `pdb_utils/normalize.py` already converts MSE to MET — run normalization before SASA.
**Warning signs:** Zero BSA values across all candidates from a single tool.

### Pitfall 4: dispatch_tool Missing user_id for Refolding

**What goes wrong:** `submit_refolding_job` needs to create a job row in DB with the user's ID, but `dispatch_tool` currently accepts `user_id=""` as default.
**Why it happens:** The existing `_handle_validate_preflight` already has this problem — user_id is passed into `dispatch_tool` from `router.py`. It is passed but the parameter flow must be traced.
**How to avoid:** Verify that `user_id` is passed through the `dispatch_tool` call chain from `router.py` → `dispatch_tool` → `_handle_submit_refolding_job`. The router already passes `user_id` [VERIFIED: agent/router.py line 141].
**Warning signs:** Refolding jobs created with NULL user_id, violating FK constraint.

### Pitfall 5: Metric Profiles Not Loaded if Reference File Missing

**What goes wrong:** `_load_reference()` in `system_prompt.py` returns empty string silently if the file doesn't exist. Agent gives generic metric interpretations instead of Kendrew-calibrated ones.
**Why it happens:** The `_load_reference` function has `if path.exists() else ""` — designed for progressive loading but masks missing files.
**How to avoid:** Wave 0 must create placeholder metric profile files. Add an assertion in tests that the profile files are non-empty.
**Warning signs:** Agent refers to "typical thresholds" without specific Kendrew numbers.

### Pitfall 6: Cross-Tool Metric Confusion

**What goes wrong:** BindCraft scores (`ipTM`, `dG`, `ShapeComplementarity`) are in the candidate scores JSONB. RFdiffusion scores (`pLDDT`, `pAE`) are different keys. Agent applies wrong metric profile to wrong tool.
**Why it happens:** `scores` is freeform JSONB — no tool tag on the candidate level (tool is only on the parent job).
**How to avoid:** `load_job_results` must fetch the parent job's `tool` field and include it in the response alongside candidates. The metric profile selection is keyed on `tool`.
**Warning signs:** Agent interprets pAE values as dG values or vice versa.

---

## Runtime State Inventory

This is a greenfield feature addition (no rename/migration). No runtime state changes required.

- Stored data: None — new `reports/` prefix in MinIO is ephemeral (presigned, no DB tracking)
- Live service config: None
- OS-registered state: None
- Secrets/env vars: None (fpdf2 requires no API key)
- Build artifacts: fpdf2 added to requirements.txt — Docker image rebuild required

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|---------|
| biopython | PDB structural analysis | Docker (in requirements.txt) | 1.86 | None needed |
| pandas | Candidate ranking + CSV | Docker (in requirements.txt) | 2.2.3 | None needed |
| fpdf2 | PDF report generation | Not yet in requirements.txt | 2.8.7 (to add) | reportlab (heavier) |
| boto3 | PDB download from MinIO | Docker (in requirements.txt) | 1.42.71 | None needed |
| pyyaml | Metric profile loading | Likely transitive dep — verify | ~6.0 | json profiles instead |

**Missing dependencies with no fallback:**
- fpdf2 — must be added to requirements.txt; Docker image rebuild required

**Missing dependencies with fallback:**
- pyyaml — if not available as transitive dep, use JSON for profile files (no behavior change, just file format)

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3.5 + pytest-asyncio 0.24.0 |
| Config file | pytest.ini (existing) |
| Quick run command | `pytest backend/tests/agent/ -x -q` |
| Full suite command | `pytest backend/tests/ -q` |

### Phase Requirements to Test Map

| Behavior | Test Type | Automated Command | File Status |
|----------|-----------|-------------------|-------------|
| load_job_results returns candidates for complete job | unit | `pytest backend/tests/agent/test_analysis_tools.py::test_load_job_results -x` | Wave 0 |
| load_job_results returns summary for >20 candidates | unit | `pytest backend/tests/agent/test_analysis_tools.py::test_load_job_results_large -x` | Wave 0 |
| analyze_candidates ranks by user-specified metric | unit | `pytest backend/tests/agent/test_analysis_tools.py::test_analyze_candidates_ranking -x` | Wave 0 |
| flag_red_flags detects high Relaxed_Clashes | unit | `pytest backend/tests/agent/test_analysis_tools.py::test_red_flag_clashes -x` | Wave 0 |
| pdb_features extracts BSA from two-chain PDB | unit | `pytest backend/tests/agent/test_pdb_features.py::test_bsa_calculation -x` | Wave 0 |
| clash score returns 0 for non-clashing chains | unit | `pytest backend/tests/agent/test_pdb_features.py::test_no_clashes -x` | Wave 0 |
| generate_report returns PDF bytes | unit | `pytest backend/tests/agent/test_report.py::test_pdf_generation -x` | Wave 0 |
| generate_report returns valid CSV string | unit | `pytest backend/tests/agent/test_report.py::test_csv_generation -x` | Wave 0 |
| submit_refolding_job creates job row in DB | integration | `pytest backend/tests/agent/test_analysis_tools.py::test_submit_refolding -x` | Wave 0 |
| TOOL_DEFINITIONS includes 5 new tools | unit | `pytest backend/tests/agent/test_tools.py -x` (extend existing) | Extend existing |

### Sampling Rate

- Per task commit: `pytest backend/tests/agent/test_analysis_tools.py -x -q`
- Per wave merge: `pytest backend/tests/ -q`
- Phase gate: Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `backend/tests/agent/test_analysis_tools.py` — covers load_job_results, analyze_candidates, flag_red_flags, submit_refolding_job
- [ ] `backend/tests/agent/test_pdb_features.py` — covers BSA, clash score, contact count
- [ ] `backend/tests/agent/test_report.py` — covers PDF, CSV, Markdown generation
- [ ] `backend/tests/fixtures/two_chain.pdb` — minimal two-chain PDB for structural analysis tests
- [ ] fpdf2 install: add to `requirements.txt`

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes (refolding job creation) | `get_current_user` dependency — already applied to all agent endpoints |
| V4 Access Control | Yes (load_job_results must verify job belongs to user) | Query includes `user_id` ownership check — same pattern as `jobs/router.py` |
| V5 Input Validation | Yes (metric filter inputs, report parameters) | Validate metric names against known set; reject unknown filter keys |
| V6 Cryptography | No | No new crypto; presigned URLs via existing boto3 pattern |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| IDOR on load_job_results (user requests another user's job results) | Spoofing | `WHERE job_id = $1 AND user_id = $2` ownership check in all DB queries |
| Path traversal in PDB download to /tmp | Tampering | Sanitize pdb_key before constructing local path (`os.path.basename` + allow-list prefix check) |
| Excessive resource use from BSA on large PDB | DoS | BioPython SASA on a 500-residue protein takes ~2s; run in executor with timeout |
| Report file size abuse (user triggers huge PDF) | DoS | Cap shortlist at 50 candidates in report; log and reject larger requests |

---

## Project Constraints (from CLAUDE.md)

- Primary language: Python; no heavyweight frameworks
- BioPython, NumPy, Pandas, Matplotlib/Seaborn are preferred libraries — pandas for ranking is compliant
- All functions must have Google-style docstrings
- Never hardcode file paths — use config or CLI args (use `settings` for S3 bucket, `/tmp` paths are acceptable for ephemeral analysis files)
- No silent exception passes — wrap BioPython calls with explicit error handling and informative messages
- Smoke tests required for any repeatedly-used tool
- Kendrew branding on PDF reports — not Ranomics

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | pyyaml is available as a transitive dependency | Standard Stack | Minor — switch to JSON profiles with no behavior change |
| A2 | In-memory module-level cache is process-safe with uvicorn single-worker | Architecture Patterns | Medium — with multiple workers, cache misses on DB re-fetch only; not a data correctness issue |
| A3 | 100-candidate JSON blob is ~15K tokens (manageable but worth summary-paging) | Pitfall 1 | Low — worst case is context pressure; summary-page pattern recommended regardless |
| A4 | Boltz2 confidence metric is 0–1 analogous to ipTM | Metric Intelligence | Medium — Leo must verify against actual Boltz2 output before authoring guidance profile |
| A5 | RFdiffusion outputs pLDDT on 0–100 scale (raw AF2), not 0–1 | Metric Intelligence | Medium — actual output scale varies by scoring script version; Leo must check pipeline output format |
| A6 | Generated reports stored as ephemeral MinIO objects (no DB row) is acceptable | PDF Report | Low — if report persistence across sessions is needed, add a `reports` table; deferred to v2 |
| A7 | Simplified VdW radii dict is sufficient for clash detection (not full Amber/CHARMM) | BioPython Patterns | Low — clash detection is a red flag signal, not a precise measurement; full radii adds little value |

---

## Open Questions

1. **Refolding tool selection: AF2-multimer vs Boltz2**
   - What we know: D-06 says "AF2-multimer, Boltz2" — both are options
   - What's unclear: Are these available as deployed RunPod tools in Phase 8, or must Phase 8 add new tool images?
   - Recommendation: The plan should add a refolding-specific tool image (boltzgen image may already handle complex structure prediction). Confirm with Leo which tools are currently deployed.

2. **ipTM vs pLDDT scale in BindCraft output**
   - What we know: BindCraft normalizes some metrics to 0–1 scale; some sources show pLDDT as 0–1, others as 0–100
   - What's unclear: The actual key names and scales in the `scores` JSONB as populated by `BindCraftPipeline.parse_results()`
   - Recommendation: Leo should print actual scores dict from a real BindCraft run before authoring metric profile thresholds. The profile template should include a `_note: "verify scale from live run"` comment.

3. **Session-to-job linkage**
   - What we know: D-02 says analysis happens in the same session as job launch; the agent has job_spec context in its conversation history
   - What's unclear: When the user says "analyze my results" — how does the agent know which job_id to load? User may have launched multiple jobs in one session.
   - Recommendation: `load_job_results` tool should take an explicit `job_id` parameter. The agent must ask "which job would you like to analyze?" if ambiguous, or parse the most recent job_id from the conversation history (from the review card tool_result).

---

## Sources

### Primary (HIGH confidence)
- Codebase: `backend/agent/router.py`, `backend/agent/tools.py`, `backend/agent/system_prompt.py` — existing tool dispatch and system prompt patterns
- Codebase: `backend/pdb_utils/interface.py` — BioPython NeighborSearch pattern in production use
- Codebase: `backend/pipelines/bindcraft.py` — actual score field names in CandidateResult
- Codebase: `supabase/migrations/20260319000002_billing_and_results.sql` — job_candidates schema
- Codebase: `backend/requirements.txt` — confirmed library versions
- [biopython.org/docs/latest/api/Bio.PDB.SASA.html](https://biopython.org/docs/latest/api/Bio.PDB.SASA.html) — ShrakeRupley SASA API
- [pypi.org/project/fpdf2](https://pypi.org/project/fpdf2/) — fpdf2 2.8.7 pure-Python, no system deps

### Secondary (MEDIUM confidence)
- [github.com/martinpacesa/BindCraft](https://github.com/martinpacesa/BindCraft) — BindCraft metric names, filter thresholds, design philosophy
- [Australian Protein Design Initiative BindCraft workshop](https://australian-protein-design-initiative.github.io/binder-design-workshop/bindcraft_scoring.html) — metric interpretation guidance
- [github.com/py-pdf/fpdf2](https://github.com/py-pdf/fpdf2) — Table class API confirmed active

### Tertiary (LOW confidence — assumptions flagged above)
- BoltzGen confidence metric scale (A4) — not verified against live output
- VdW radii dict completeness (A7) — simplified from literature

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified in requirements.txt; fpdf2 version verified via pypi
- BioPython patterns: HIGH — NeighborSearch and SASA confirmed in biopython 1.86; BSA pattern from official docs
- BindCraft metric profiles: MEDIUM — metric names confirmed from GitHub; thresholds are literature-based, Leo must verify against real run output
- Architecture patterns: HIGH — all tool dispatch patterns verified from existing codebase
- PDF generation: HIGH — fpdf2 Table API verified; pure-Python confirmed

**Research date:** 2026-04-10
**Valid until:** 2026-05-10 (30 days — stable library ecosystem)
