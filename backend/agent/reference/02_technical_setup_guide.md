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

| Filter | Threshold | Backend |
|---|---|---|
| pLDDT | >= 80 | AF2 |
| i_pTM | >= 0.70 | AF2 |
| i_pAE | <= 10 | AF2 |
| binder_RMSD | <= 1.5 A | Biopython |
| Clash_score | <= 20 | OpenMM / FreeSASA |
| shape_complementarity | >= 0.60 | sc-rs (MIT) |
| SAP_score | <= 5.0 | Biopython |
| Interface_buried_sasa | >= 800 A^2 | FreeSASA |
| H-bond network | Not computed | Placeholder — expected behavior |

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

> **These show upstream BoltzGen spec syntax, not what this platform sends.** The platform builds its spec in `backend/pipelines/boltzgen.py::generate_config` (mirrored by `docker/boltzgen/run_pipeline.py::build_yaml_spec`) and emits a different concrete form: a `file:` entity plus a `protein:` entity with `sequence: "<min>..<max>"`, with hotspots as per-chain `binding_types:` — there is no `binder:` block anywhere in the code. Do not quote these examples to a user as the job that will run.

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

#### Cyclic Peptide (`peptide-anything`) — OFF-PLATFORM ONLY

> **This platform cannot run this.** The `peptide-anything` protocol is reachable, but nothing in the pipeline emits a cyclisation constraint, so a job launched here returns a **linear** peptide. The `cyclic: true` key below is also not the form measured to work: head-to-tail closure was achieved off-platform with a hand-written spec carrying a `constraints: bond:` block through a bespoke Modal app, and even then only 2 of 16 designs closed (N-to-C < 1.6 A) in the one pilot run. Route cyclic requests to a bespoke run and do not promise a yield.

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
