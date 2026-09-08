# De Novo Protein Design — Technical Setup & Execution Guide

> **Agent role:** Use this document once a tool has been selected (see `01_tool_selection_guide.md`). Contains step-by-step setup, configuration, and execution instructions.
>
> **Stack:** RFdiffusion, BindCraft, RFantibody, BoltzGen, PXDesign.

---

## 1. Target Preparation (Universal)

### 1.1 Obtain Target Structure

**Option A — Experimental structure (preferred)**
- Download from RCSB PDB: https://www.rcsb.org
- Formats: .pdb (most tools) or .cif (BoltzGen and PXDesign require CIF)

**Option B — Predicted structure**
- AlphaFold2 via ColabFold
- Boltz-2 prediction: `boltz predict target.fasta --out_dir target_structure/ --use_msa_server`

### 1.2 Clean the PDB

Remove waters, heteroatoms, ligands. Keep only relevant chain(s). Crop to region of interest (leave ~10 A on each side of binding site).

### 1.3 Identify Hotspot Residues

1. Known co-crystal structure: residues within 5 A of binding partner
2. ConSurf evolutionary conservation
3. PDBePISA interface analysis
4. Published mutagenesis data (alanine scanning)

### 1.4 Convert PDB to CIF (BoltzGen and PXDesign)

Both require CIF format with chains starting at residue index 1. Use gemmi for conversion and re-indexing.

### 1.5 Pre-compute MSA (PXDesign Extended Mode)

PXDesign's Protenix-based filtering requires MSA for reliable confidence scoring. Use ColabFold or MMseqs2.

---

## 2. RFdiffusion

### Key Parameters

| Parameter | Description | Recommended |
|---|---|---|
| `contigmap.contigs` | Fixed/variable residue specification | `[A1-150/0 50-100]` |
| `ppi.hotspot_res` | Hotspot residues on target | `[A30,A55,A82]` — 3–6 residues |
| `inference.num_designs` | Number of backbone structures | 10,000 production; 100 pilot |
| `inference.ckpt_override_path` | Model weights | Complex_base_ckpt.pt for binders |
| `diffuser.partial_T` | For partial diffusion | 10–200 |

### Model Weight Selection

| Weight File | Use Case |
|---|---|
| `Complex_base_ckpt.pt` | Protein binder design (default) |
| `Base_ckpt.pt` | Unconditional monomer design |
| `ActiveSite_ckpt.pt` | Enzyme active site scaffolding |
| `InpaintSeq_ckpt.pt` | Motif scaffolding + sequence masking |
| `Complex_beta_ckpt.pt` | Non-helical topologies (less validated) |

### Design Modes

1. **Protein Binder Design** — Standard contig with hotspots
2. **Production Scale** — Parallel batches of 1000 designs
3. **Motif Scaffolding** — ActiveSite checkpoint with motif contigs
4. **Symmetric Oligomer** — Symmetry flag (C3, D2, etc.)
5. **Partial Diffusion** — Loop remodeling with partial_T

### Post-Generation Pipeline

1. ProteinMPNN sequence design (fix target chain, design binder)
2. AlphaFold2 validation (multimer prediction)
3. Filter: ipTM >= 0.70–0.80, pLDDT >= 80, i_pAE <= 10 A, buried SASA >= 800 A^2

These are conventional post-hoc cutoffs you apply yourself to raw AF2 output, on **raw scales**
(pLDDT 0–100, pAE in A). They are not BindCraft's filters and must not be quoted as such —
BindCraft ships its own `default_filters.json` on normalized 0–1 scales with different values.
See §3, *Default Filter Thresholds*.

### Hardware

| GPU | Max Target Size | Designs/Hour |
|---|---|---|
| RTX 3090 / A5000 (24 GB) | ~200 residues | 400–600 |
| A100 40 GB | ~350 residues | 800–1200 |
| H100 SXM 80 GB | ~600 residues | 1500–2500 |

---

## 3. BindCraft (FreeBindCraft Fork)

### Target JSON Configuration

```json
{
    "design_path": "/path/to/outputs/MyTarget/",
    "binder_name": "MyTarget",
    "starting_pdb": "/path/to/target_clean.pdb",
    "chains": "A",
    "target_hotspot_residues": "30,55,82",
    "lengths": [65, 150],
    "number_of_final_designs": 100
}
```

### Design Protocol Selection

