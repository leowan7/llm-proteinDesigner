# RFantibody: multi-chain antigens are supported upstream and blocked by us

**Status:** open, not started. Deliberately scoped out of the 2026-08-05
multi-chain pass (PXDesign, RFdiffusion, BoltzGen) because the HLT chain
merge needs a renumbering scheme, and a rushed version produces a
silently-wrong multi-chain path — worse than none.

**Read first:** [MULTI-CHAIN-TARGETS.md](MULTI-CHAIN-TARGETS.md) for the
job_spec contract the other three tools now implement.

## The claim in our code is false

`docker/rfantibody/run_pipeline.py:248` says, as justification for a
single-chain filter:

> 1. Multi-chain targets where only one chain is the antigen. RFantibody
>    expects a single-chain target; extra chains can confuse residue
>    indexing and blow up frame construction.

Upstream RFantibody's README says the opposite, describing the HLT format:

> The Target chain(s) are denoted as chain id 'T' (even if there are
> multiple target chains).

Multi-chain antigens are supported. Ours are discarded before the model
sees them, and the comment above is why nobody rechecked. This is the same
pattern found in PXDesign, RFdiffusion, BoltzGen and Proteina: the
wrapper's input surface was read as the engine's capability.

## Where the single-chain assumption lives

Two independent filters, one layered on the other:

1. `pipeline_normalize.normalize_for_rfantibody(..., target_chain=chain)`
   — as of 2026-08-05 this already accepts `"A,B"`, so it is **not** a
   blocker. The preset's docstring says so explicitly.
2. `docker/rfantibody/run_pipeline.py::preprocess_target_pdb` — a second,
   tool-local filter at `:308` and `:358` (`if chain != target_chain`). This
   is the actual blocker and it is where the work is.

Hotspots are built at `:1391`:

```python
hotspots_str = ",".join(f"{chain}{res}" for res in hotspot_residues)
```

Force-prefixed with the single target chain, so a `"B264"` token becomes
`"AB264"` — identical to the RFdiffusion bug fixed in `ee5102e`.

## Why this one is harder than the other three

PXDesign and BoltzGen keep target chains as distinct chains in their
configs, so "make the list longer" was most of the fix. RFdiffusion keeps
them as distinct contig segments. **RFantibody does not**: HLT collapses
every antigen chain into the single chain id `T`.

That creates a residue-number collision the other tools do not have. An
IgG1 Fc homodimer is two chains each numbered 1..223; merged into one
chain T, residue 100 is ambiguous. So a correct fix needs:

1. A deliberate renumbering scheme for the merge (offset per source chain,
   or sequential 1..N across the concatenation) — and it must be recorded,
   because
2. hotspots must be remapped through exactly that scheme. `"B264"` has to
   become whatever residue 264-of-chain-B is called inside chain T.
3. The reverse map, so residues in returned designs can be attributed back
   to the protomer they actually contacted. Without this the developability
   / interface analysis downstream cannot tell which protomer a design
   grips — which is the entire point of the Fc use case.

Step 2 is the trap. It is the same double-mapping hazard called out for
PXDesign in `MULTI-CHAIN-TARGETS.md`: convert hotspots twice and the design
aims at the wrong residues while every downstream check still passes.

## Suggested shape

Mirror the pass already landed for the other three:

1. `preprocess_target_pdb` keeps N chains; return the chain→offset map it
   used for the merge as part of its stats dict.
2. Build hotspots via `pipeline_normalize.parse_hotspots`, then map each
   `(chain, resnum)` through that offset map to a `T<n>` token. An unmapped
   hotspot is a hard error, never a skip.
3. Assert every requested chain survived into the written HLT file.
4. Dry-run harness diffing single-chain output against the pre-change
   module loaded from git, byte-for-byte — the pattern used in
   `scratchpad/dryrun_{pxdesign,rfdiffusion,boltzgen}.py`. Single-chain
   must be byte-identical before any multi-chain claim is made.
5. Smoke tier before and after.

## Also note

- `tools/rfantibody/__init__.py` in tools-hub coerces hotspots with
  `int()`, so chain-prefixed tokens are rejected at the form layer. Same
  bug fixed for bindcraft/pxdesign/rfdiffusion in tools-hub `355c95e`; the
  shared helpers `parse_target_chains` / `parse_hotspot_residues` in
  `tools/base.py` are ready to use.
- `docker/rfantibody/Dockerfile.modal` was pinned to
  `8fe311415754e0276d1a39c87c57e69c88927a2d` on 2026-08-05 (`9605570`), so
  the upstream this is written against is now fixed and known.
- That upstream HEAD adds "JSON and TCR-MHC support" — TCR-MHC is
  inherently a multi-chain target, which is further reason to expect the
  multi-chain path to be well-supported upstream.
