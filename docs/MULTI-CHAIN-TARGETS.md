# Multi-chain targets — the job_spec contract

Landed 2026-08-05. Applies to **PXDesign**, **RFdiffusion** and **BoltzGen**
(BoltzGen's wrapper landed in the same pass and was GPU-verified the same day —
4ZQK, `{A:115, B:106, C:55}`, 430 s — but this file said otherwise until
2026-08-07); BindCraft already worked and its form layer was unblocked
alongside them.

## Why this exists

The wrappers were written for single-chain targets and silently discarded
multi-chain capability the upstream models have. The driving case is an IgG1
Fc homodimer (two identical 223-aa chains A and B): measured across 1,875
designs, binders grip both protomers near-symmetrically — 697 Å² on A, 706 Å²
on B, with 85% putting ≥30% of the interface on the second chain. Designing
against one chain aims at half the epitope, and still returns plausible
designs.

**Read those numbers for what they are.** They are an analysis of prior
**single-chain** runs — binders designed against chain A alone, then measured
against the assembled dimer. That is what makes them an argument FOR
multi-chain support rather than a result OF it. As of 2026-08-07 no
multi-chain run against the Fc dimer has been done.

**Never read a wrapper's input surface as a measure of the engine's
capability.** Answering "can this tool take two chains?" needs three hops —
the model's docs, the pipeline in llm-proteinDesigner, the validator in
tools-hub — and nowhere was the real capability written down. That is what
this file is for.

## The contract

```jsonc
{
  "target_chain":     "A,B",              // or "A B"; "A" behaves exactly as before
  "hotspot_residues": ["A296", "B264"],   // chain-prefixed; bare ints still accepted
  "parameters": { /* unchanged per tool */ }
}
```

- `target_chain` — one chain (`"A"`) or several. **Both separators are
  accepted**: `"A,B"` and `"A B"` are equivalent, as is `"A, B"`. The comma
  form is this repo's contract; whitespace is what tools-hub shipped first
  (`shared/pdb_inspect.validate_target_chain` splits on it, five tools carry
  `multi_chain_supported=True`, and the form copy tells users "List chains
  separated by spaces"). Parsing only one of them left multi-chain accepted
  by the form and then rejected by every gate behind it. Case-sensitive.
  **Order is significant**: it drives contig segment order and AF2 FASTA
  concatenation order. De-duplicated.
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

A chain is emitted as **one segment per contiguous run**, joined by `/` with
no `0` inside the chain. RFdiffusion expands each segment into every integer
in the range and asserts each one exists in the input PDB, so a dense
`{chain}{min}-{max}` span aborts the run the moment the chain has a gap:

```
contigmap.contigs=[A18-132/0 B33-84/B93-146/0 55-65]   # 4ZQK: B is missing 85-92
```

This is not exotic. Crystal structures of oligomers almost always carry a
disordered loop, and the normalizer opens gaps of its own whenever it drops a
residue with an incomplete backbone. The single-chain path had the same
defect — it went unnoticed only because the one fixture ever used, PD-L1
chain A, happens to be gap-free.

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
| RFdiffusion hotspots | every token force-prefixed with the single target chain, so `"B264"` became `"AB264"` | chain-qualified tokens pass through, and a hotspot absent from the cleaned structure is a hard `ValueError` — upstream drops unmatched tokens silently, yielding an all-zero hotspot tensor |
| RFdiffusion contigs | one dense `{chain}{min}-{max}` span per chain; any numbering gap aborted with `('B', 85) is not in pdb file!` | one segment per contiguous run |
| RFdiffusion `binder_length` | `infer_binder_chain` called `.get()` on it, but the agent wizard supplies a bare `int` — `AttributeError` at Stage 2, after RFdiffusion had already been paid for | both shapes resolve through one helper shared with the contig builder |
| RFdiffusion binder chain | `"B" if target_chain == "A" else "A"` returned a *target* chain for a 2-chain target; ProteinMPNN redesigned the target | inferred from output + length cross-check, raises on ambiguity |
| RFdiffusion AF2 binder | picked positionally; a wrong pick scores a target chain as the binder | identified by sequence identity, raises if unresolvable |
| RFdiffusion `i_pAE` | boundary `chain_lengths[0]` scored protomer B as binder-side — and since ColabFold's scores JSON carries no `chain_lengths` at all, the real production boundary was a `total_res // 2` guess, wrong on the single-chain path too | boundary is the residue count of the target sequences this pipeline wrote into the AF2 FASTA; the guess remains only as a last resort and now logs a warning when it fires |

## Known limitations

- **`ipTM` is still the complex-wide value, not the binder-target pair.**
  This was harmless while every target was single-chain, because the two
  coincide for a 2-chain complex. On a multi-chain target they do not: the
  target-target interface of a real crystal dimer scores ~0.9 and, since
  `ipTM` is a max over residues rather than a mean, dominates almost
  independently of binder quality. It is both the ranking key and the
  `IPTM_THRESHOLD` gate, so a mediocre binder can rank first with a
  plausible-looking number. **Fixed for BoltzGen** (2026-08-07): `design_iptm`
  is now first in `IPTM_KEYS`, so the value carried into ranking and the
  `IPTM_THRESHOLD` label is the binder-to-target pair. RFdiffusion and
  PXDesign still need a per-pair value derived from the chain layout —
  **not fixed there, treat their multi-chain `ipTM` as unreliable.** tools-hub
  marks it as such in the results UI rather than letting the number stand.
- **PXDesign's binder is single-chain** upstream. Only the target is
  multi-chain.
- **RFantibody is NOT multi-chain**, and this is an upstream limit rather
  than a wrapper gap: it builds a VHH against one chain
  (`multi_chain_supported=False` in tools-hub `shared/pdb_preflight_rules.py`).
  Its wrapper also has the same class of bug —
  `docker/rfantibody/run_pipeline.py:1391` prepends the target chain blindly,
  so a chain-qualified token `"A25"` becomes `--hotspots AA25`. Flagged, not
  fixed; the tools-hub form deliberately does not offer the prefixed form.
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
`test_pxdesign_multichain.py`, `test_rfdiffusion_multichain.py`,
`test_boltzgen_multichain.py`, `test_boltzgen_metrics_csv.py`, and tools-hub
`tests/test_multichain_targets.py`, `tests/test_hotspot_chain_prefix_gates.py`.

## Upstream references

- PXDesign — <https://github.com/bytedance/PXDesign> (`target.chains` map,
  `"all"` shorthand, "currently only a single binder chain is supported")
- RFdiffusion — <https://github.com/RosettaCommons/RFdiffusion> (`/0 ` chain
  break, "NOTE, the space is important here"; `ppi.hotspot_res=[A30,A33,A34]`)