| Protocol | Use Case |
|---|---|
| `default_4stage_multimer.json` | Default — most targets; helical binder bias |
| `beta_sheet_4stage_multimer.json` | Non-helical binders |
| `peptide_4stage_multimer.json` | Helical peptide binders (20–40 aa) |

### Filter Sets

| Filter Set | Use Case |
|---|---|
| `default_filters.json` | Standard — most targets |
| `relaxed_filters.json` | Difficult targets with no designs passing default |
| `peptide_filters.json` | For peptide protocol |

### Default Filter Thresholds

Verified against `settings_filters/default_filters.json` on FreeBindCraft `master` — the filter set
`backend/pipelines/bindcraft.py` selects by default. The names below are the literal JSON keys.
That file has 218 top-level keys holding 56 active thresholds: 18 at the average level (the 17 rows
below — `Average_InterfaceAAs` carries two, K and M), plus 38 per-model duplicates (see the per-model
note below). Every other key is `"threshold": null` and rejects nothing.

| Filter key | Pass condition | Scale | Backend |
|---|---|---|---|
| `Average_pLDDT` | >= 0.80 | **0–1, not 0–100** | AF2 |
| `Average_Binder_pLDDT` | >= 0.80 | 0–1 | AF2 |
| `Average_pTM` | >= 0.55 | 0–1 | AF2 |
| `Average_i_pTM` | >= 0.50 | 0–1 | AF2 |
| `Average_i_pAE` | <= 0.35 | **normalized 0–1, not raw A** | AF2 |
| `Average_Binder_RMSD` | <= 3.5 A | A | Biopython, CA-only, **no superposition** |
| `Average_Hotspot_RMSD` | <= 6 A | A | Biopython, CA-only, **no superposition** |
| `Average_ShapeComplementarity` | >= 0.60 | 0–1 | `sc-rs` CLI (MIT) |
| `Average_Surface_Hydrophobicity` | <= 0.35 | 0–1 fraction of binder-monomer SASA | FreeSASA, Biopython `ShrakeRupley` fallback |
| `Average_dSASA` | >= 1 A^2 | A^2, binder + target sides summed | FreeSASA / Biopython |
| `Average_n_InterfaceResidues` | >= 7 | count | Biopython contact search |
| `Average_InterfaceAAs` K, M | <= 3 each | count | Biopython |
| `Average_Binder_Loop%` | <= 90 | percent | Biopython + DSSP |
| `Average_Binder_Energy_Score` | <= 0 | — | **Not computed — see below** |
| `Average_dG` | <= 0 | — | **Not computed — see below** |
| `Average_n_InterfaceHbonds` | >= 3 | count | **Not computed — see below** |
| `Average_n_InterfaceUnsatHbonds` | <= 4 | count | **Not computed — see below** |

**There is no clash filter and no SAP filter in the default set.** `Average_Unrelaxed_Clashes` and
`Average_Relaxed_Clashes` both have null thresholds, so clashes reject nothing by default (they are
still computed, and are still worth reporting — see `03_metric_profiles.md`); also null are
`Average_PackStat`, `Average_pAE`, `Average_i_pLDDT`, `Average_Interface_SASA_%` and
`Average_dG/dSASA`. No key named `SAP` exists anywhere in the file — the closest quantity is
`Average_Surface_Hydrophobicity`, defined in `01_tool_selection_guide.md` under *Key Quality
Metrics*. It is a different measurement, not a renamed SAP.

Some thresholds mean less than they look like. `Average_dSASA >= 1 A^2` only checks that an interface
exists at all — it is not a burial requirement, so never present it as an 800 A^2-style cutoff. The
800 A^2 figures elsewhere — the RFdiffusion post-hoc filter in §2 and the dSASA interpretation band in
`03_metric_profiles.md` — are conventions for judging a design, not BindCraft pass/reject rules. And both RMSD filters
are computed **without superposition**, as a direct CA-coordinate difference, so their values are not
comparable to the aligned RMSDs quoted in most papers — which is why the cutoff is 3.5 A rather than
the ~1.5 A an aligned metric would use. `Average_Hotspot_RMSD` is measured on the binder chain
despite its name; it does not measure hotspot residues.

Per-model keys mirror the average for models 1 and 2 (`1_*`, `2_*`), with two exceptions:
`1_ShapeComplementarity` and `2_ShapeComplementarity` are 0.55 rather than 0.60, and `InterfaceAAs`
carries its K/M limits **only** on the average (`1_InterfaceAAs` and `2_InterfaceAAs` are fully null).
Models 3–5 carry only two active filters each — `3_Binder_pLDDT >= 0.80` and
`3_Binder_RMSD <= 3.5` A (likewise `4_` and `5_`) — and are null for everything else.

