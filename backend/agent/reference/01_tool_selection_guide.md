# De Novo Protein Design — Tool Selection Guide

> **Agent role:** Use this document to understand each tool, recommend the right one for a given target and biologic type, explain trade-offs, and anticipate failure modes. Pair with `02_technical_setup_guide.md` for execution details.
>
> **Stack:** This platform runs five tools — RFdiffusion, BindCraft, RFantibody, BoltzGen, PXDesign. All are open-source, self-hostable on cloud GPU, and free for commercial use.

---

## Table of Contents

1. [Decision Framework](#1-decision-framework)
2. [Shared Vocabulary & Key Metrics](#2-shared-vocabulary--key-metrics)
3. [Tool Profiles](#3-tool-profiles)
   - [RFdiffusion](#31-rfdiffusion)
   - [BindCraft](#32-freebindcraft)
   - [RFantibody](#33-rfantibody)
   - [BoltzGen](#34-boltzgen)
   - [PXDesign](#35-pxdesign)
4. [Tool Comparison Matrix](#4-tool-comparison-matrix)
5. [Common Failure Modes](#5-common-failure-modes)
6. [Experimental Validation Guidance](#6-experimental-validation-guidance)
7. [Agent Conversation Guide](#7-agent-conversation-guide)

---

## 1. Decision Framework

Work through these questions in order when a user presents a design task.

---

### Step 1 — What biologic format is the user designing?

| Biologic Format | Recommended Tool(s) | Notes |
|---|---|---|
| Small miniprotein binder (40–150 aa) | **RFdiffusion**, **BindCraft**, **BoltzGen**, **PXDesign** | RFdiffusion / PXDesign for high throughput; BindCraft for highest hit rate per GPU-hour; BoltzGen for universal modality coverage |
| VHH / nanobody | **RFantibody**, **BoltzGen** (`nanobody-anything`) | RFantibody best paired with YSD screening; BoltzGen for end-to-end without YSD |
| scFv (single-chain variable fragment) | **RFantibody** | CDR-loop-mediated interface required |
| Full-length IgG / mAb | **Not available on this platform** | Chai-2 (closed-source) and IgGM (PyRosetta dependency) are not deployable; inform user that full-length IgG de novo design is not currently supported |
| Cyclic peptide | **Not available on this platform** | BoltzGen's `peptide-anything` protocol runs, but the platform emits no `constraints: bond:` block, so it produces **linear** peptides only. Head-to-tail cyclisation has been demonstrated off-platform (bespoke Modal app, hand-written spec YAML) at a low closure rate. Offer a linear peptide binder, or route the request to a bespoke run. |
| Disulfide-bonded peptide | **Not available on this platform** | Same gap as cyclic peptides: BoltzGen accepts covalent bond constraints upstream, but the platform never generates them. Off-platform only. |
| Protein binder to a small molecule | **BoltzGen** (`protein-small_molecule` protocol) | Unique capability among the available tools |
| Symmetric oligomer / nanoparticle | **Not available on this platform** | RFdiffusion supports symmetry upstream, but the platform never passes `inference.symmetry` and only builds fixed-target-plus-binder contigs. Do not offer it, even though `symmetric_assembly` appears in the intent classifier. |
| Enzyme active site scaffold | **RFdiffusion** (motif scaffolding mode) | Well-validated use case |

---

### Step 2 — What is known about the target?

| Target Information | Implication |
|---|---|
| Experimental crystal/cryo-EM structure | All tools applicable; best starting point |
| AlphaFold2 or Boltz-2 predicted structure | Acceptable for all tools |
| Specific epitope / hotspot residues defined | Any tool with hotspot support; tighter control over binding site |
| No structural data at all | Predict structure first using AlphaFold2 or Boltz-2 before designing |
| Intrinsically disordered region (IDR) | **BoltzGen** (validated on IDRs); other tools require ordered structure |
| Small molecule as the target | **BoltzGen** (`protein-small_molecule`) |
| GPCR target | These are notoriously difficult; BoltzGen or BindCraft; expect lower hit rates |
| Membrane protein (extracellular domain only) | BindCraft or RFdiffusion; crop to extracellular domain only — do not include TM helices |
| Large target (>300 residues) | Crop to relevant domain; all tools benefit; BindCraft has hard VRAM limits |

---

### Step 3 — What compute resources are available?

| Scenario | Recommendation |
|---|---|
| Single GPU 24 GB | BindCraft (100–500 trajectories); RFdiffusion pilot (1,000–2,000 designs) |
| Multi-GPU cloud (A100/H100) | RFdiffusion or PXDesign at 10k+ scale; BoltzGen at 10k–60k scale |
| Fixed-runtime campaign needed | BoltzGen or PXDesign (deterministic throughput; unlike BindCraft which runs until quota is met) |
| HPC cluster (Slurm) | RFdiffusion or BoltzGen with job arrays; BindCraft with multiple single-GPU jobs |

---

### Step 4 — Licensing summary

All five tools are fully open-source and commercially deployable with no licensing friction:

| Tool | License | PyRosetta | Notes |
|---|---|---|---|
| RFdiffusion | BSD-3-Clause | None in core pipeline | Optional FastRelax step in separate repo uses PyRosetta — skip it |
| BindCraft | MIT | None | Full PyRosetta replacement with OpenMM, FreeSASA, sc-rs |
| RFantibody | MIT | None | — |
| BoltzGen | MIT | None | — |
| PXDesign | Apache 2.0 | None | Built on Protenix (Apache 2.0) |

---

### Step 5 — Which tool fits the target difficulty?

| Target Characteristic | Best Tool |
|---|---|
| Well-ordered, soluble protein with clear binding surface | Any tool; start with RFdiffusion or BindCraft |
| Difficult target (flat epitope, flexible surface) | BindCraft (induced-fit; re-evaluates interface at every optimization step) |
| Novel target (<30% PDB sequence identity to any bound structure) | BoltzGen (validated on this) |
| High throughput campaign, highest experimental hit rates | PXDesign (20–73% nanomolar hit rates; multi-predictor filtering) |
| Antibody-specific target (requires CDR loops) | RFantibody |

---

## 2. Shared Vocabulary & Key Metrics

### Structure Design Terms

**Hotspot residues:** Residues on the target that the designed binder must contact. Typically 3–6 residues. The model is trained to make additional contacts beyond what is specified — provide fewer than the total expected contacts.

**Contig (RFdiffusion-specific):** Notation string describing which parts of the input PDB are fixed (target) vs. being designed (binder). `[A1-150/0 50-100]` means: chain A residues 1–150 are fixed target, `/0` is a chain break, and 50–100 residues of de novo binder will be designed. Length ranges are randomly sampled at each trajectory.

**Motif scaffolding:** Designing a protein that holds a functional motif in the correct geometry by building supporting scaffold around it.

**Induced-fit interface:** Design mode where the target protein is allowed to flex during design rather than being held rigid. BindCraft implements this via iterative AF2 backpropagation. RFdiffusion and PXDesign-d hold the target rigid.

**CDR loops:** The six variable loops on antibodies (CDR-H1, H2, H3 heavy; CDR-L1, L2, L3 light) that mediate antigen binding. CDR-H3 is the most variable and hardest to design.

### Key Quality Metrics

**pLDDT:** Per-residue confidence score from AF2/Boltz/Protenix (0–100). >80 = well-folded.

**ipTM (interface predicted Template Modeling score):** Predicted structural quality of the protein-protein interface (0–1). Typical passing threshold: **>= 0.70–0.80**. ipTM is a **binary predictor of binding likelihood only — it does not predict affinity magnitude.**

**ipSAE (BindCraft-specific):** Alternative interface metric using per-residue d0 normalization based on interface contact count. Range 0–1; higher is better. Used via `--rank-by ipSAE`.

**i_pAE (interface predicted Aligned Error):** Predicted uncertainty in binder-target relative orientation. Lower is better. Typical threshold: **<= 10 A**.

**Binder_RMSD (BindCraft-specific):** Backbone RMSD of binder in complex vs. binder alone. Low RMSD (<= 1.5 A) = binder folds the same way with or without target.

**Shape complementarity (Sc):** Geometric fit between binder and target surfaces (0–1). Default threshold: **>= 0.60**.

**SAP score:** Spatial Aggregation Propensity. Predicts aggregation risk. Lower is better; >5 indicates risk.

**Critical rule — ipTM != affinity:** Always remind users that computational metrics predict whether binding will occur, not how tightly. Kd must be measured experimentally by SPR, BLI, or ITC.

---

## 3. Tool Profiles

### 3.1 RFdiffusion

**Repository:** https://github.com/RosettaCommons/RFdiffusion
**License:** BSD-3-Clause
**Origin:** Baker Lab, University of Washington. Watson et al., *Nature* 2023.

#### Mechanism

Denoising diffusion probabilistic model (DDPM) fine-tuned from RoseTTAFold. Operates on protein backbone frames. Iteratively denoises from Gaussian noise over 200 timesteps conditioned on the target PDB, hotspot residues, symmetry specs, or functional motifs. Outputs backbone structures only — all designed residues output as glycine. **Sequence must be assigned downstream by ProteinMPNN.**

The target is held rigid throughout diffusion. The binder is designed around the fixed target surface.

#### What It Designs Best

- Miniprotein binders (40–150 aa) — most validated use case in the field
- Symmetric oligomers (C2–C12, D2, tetrahedral, octahedral, icosahedral)
- Enzyme active site scaffolds (motif scaffolding)
- Metal-binding proteins
- Partial diffusion / loop remodeling of existing proteins

#### What It Cannot Design

- CDR-loop antibody interfaces — **RFantibody**
- Small molecule binder proteins — **BoltzGen**
- Cyclic or disulfide-bonded peptides — not available on this platform (see Step 1)
- Induced-fit interfaces — **BindCraft**

#### Advantages

- Most experimentally validated de novo binder tool (2023–2026); largest published precedent
- Flexible design modes: binder design, motif scaffolding, symmetry, partial diffusion
- Fast per-design runtime (~3–5 seconds/design on A100 for 150-residue systems)
- Excellent structural diversity; large community; extensive documentation

#### Limitations

- Heavily biased toward helical binders by default. Upstream offers `beta_ckpt.pt` for beta topologies, but **that checkpoint is not in the platform image and cannot be selected** — the platform always runs `Complex_base_ckpt.pt`. Helical bias is not tunable here.
- Low in silico pass rate (~1–5%); thousands to tens of thousands of designs required
- Outputs backbone only — sequence design is a separate step
- Target held rigid; cannot model induced-fit interfaces

---

### 3.2 BindCraft

**Repository:** https://github.com/cytokineking/FreeBindCraft
**Implementation note:** This platform uses the FreeBindCraft fork throughout.
**License:** MIT
**Origin:** Original BindCraft: Martin Pacesa, EPFL / Ovchinnikov Lab, *Nature* 2025. FreeBindCraft fork: Aaron Ring / cytokineking.

#### What BindCraft Is

Community fork of the original `martinpacesa/BindCraft` that replaces the PyRosetta dependency with fully open-source alternatives. Design logic, AF2 backpropagation hallucination, ProteinMPNN integration, and filtering architecture are **identical** to original BindCraft. Only the backend implementations of specific scoring functions differ.

#### Mechanism (4 Automated Stages Per Trajectory)

1. **AF2 Multimer Hallucination** — gradient descent on interface loss (ipTM, i_pAE, pLDDT) via ColabDesign; binder initialized from random length/composition
2. **ProteinMPNN Sequence Redesign** — sequences redesigned on the hallucinated backbone
3. **AF2 Monomer Validation** — binder repredicted in isolation using AF2 monomer model (deliberately stringent cross-check)
4. **Interface Analysis** — OpenMM relaxation, FreeSASA, sc-rs shape complementarity, RMSD, SAP score

Key distinction from RFdiffusion and PXDesign: the interface is **re-evaluated at every optimization step** (induced-fit).

#### What It Designs Best

- Miniprotein binders, especially where flexible or concave binding surfaces require induced-fit

Upstream FreeBindCraft ships peptide-oriented advanced-settings presets, but the platform hardwires `default_4stage_multimer.json` and exposes no binder-length control for BindCraft — every run is a 50–100 aa miniprotein. **Do not offer BindCraft for peptides.**

#### What It Cannot Design

- CDR-loop antibody interfaces — **RFantibody**
- Small molecule binders — **BoltzGen**
- Symmetric oligomers — **RFdiffusion**

#### Advantages

- Induced-fit design captures flexible interfaces missed by rigid-target methods
- Fully automated end-to-end: input target PDB to ranked sequences ready to order
- No PyRosetta; fully MIT licensed
- ~3x faster than original BindCraft

#### Limitations

- Cannot be parallelized across multiple GPUs on a single instance
- Hard VRAM limits constrain target size
- H-bond network metrics absent (expected behavior — no open-source equivalent)
- No guaranteed throughput per unit time (runs until filter quota is met)

---

### 3.3 RFantibody

**Repository:** https://github.com/RosettaCommons/RFantibody
**License:** MIT
**Origin:** Bennett, Watson, Ragotte, Baker et al., Baker Lab. *Nature* 2025.

#### Mechanism

Fine-tuned variant of RFdiffusion, retrained on antibody–antigen complex structures. Three integrated components:

1. **RFdiffusion (antibody fine-tune):** CDR loops diffused around user-specified epitope
2. **ProteinMPNN (AbMPNN weights):** Designs CDR sequences
3. **Fine-tuned RoseTTAFold2 (RF2):** Antibody-antigen complex validation

#### What It Designs Best

- VHH nanobodies (single-domain)
- scFv fragments (single-chain VH–VL pairs)
- Precise epitope targeting via CDR loop design

#### What It Cannot Design

- Non-antibody miniprotein binders — **RFdiffusion** or **BindCraft**
- Full-length IgG with Fc domain — **not available on this platform**
- Small molecule binders — **BoltzGen**

#### Limitations

- Lower per-design hit rate than miniprotein tools; requires library screening (yeast display)
- CDR-H3 loop geometry is the primary bottleneck
- Requires antibody-specific RF2 weights

---

### 3.4 BoltzGen

**Repository:** https://github.com/HannesStark/boltzgen
**License:** MIT
**Origin:** Hannes Stark, Barzilay Lab / Jaakkola Lab, MIT. *bioRxiv* November 2025.

#### Mechanism

All-atom generative diffusion model that **simultaneously co-designs backbone, sequence, and sidechain packing** in a single forward pass. YAML-based design specification.

Built-in design protocols:
- `protein-anything`: de novo protein binders (minibinders)
- `nanobody-anything`: VHH/nanobody CDR design
- `antibody-anything`: VH-VL antibody CDR design
- `peptide-anything`: linear peptide binders (cyclic requires bond constraints the platform does not emit; see Step 1)
- `protein-small_molecule`: small molecule binders
- `protein-redesign`: template-based protein optimization

#### What It Designs Best

Most universally capable tool on this platform — miniprotein binders, VHH nanobodies, small molecule binders, proteins targeting IDRs.

Upstream BoltzGen also does cyclic and disulfide-bonded peptides via covalent bond constraints. **The platform does not generate those constraints**, so they are off-platform (bespoke run) only.

#### Known Issues

- **Ubiquitin contamination bug:** Requesting binders in the 73–76 aa range frequently outputs ubiquitin-like structures — always BLAST-check designs in this length range

---

### 3.5 PXDesign

**Repository:** https://github.com/bytedance/PXDesign
**License:** Apache 2.0 (free for commercial use)
**Origin:** ByteDance Seed AI4Science Team. *bioRxiv* August 2025.

#### Mechanism

Model suite built on the Protenix foundation model. Two design components:
- **PXDesign-d:** DiT-style diffusion model for backbone generation
- **PXDesign-h:** Hallucination-based alternative using AF2 backpropagation

Key contribution: **multi-predictor confidence filtering** combining metrics from AF2 Initial Guess (AF2-IG) and Protenix.

#### What It Designs Best

- Miniprotein binders to diverse protein targets
- High experimental hit rates: 20–73% nanomolar binders across tested targets (filtered candidates)
- PXDesign-d is more throughput-efficient than hallucination methods for large campaigns

#### What It Cannot Design

- CDR-loop antibody interfaces — **RFantibody**
- Small molecule binders — **BoltzGen**
- Symmetric oligomers — **RFdiffusion**

---

## 4. Tool Comparison Matrix

| Tool | Primary Format | Mechanism | License | Min VRAM | Design Scale |
|---|---|---|---|---|---|
| **RFdiffusion** | Miniprotein binder (target-conditioned only) | Backbone diffusion (DDPM) | BSD-3 | 8 GB | 10k–50k |
| **BindCraft** | Miniprotein (50–100 aa, fixed) | AF2 backprop hallucination | MIT | 24 GB | 100–5k |
| **RFantibody** | VHH, scFv, antibody | Antibody-fine-tuned diffusion | MIT | 8 GB | 5k–20k |
| **BoltzGen** | Miniprotein, VHH, antibody, linear peptide, small molecule | All-atom co-design diffusion | MIT | 24 GB | 10k–60k |
| **PXDesign** | Miniprotein | Diffusion + hallucination | Apache 2.0 | 16 GB | 5k–20k |

---

## 5. Common Failure Modes

### RFdiffusion
- All designs are long helices → Expected. `beta_ckpt.pt` is not available on this platform; switch tool (BindCraft or PXDesign) rather than checkpoint.
- Designs don't contact hotspots → Use 3–6 residues in a concave pocket
- AF2 ipTM universally < 0.5 → Target surface too flat; use ConSurf/PDBePISA data

### BindCraft
- Zero designs passing all filter stages → Try `Relaxed` filter set
- H-bond metrics missing or zero → Expected FreeBindCraft behavior (placeholder values)
- All designs structurally identical → Increase temperature; run multiple separate jobs

### RFantibody
- CDR-H3 loops geometrically implausible → Vary CDR-H3 length range; generate 10k+ designs
- RF2 validation gives all low scores → Confirm antibody-specific RF2 weights are loaded

### BoltzGen
- Residue indexing errors → Re-index CIF so each chain starts at index 1 (use gemmi)
- Designs cluster around ubiquitin → Avoid 73–76 aa binder length range
- Small molecule pipeline fails → Verify CCD code is in moldir

### PXDesign
- Extended mode fails → MSA not provided or poor quality; pre-compute with ColabFold
- First run is very slow → Initial Protenix JIT compilation; expected

### General (All Tools)
- No experimental hits despite good ipTM → ipTM is binary only; screen more designs
- Expression failure → Add SAP score filter; use SolMPNN variant

---

## 6. Experimental Validation Guidance

### Critical Rule: Computational != Affinity

**No current computational metric predicts binding affinity magnitude.** ipTM, ipSAE, pLDDT, and i_pAE are binary or qualitative indicators of binding likelihood only. Kd must always be measured by SPR, BLI, or ITC.

### Miniprotein Binders (RFdiffusion, BindCraft, BoltzGen, PXDesign)

1. Synthesis: E. coli periplasmic expression or SPPS
2. Primary screen: SPR or BLI at 1–10 uM
3. Hit confirmation: Kd measurement by SPR/BLI titration
4. Counter-screen: SPR against irrelevant protein
5. Order 5–20 from top-ranked passing designs

### Antibodies / Nanobodies (RFantibody)

1. Library construction: Yeast display with designed CDR sequences (10^4–10^6 sequences)
2. Selection: FACS sorting against fluorescent antigen (2–4 rounds)
3. Sequencing: NGS post-sort
4. Clone validation: Express top 20–50 NGS-enriched sequences
5. Binding confirmation: BLI or SPR

---

## 7. Agent Conversation Guide

### Routing Questions

1. **Biologic format:** "What type of molecule are you designing — a small miniprotein binder, a nanobody/VHH, a scFv, a cyclic peptide, or something else?"
2. **Target:** "What is your target protein? Is there a PDB or AlphaFold structure available?"
3. **Binding site:** "Do you have specific residues you want the binder to contact (hotspots), or are you doing site-agnostic discovery?"
4. **Throughput intent:** "Are you running a quick pilot or a full production campaign?"
5. **Compute:** "What GPU tier are you deploying on — A100, H100, or something smaller?"

### What the Agent Should Always Say Before Closing

> "Regardless of which tool we use, computational metrics — ipTM, ipSAE, pLDDT — tell us whether a design is predicted to bind, not how tightly. Affinity must always be measured experimentally. Plan for expression and biophysical characterization as the next step after the computational campaign."
