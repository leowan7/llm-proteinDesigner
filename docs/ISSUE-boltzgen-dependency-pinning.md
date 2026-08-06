# BoltzGen: the image cannot currently be edited at all

**Status:** open. A pin attempt on 2026-08-05 (`9605570`) broke
`ranomics-boltzgen-prod`; reverted in `e8adf95` and verified restored.

## The problem in one line

`docker/boltzgen/Dockerfile.modal` pins essentially nothing, and Modal
caches images by Dockerfile content hash — so **any** edit to that file
discards a months-old cached image and re-resolves every floating
dependency against today's PyPI, producing a set that no longer agrees
with itself.

## What actually happened

Changing `pip install boltzgen` to `pip install "boltzgen==0.3.2"` forced a
rebuild. The rebuilt image deployed cleanly (Layer 1 build validation
passed — it only checks `import boltzgen`, which succeeds) and then failed
at the first GPU forward pass:

```
File ".../boltzgen/model/layers/triangular.py", line 32, in _kernel_triangular_mult
File ".../cuequivariance_ops_torch/__init__.py", line 91, in _raise_triton_import_error
Exception: Failed to import Triton-based component: triangle_multiplicative_update:
  ImportError: cannot import name 'is_fx_symbolic_tracing'
               from 'torch.fx._symbolic_trace'
Please make sure to install triton==3.3.0. Other versions may not work!
```

Smoke: `status FAILED`, bucket `tool-invocation`, 119 gpu_seconds.

Note the pin is incidental to the mechanism. A comment or whitespace change
would have had the same effect. The image was a time bomb whose fuse was
"next time anyone touches this file."

## Why this image and not the others

| Image | Rebuild outcome | Why |
|---|---|---|
| rfdiffusion | clean | pins `torch==2.2.2`, `torchvision==0.17.2`, `torchdata==0.7.1`, `colabfold==1.5.5`, `jax==0.4.23`, `nvidia-cudnn-cu11<9.0` |
| rfantibody | clean | thin image, `uv sync` resolves from upstream's own lockfile |
| pxdesign | clean | pins `PXDESIGN_SHA`, `COLABDESIGN_SHA`, `torch==2.3.1`, `jax==0.4.29`, `PXDesignBench@v0.1.2`, CUTLASS `v3.5.1` |
| bindcraft | clean | conda env spec from upstream |
| **boltzgen** | **broke** | five `pip install` lines, all floating |

The floating installs:

- `:41` `torch` (cu121 index, no version)
- `:45` `boltzgen` (no version)
- `:54` `torch` again, `--force-reinstall`, no version
- `:67` `cuequivariance-ops-cu12`, `cuequivariance-ops-torch-cu12`, neither versioned
- `:84` `gemmi`, `requests`, `pyyaml` unversioned
- triton: never requested explicitly, arrives transitively

The Dockerfile already carries comments fighting this exact class of
problem ("boltzgen's transitive deps can upgrade torch to a version
compiled against CUDA 12.8+…", "force-reinstalling torch AFTER the kernel
install invalidates the compiled bindings"). Those comments are the scar
tissue of previous rounds of the same fight.

## What a real fix looks like

Pin the whole stack together, not one package:

1. Determine the currently-working versions by introspecting the running
   image (the approach used to confirm the rfdiffusion pin — a throwaway
   `modal run` on the same Dockerfile that prints
   `importlib.metadata.version(...)` for boltzgen, torch, triton, and both
   cuequivariance wheels). **Do this before editing the file**, because the
   moment it is edited the evidence is gone.
2. Pin all of them, plus `triton==3.3.0` as the error message asks.
3. Rebuild, smoke, iterate. Expect several rounds — the constraint is a
   mutually-compatible (torch, triton, cuequivariance, boltzgen) tuple and
   the wheels are ABI-coupled.
4. Add a Layer 1 build check that actually exercises the failing path.
   The current check is `python3 -c "import boltzgen"`, which passed on the
   broken image. Something that imports `cuequivariance_ops_torch` would
   have failed the build instead of a GPU run.

Step 4 is worth doing regardless of the pinning work — it converts this
failure mode from "deploys fine, dies on GPU" to "never deploys".

## Consequence right now

`docker/boltzgen/Dockerfile.modal` must not be edited until this is done.
That is a real constraint on anything needing an image change there — new
system deps, new baked weights, Python version moves.

It does **not** block `docker/boltzgen/run_pipeline.py`. That file is
mounted via `add_local_file` in `infrastructure/modal/boltzgen_app.py`, not
baked into the image, so it does not participate in the content hash. The
multi-chain change (`bcce405`) shipped this way and its single-chain smoke
passes on the restored image.