#### Metrics that are not computed on the PyRosetta-free path

FreeBindCraft replaces PyRosetta, and four filtered quantities are not calculated at all. They are
returned as fixed constants deliberately chosen to sit on the passing side of their own filters
(`functions/pr_alternative_utils.py:577-584`; the image clones `master` unpinned, so line numbers are
as of 2026-09 and may drift — the values are what matter):

| Metric | Constant returned | Its filter | Effect |
|---|---|---|---|
| `interface_dG` | -10.0 | `Average_dG <= 0` | always passes |
| `binder_score` | -1.0 | `Average_Binder_Energy_Score <= 0` | always passes |
| `interface_interface_hbonds` | 5 | `Average_n_InterfaceHbonds >= 3` | always passes |
| `interface_delta_unsat_hbonds` | 1 | `Average_n_InterfaceUnsatHbonds <= 4` | always passes |

`interface_packstat` (0.65), `interface_hbond_percentage` (60.0), `interface_bunsch_percentage` (0.0)
and `interface_dG_SASA_ratio` (0.0) are constants too, but have no active filter. Never present any of
these to a user as a measured property of their design — an H-bond count of 5 means "not measured",
not "five hydrogen bonds".

#### Failure sentinels: values that mean "the calculation failed"

Two genuinely-computed metrics fall back to hard-coded values when their computation fails — three
sentinels in all, every one on the **passing** side of its own filter, so a failed calculation is
silently indistinguishable from a good design. A fourth fallback, on a third metric, runs the other
way (below).

| Metric | Sentinel | Its filter | Why it is silent |
|---|---|---|---|
| `interface_sc` (shape complementarity) | **0.70** | `>= 0.60` (per-model `>= 0.55`) | `sc-rs` binary missing, empty output, 120 s timeout, or any exception |
| `surface_hydrophobicity` | **0.30** | `<= 0.35` | Biopython SASA path raises |
| `surface_hydrophobicity` | **0.00** | `<= 0.35` | FreeSASA hydrophobic-residue selection fails (`except Exception: pass` leaves the variable at its 0.0 initial value) |

Which sentinel you get depends on where the SASA calculation fails. FreeSASA is the default engine
when it imports, so **0.00 is the more likely silent pass**; if the freesasa package is missing
entirely, Biopython becomes the first-level engine and 0.30 becomes the likely one. A 0.00 can also
mean the binder chain was simply absent from the model, on either engine.

The fourth fallback runs the other way. If DSSP fails, secondary-structure assignment returns a
hard-coded `Binder_Loop% = 100.0`, which **fails** `Average_Binder_Loop% <= 90` — so a DSSP failure
silently discards good designs rather than passing bad ones. A run with suspiciously few survivors is
worth checking for DSSP errors before blaming the target.

Treat an exact 0.70 shape complementarity, or a surface hydrophobicity of exactly 0.30 or 0.00, as
suspect rather than as a result.

### GPU Memory Reference

| GPU VRAM | Max System Size (Target + Binder) |
|---|---|
| 16 GB (V100) | ~300 residues |
| 24 GB (RTX 3090) | ~400 residues |
| 40 GB (A100) | ~700 residues |
| 80 GB (H100) | ~950 residues |

---

## 4. RFantibody

### Inputs Required

1. Target antigen PDB (cleaned, cropped)
2. Epitope residue list
3. Antibody framework: VHH or scFv

### CDR Selection

| Goal | CDRs to Design |
|---|---|
| Maximum VHH diversity | H1, H2, H3 |
| CDR-H3 only (highest impact) | H3 |
| scFv full redesign | H1, H2, H3, L1, L2, L3 |

### Pipeline

1. RFdiffusion (antibody fine-tune) — CDR loop generation
2. ProteinMPNN (AbMPNN weights) — sequence design
3. RF2 (antibody-antigen fine-tune) — validation
4. Yeast display library construction + FACS screening

### Hardware

| Task | Min VRAM | Recommended |
|---|---|---|
| RFdiffusion antibody | 8 GB | 24 GB |
| AbMPNN sequence design | 8 GB | 16 GB |
| RF2 antibody validation | 24 GB | 40–80 GB |

---

## 5. BoltzGen

### YAML Configuration Examples

#### Miniprotein Binder (`protein-anything`)

