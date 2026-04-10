# Kendrew Experimental Guidance Profiles

> Protocol-level recommendations for what to do after a design run completes.
> Authored by Leo Wan. Agent applies the matching profile based on tool + target class.
> No cost or timeline estimates (per D-21).

---

## Post-Design Validation (All Tools)

For ALL tools, recommend orthogonal refolding validation before committing to synthesis or expression:

- Top 5-10 candidates should be refolded with an independent structure predictor (AF2-multimer or Boltz2)
- Compare predicted structure to designed structure — RMSD < 2A indicates good agreement between design model and independent prediction
- Candidates with good refolding agreement have higher probability of adopting the intended conformation experimentally

---

## BindCraft Binder Designs

### Expression and Purification

- **Expression system**: E. coli BL21(DE3) for minibinders < 100 residues without disulfide bonds; HEK293 or ExpiCHO for larger designs (> 100 residues) or disulfide-containing designs
- **Purification**: Ni-NTA IMAC (if His-tagged) followed by size exclusion chromatography (Superdex 75 Increase 10/300 for minibinders < 15 kDa; Superdex 200 Increase for larger)
- **Quality control**: SDS-PAGE for purity confirmation; SEC-MALS or analytical SEC to confirm monodispersity and rule out oligomerization

### Binding Validation

- **Primary assay**: SPR (Biacore) at 3+ concentrations in single-cycle or multicycle kinetics for on-rate, off-rate, and KD
- **Alternative**: BLI (Octet RED96) if SPR instrument unavailable — similar sensitivity for nanomolar to micromolar interactions
- **Counter-screen**: Test against closest sequence or structural homolog to confirm target specificity
- **Negative control**: Scrambled sequence or irrelevant minibinder at same concentration to confirm binding is sequence-specific

### Yeast Display Library (when applicable)

Recommend yeast display library construction when:
- Design run produced >= 10 candidates passing all quality filters
- Target is a cell-surface receptor, secreted protein, or cytokine
- User intends to optimize affinity beyond what computational predictions can achieve

**Approach**: Pool top 20-50 sequences from the computational shortlist, construct a yeast display library (NNK mutagenesis of key interface positions), FACS sort for binding at multiple antigen concentrations. 3-4 rounds of selection typically yield low-nanomolar to sub-nanomolar binders starting from micromolar computational hits.

---

## RFdiffusion Backbone Designs

### Expression and Purification

- **Expression system**: E. coli BL21(DE3) for scaffolds < 200 residues with no disulfide bonds; HEK293 or Pichia pastoris for disulfide-containing or glycosylated designs
- **Purification**: Standard IMAC (Ni-NTA) followed by size exclusion chromatography
- **Refolding**: If expression yields insoluble inclusion bodies, consider on-column refolding protocol before declaring design failure

### Structural Validation

- Express and purify top 5-10 designs by ipTM and pLDDT
- **Circular dichroism (CD)**: Confirm secondary structure content matches design (alpha-helical, beta-sheet, mixed). CD in the far-UV (190-250 nm) is a fast, sensitive test
- **SEC-MALS or analytical SEC**: Confirm monodispersity and correct molecular weight. Oligomerization is a common failure mode for scaffold designs
- **If binder**: Proceed to SPR/BLI as described under BindCraft binding validation

---

## RFantibody VHH / Nanobody Designs

### Expression and Purification

- **Expression system**: E. coli periplasm (SHuffle T7 Express for disulfide bonds) for initial screening; Pichia pastoris for scale-up and glycosylated variants
- **Purification**: Protein A/G affinity resin (if Fc-tagged) or Ni-NTA + size exclusion chromatography
- **Quality control**: SDS-PAGE under reducing and non-reducing conditions to confirm disulfide bond formation; SEC for monodispersity

### Binding Validation

- **Primary assay**: SPR or BLI at 3+ concentrations — nanobodies often have fast on-rates and slow off-rates; multi-cycle kinetics is preferred
- **ELISA**: Rapid preliminary screen for binding-positive clones when testing many candidates before investing in kinetics
- **Thermostability**: nanoDSF (Tycho) or DSF to measure apparent melting temperature (Tm). Well-folded VHHs typically show Tm > 60°C. Low Tm candidates should be deprioritized or subjected to stability engineering
- **Epitope binning**: If multiple lead VHHs confirmed, BLI-based competition assay to identify non-competing pairs for bispecific or sandwich assay applications

---

## BoltzGen Designs

BoltzGen supports both cyclic peptides and protein binders. Apply the appropriate sub-profile.

### Cyclic Peptide Designs

- **Synthesis**: Standard solid-phase peptide synthesis (SPPS) on Rink amide or Wang resin; head-to-tail cyclization via solution-phase macrolactamization after resin cleavage
- **Analytical confirmation**: HPLC purity > 95%; MALDI-TOF or LC-MS for mass confirmation; 1H NMR for structure confirmation if > 10 residues
- **Binding validation**: SPR or ITC (isothermal titration calorimetry) for thermodynamic characterization; ITC provides both KD and enthalpy/entropy decomposition

### Protein Binder Designs (BoltzGen)

Follow expression, purification, and validation guidance from the BindCraft section above.

---

*LEO: Customize these protocols based on Ranomics internal SOPs and client preferences. Add target-class-specific guidance as needed (e.g., membrane protein targets requiring detergent or nanodisc reconstitution, enzyme inhibitor designs requiring activity assay rather than direct binding readout, cytokine receptor designs requiring cell-based signaling assays for functional validation). Note which steps Ranomics can perform in-house versus which require external CROs or collaborators.*
