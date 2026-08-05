# Multi-chain targets — the job_spec contract

Landed 2026-08-05. Applies to **PXDesign** and **RFdiffusion**; BindCraft
already worked and its form layer was unblocked in the same pass.

## Why this exists

The wrappers were written for single-chain targets and silently discarded
multi-chain capability the upstream models have. The driving case is an IgG1
Fc homodimer (two identical 223-aa chains A and B): measured across 1,875
designs, binders grip both protomers near-symmetrically — 697 Å² on A, 706 Å²
on B, with 85% putting ≥30% of the interface on the second chain. Designing
against one chain aims at half the epitope, and still returns plausible
designs.

**Never read a wrapper's input surface as a measure of the engine's
capability.** Answering "can this tool take two chains?" needs three hops —
the model's docs, the pipeline in llm-proteinDesigner, the validator in
tools-hub — and nowhere was the real capability written down. That is what
this file is for.

## The contract

```jsonc
{
  "target_chain":     "A,B",              // comma string; "A" behaves exactly as before
  "hotspot_residues": ["A296", "B264"],   // chain-prefixed; bare ints still accepted
  "parameters": { /* unchanged per tool */ }
}
```

- `target_chain` — one chain (`"A"`) or several (`"A,B"`). Case-sensitive.
  **Order is significant**: it drives contig segment order and AF2 FASTA
  concatenation order. De-duplicated, whitespace tolerated (`" A , B "`).
- `hotspot_residues` — chain-prefixed tokens (`"A296"`) address a specific
  protomer. **Bare integers (`296`) are still accepted and attributed to the
  FIRST target chain**, which is exactly the historical single-chain
  behaviour. The two forms may be mixed.
- Residue numbers are always in **original author numbering** from the
  uploaded structure. Do not pre-convert them; PXDesign renumbers internally
  and double-mapping silently aims at the wrong residues.

Backward compatibility is absolute: `target_chain: "A"` with bare-int
hotspots produces byte-identical downstream artifacts (verified by diff, not
inspection — see "Verification" below).

## What each tool does with it

### PXDesign

`target.chains` is a per-chain **map** upstream; only the *binder* is
single-chain there. Each target chain gets its own `crop` (from that chain's
real length in the cleaned CIF) and its own `hotspots` list, renumbered 1..N
per chain:

```yaml
target:
  file: /work/target.cif
  chains:
    A: {crop: ["1-223"], hotspots: [96]}
    B: {crop: ["1-223"], hotspots: [64]}
binder_length: 80
preset: preview
N_sample: 8
```

### RFdiffusion

Multi-chain fixed targets use a `/0 ` chain break. **The space after `/0` is
required** — it is what makes the model treat the segments as separate
chains rather than one continuous polymer.

```
contigmap.contigs=[A1-223/0 B1-223/0 50-70]
ppi.hotspot_res=[A296,B264]
```

The binder chain is **inferred from the RFdiffusion output** (the one chain
that is not a target) and cross-checked against the requested binder length
range, rather than assumed to be the next letter. The AF2 validation complex
is written target chains first in caller order, binder last:
`targetA:targetB:binder`.

## Failure modes this closes

Each of these previously produced output that looked like a successful run.

| Where | Old behaviour | Now |
|---|---|---|
| `normalize_for_pipeline` | filtered to one chain before anything ran | keeps every named chain; a named chain that does not survive is a hard error |
| PXDesign `ensure_cif` | same one-chain filter, so a correct YAML pointed at a CIF missing chain B | asserts every requested chain is in the written CIF |
| PXDesign hotspots | unmapped hotspot logged and skipped → `hotspots: []`, an untargeted design | hard `ValueError` |
| RFdiffusion hotspots | every token force-prefixed with the single target chain, so `"B264"` became `"AB264"` | chain-qualified tokens pass through |
| RFdiffusion binder chain | `"B" if target_chain == "A" else "A"` returned a *target* chain for a 2-chain target; ProteinMPNN redesigned the target | inferred from output + length cross-check, raises on ambiguity |
| RFdiffusion AF2 binder | picked positionally; a wrong pick scores a target chain as the binder | identified by sequence identity, raises if unresolvable |
| RFdiffusion `i_pAE` | boundary `chain_lengths[0]` scored protomer B as binder-side | boundary sums the target chains |

## Known limitations

- **Three or more chains are not reachable from the web form.** The
  pre-existing `len(target_chain) > 4` cap in the tools-hub adapters admits
  `"A,B"` but not `"A,B,C"`. The pipelines themselves handle N chains;
  raising the cap is a deliberate, separate decision. Direct Modal callers
  are unaffected.
- **PXDesign's binder is single-chain** upstream. Only the target is
  multi-chain.
- **BoltzGen and RFantibody are NOT multi-chain.** `pipeline_normalize`
  accepts a multi-chain selector for all four presets, but those two
  pipelines still pass a single chain id and their downstream handling has
  not been audited. RFantibody in particular has the same class of bug in
  its own wrapper — flagged, not fixed.
- **Proteina** was owned by a concurrent session and is not covered here.

## Verification (no GPU)

Both dry-run harnesses diff the current module against the pre-change copy
loaded from git, rather than eyeballing output:

- PXDesign single chain: YAML, CIF (6673 bytes) and renumber_map all
  byte-identical. Two chains: both present in the YAML map *and* in the
  written CIF.
- RFdiffusion single chain: every resolved Hydra arg identical —
  `[A18-47/0 50-70]`, `[A30,A33,A34]`, binder chain `B`, `i_pAE` identical.
  Two chains: `[A1-30/0 B1-30/0 50-70]`, `[A5,A9,B5,B9]`, binder chain `C`.

Test suites: `backend/tests/pdb/test_pipeline_normalize.py` (multi-chain
cases alongside the original 22, all unchanged),
`test_pxdesign_multichain.py`, `test_rfdiffusion_multichain.py`, and
tools-hub `tests/test_multichain_targets.py`.

## Upstream references

- PXDesign — <https://github.com/bytedance/PXDesign> (`target.chains` map,
  `"all"` shorthand, "currently only a single binder chain is supported")
- RFdiffusion — <https://github.com/RosettaCommons/RFdiffusion> (`/0 ` chain
  break, "NOTE, the space is important here"; `ppi.hotspot_res=[A30,A33,A34]`)