```yaml
protocol: protein-anything
entities:
  - file:
      path: /path/to/target_clean.cif
      include:
        - chain:
            id: A
            res_index: 1..180
binder:
  length: [60, 120]
  hotspots:
    - chain: A
      res_index: 30..60
```

#### Nanobody (`nanobody-anything`)

```yaml
protocol: nanobody-anything
entities:
  - file:
      path: /path/to/target_clean.cif
      include:
        - chain:
            id: A
binder:
  hotspots:
    - chain: A
      res_index: 30..80
```

#### Cyclic Peptide (`peptide-anything`)

```yaml
protocol: peptide-anything
entities:
  - file:
      path: /path/to/target_clean.cif
      include:
        - chain:
            id: A
binder:
  length: [8, 20]
  hotspots:
    - chain: A
      res_index: 30..60
  cyclic: true
```

### Key Parameters

| Parameter | CLI Flag | Recommended |
|---|---|---|
| Number of designs | `--num_designs` | 10,000–60,000 production; 100–500 pilot |
| Budget | `--budget` | 20–100 |
| Diffusion batch size | `--diffusion_batch_size` | Auto |
| Reuse | `--reuse` | Use for interrupted campaigns |

### Known Issues

- **Ubiquitin contamination at 73–76 aa** — avoid this length range or BLAST-check designs
- Residue indexing must start at 1 per chain in CIF

### Hardware

| GPU | Designs/Hour (10k run) |
|---|---|
| RTX 3090 / A5000 (24 GB) | 500–800 |
| A100 40 GB | 1000–1500 |
| H100 SXM 80 GB | 2000–4000 |

---

## 6. PXDesign

### YAML Configuration

```yaml
target:
  file: "./target_clean.cif"
  chains:
    A:
      crop: ["1-150"]
      hotspots: [30, 55, 82]
      msa: "./msa/target/0"
binder_length: [60, 100]
```

### Design Modes

| Mode | Generator | Filter | Notes |
|---|---|---|---|
| Basic | PXDesign-d (diffusion) | AF2-IG only | Faster; most campaigns |
| Extended | PXDesign-d (diffusion) | AF2-IG + Protenix | Higher discriminating power; requires MSA |
| Hallucination | PXDesign-h (AF2 backprop) | AF2-IG | More diverse topologies; slower |

### Key Parameters

| Parameter | Description | Recommended |
|---|---|---|
| `--num_designs` | Number of designs | 5,000–20,000 production; 100–500 pilot |
| `--mode` | Filter mode | `extended` when MSA available |
| `--generator` | Design generator | `diffusion` for throughput |
| `hotspots` (YAML) | Interface residues | 3–6 residues; CIF numbering |

---

## 7. Downstream Tools

### ProteinMPNN (Required for RFdiffusion and PXDesign-d)

Not needed for BindCraft (integrated) or BoltzGen (BoltzIF handles sequence design).

| Model | Best For |
|---|---|
| `v_48_020` | General purpose (default) |
| `v_48_020` + `--use_soluble_model` | Improved solubility |
| `abmpnn` | Antibody CDR design (RFantibody only) |

### LigandMPNN (Small Molecule Workflows)

Use instead of ProteinMPNN when backbone includes a ligand. Vanilla ProteinMPNN is blind to non-protein atoms.

### Boltz-2 (Structure Prediction and Affinity)

- Structure prediction of binder-target complex
- Protein-ligand affinity prediction (log Kd and binary probability)

---

## 8. GPU Selection Reference

### By Tool and Campaign Scale

| Tool | Scale | Recommended GPU | Est. RunPod Cost |
|---|---|---|---|
| RFdiffusion | Pilot (1k) | A100 40 GB | ~$1–2 |
| RFdiffusion | Production (10k) | H100 SXM 80 GB | ~$10–20 |
| BindCraft | Standard (100 final) | A100 40–80 GB | ~$5–15 |
| BindCraft | Difficult (500+ final) | H100 SXM 80 GB | ~$30–80 |
| BoltzGen | Pilot (500) | A100 40 GB | ~$2–5 |
| BoltzGen | Production (10k–60k) | H100 SXM 80 GB | ~$20–100 |
| RFantibody | Standard (5k) | A100 40 GB | ~$5–10 |
| PXDesign | Basic mode (5k) | A100 40 GB | ~$3–8 |
| PXDesign | Extended mode (5k) | H100 SXM 80 GB | ~$8–20 |
