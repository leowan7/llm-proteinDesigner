# Kendrew Metric Interpretation Profiles

> These profiles define how Kendrew interprets design scores. Authored by Leo Wan.
> Each tool produces different metrics. Never compare scores across tools (per D-05).

## BindCraft Metrics

> These are **interpretation bands for ranking designs that already passed BindCraft's filters**,
> not the filters themselves. The pass/reject cutoffs live in `02_technical_setup_guide.md` under
> *Default Filter Thresholds*. For most metrics the filter has already removed everything a red flag
> would catch, so the Passable band stops exactly at the filter, the Red Flag cell reads `—`, and the
> Interpretation column names the filter. Two exceptions keep a real red flag: dSASA, whose filter is
> only an interface-exists check, and the clash counts, which are not filtered at all. A row that
> reads `—` across every band is not computed at all. Never quote a band as if it were the filter,
> and never quote an uncalibrated split as if it were established.

| Metric | Range | Strong (green) | Passable | Red Flag | Interpretation |
|--------|-------|----------------|----------|----------|---------------|
| ipTM | 0-1 | > 0.7 | 0.5-0.7 | — | Predicts binding likelihood, NOT affinity. Filter is `Average_i_pTM >= 0.50`, so a red flag below that is *unreachable*. High ipTM = well-formed interface. Does not predict Kd — that requires SPR/BLI/ITC. |
| i_pAE | 0-1 | < 0.25 | 0.25-0.35 | — | Lower = better positional certainty at interface. Complement to ipTM. Filter is `Average_i_pAE <= 0.35`, so bands above 0.35 are *unreachable*. The 0.25 split is an **uncalibrated placeholder**. |
| pLDDT | 0-1 | > 0.85 | 0.80-0.85 | — | AF2 backbone confidence. Correlates with foldability in expression. Filter is `Average_pLDDT >= 0.80` (and `Average_Binder_pLDDT >= 0.80`), so every delivered design already clears 0.80 and anything lower is *unreachable*. The 0.85 split is an **uncalibrated placeholder**. |
| dG | — | — | — | — | **Not computed.** FreeBindCraft has no PyRosetta, so `interface_dG` is the fixed constant -10.0 for every design. Never report it, rank on it, or invite a user to look for a value. |
| dSASA | Angstrom^2 | > 800 | 400-800 | < 400 | Buried surface area at interface (binder + target sides summed). Larger interface = more extensive contact. These are interpretation bands only — the filter is merely `>= 1 A^2`, an interface-exists check. |
| ShapeComplementarity | 0-1 | > 0.70 | 0.60-0.70 | — | Geometric fit between binder and target surfaces. Filter is `Average_ShapeComplementarity >= 0.60`, so nothing below 0.60 reaches you. Poor packing with high ipTM still suggests a false positive. Exactly 0.70 is a failure sentinel, not a measurement. |
| Unrelaxed_Clashes | count | 0 | 1-5 | > 5 | Steric clashes before OpenMM relaxation. Genuinely computed, but **not filtered** — no clash threshold is active. Moderate counts acceptable; relaxation should resolve most. |
| Relaxed_Clashes | count | 0 | 1-2 | > 2 | Clashes after OpenMM relaxation. Genuinely computed, but **not filtered**. Nonzero = structural problem that survived energy minimization. |
| Surface_Hydrophobicity | 0-1 | < 0.25 | 0.25-0.35 | — | Fraction of binder-monomer SASA from hydrophobic residues. High = aggregation risk in aqueous solution. Filter is `Average_Surface_Hydrophobicity <= 0.35`, so bands above 0.35 are *unreachable*. The 0.25 split is an **uncalibrated placeholder**. Exactly 0.30 or 0.00 is a failure sentinel, not a measurement. |
| n_InterfaceResidues | count | > 10 | 7-10 | — | Number of binder residues contributing to interface contacts. Filter is `Average_n_InterfaceResidues >= 7`, so a red flag below 7 is *unreachable*. Too few contacts = weak, non-specific binding surface. |

### Red Flag Combinations (BindCraft)

1. **High ipTM + borderline ShapeComplementarity**: ipTM > 0.7 and ShapeComplementarity in the 0.60-0.70 Passable band. AF2 confidence is high but geometric packing only just clears the filter — likely false positive. These designs often fail in experimental validation. Check for an exact 0.70 first — that is a failure sentinel, not a measurement. Nothing below 0.60 reaches you; the filter already rejects it.

2. **High ipTM + Surface_Hydrophobicity near the filter ceiling**: ipTM > 0.7 and Surface_Hydrophobicity in 0.30-0.35. The interface looks well-formed but the binder is aggregation-prone in solution; expression yield will likely be poor. Check for an exact 0.30 first — that is a failure sentinel, not a measurement. (dG cannot appear in any rule here; it is a fixed constant.)

3. **Any Relaxed_Clashes > 0**: Structural clash survives OpenMM energy minimization. Indicates a real backbone/sidechain conflict. Deprioritize these candidates.

4. **pLDDT near the filter floor**: `Average_pLDDT` in 0.80-0.85. Backbone confidence is only marginally above the reject line, so foldability in a cellular or cell-free expression system is uncertain. Treat this as a prompt to check rather than a verdict — the split is uncalibrated, and anything below 0.80 was already rejected.

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

*LEO: Replace threshold values above with Ranomics/Kendrew-calibrated thresholds based on internal benchmarking data. The literature values are starting points only. Particularly: dSASA and n_InterfaceResidues distributions depend heavily on target size and binding site properties — single-pass transmembrane targets will have different distributions than soluble cytokines. The splits marked **uncalibrated placeholder** above (pLDDT 0.85, i_pAE 0.25, Surface_Hydrophobicity 0.25) are the first ones to replace: every delivered design sits inside the filter's narrow band, so these decide the whole ranking. dG cannot be calibrated at all — its `Average_dG <= 0` threshold is vacuous, always passed by the fixed `interface_dG` constant.*
