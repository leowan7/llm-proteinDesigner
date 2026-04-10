# Kendrew Metric Interpretation Profiles

> These profiles define how Kendrew interprets design scores. Authored by Leo Wan.
> Each tool produces different metrics. Never compare scores across tools (per D-05).

## BindCraft Metrics

| Metric | Range | Strong (green) | Passable | Red Flag | Interpretation |
|--------|-------|----------------|----------|----------|---------------|
| ipTM | 0-1 | > 0.7 | 0.5-0.7 | < 0.45 | Predicts binding likelihood, NOT affinity. High ipTM = well-formed interface. Does not predict Kd — that requires SPR/BLI/ITC. |
| i_pAE | 0-1 | < 0.4 | 0.4-0.6 | > 0.6 | Lower = better positional certainty at interface. Complement to ipTM. |
| pLDDT | 0-1 | > 0.8 | 0.7-0.8 | < 0.7 | AF2 backbone confidence. Correlates with foldability in expression. |
| dG | kcal/mol | < -30 | -30 to -10 | > -10 | Rosetta binding energy. More negative = more favorable interaction. Not directly predictive of experimental Kd. |
| dSASA | Angstrom^2 | > 800 | 400-800 | < 400 | Buried surface area at interface. Larger interface = more extensive contact. |
| ShapeComplementarity | 0-1 | > 0.65 | 0.5-0.65 | < 0.5 | Geometric fit between binder and target surfaces. < 0.5 indicates poor packing and likely false positive when combined with high ipTM. |
| Unrelaxed_Clashes | count | 0 | 1-5 | > 5 | Steric clashes before Rosetta relaxation. Moderate counts acceptable — relaxation should resolve most. |
| Relaxed_Clashes | count | 0 | 1-2 | > 2 | Clashes after Rosetta relaxation. Nonzero = structural problem that survived energy minimization. |
| Surface_Hydrophobicity | fraction | < 0.4 | 0.4-0.6 | > 0.6 | Fraction of surface exposed hydrophobic residues. High = aggregation risk in aqueous solution. |
| n_InterfaceResidues | count | > 10 | 6-10 | < 6 | Number of binder residues contributing to interface contacts. Too few contacts = weak, non-specific binding surface. |

### Red Flag Combinations (BindCraft)

1. **High ipTM + low ShapeComplementarity**: ipTM > 0.7 and ShapeComplementarity < 0.5. AF2 confidence is high but geometric packing is poor — likely false positive. These designs often fail in experimental validation.

2. **Low dG + high Surface_Hydrophobicity**: dG < -30 and Surface_Hydrophobicity > 0.6. Energetically favorable by Rosetta but aggregation-prone in solution. Expression yield will likely be poor.

3. **Any Relaxed_Clashes > 0**: Structural clash survives Rosetta energy minimization. Indicates a real backbone/sidechain conflict. Deprioritize these candidates.

4. **pLDDT < 0.7**: Low backbone confidence. Foldability in a cellular or cell-free expression system is uncertain.

---

## RFdiffusion / RFantibody Metrics

RFdiffusion produces backbone-only designs; ProteinMPNN assigns sequences. AF2-multimer then validates.
RFantibody focuses on CDR loop design for VHH/nanobody scaffolds.

| Metric | Range | Strong | Passable | Red Flag | Notes |
|--------|-------|--------|----------|----------|-------|
| pLDDT | 0-100 | > 80 | 70-80 | < 70 | AF2 backbone confidence (0-100 scale for RFdiffusion outputs). |
| pAE | Angstrom | < 5 | 5-10 | > 10 | Predicted aligned error. Lower = more confident domain orientation. |
| ipTM | 0-1 | > 0.7 | 0.5-0.7 | < 0.45 | Interface confidence for binder designs validated with AF2-multimer. |

---

## BoltzGen / PXDesign Metrics

| Metric | Range | Strong | Passable | Red Flag | Notes |
|--------|-------|--------|----------|----------|-------|
| confidence | 0-1 | > 0.8 | 0.6-0.8 | < 0.6 | BoltzGen overall structure confidence. Analogous to pLDDT. |
| ptm | 0-1 | > 0.7 | 0.5-0.7 | < 0.5 | Template modeling score for structural accuracy. |
| iptm | 0-1 | > 0.7 | 0.5-0.7 | < 0.45 | Interface template modeling — binder:target interface quality. |

---

*LEO: Replace threshold values above with Ranomics/Kendrew-calibrated thresholds based on internal benchmarking data. The literature values are starting points only. Particularly: dG thresholds are heavily dependent on target size and binding site properties — single-pass transmembrane targets will have different distributions than soluble cytokines.*
