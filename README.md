# llm-proteinDesigner

Modal-deployed GPU pipelines for AI protein design. Hosts the heavyweight
compute side of the Ranomics CRO platform: rfdiffusion, bindcraft, boltzgen,
pxdesign, and rfantibody.

## Relationship to tools-hub

This repo is one half of a two-repo system:

- **tools-hub** (sibling repo) is the web, wallet, auth, and job orchestration
  layer. It runs on Railway (Flask + Supabase), accepts user submissions, and
  dispatches GPU work to Modal.
- **llm-proteinDesigner** (this repo) hosts the per-tool Modal apps. Each app
  exposes a single `run_tool` entrypoint that pulls inputs from the
  tools-hub-issued presigned URLs, runs the GPU pipeline inside the tool's
  container, and posts results back via the tools-hub webhook surface.

The two repos talk to each other across a stable RPC contract; see
`contracts/` below.

## The contracts/ vendor

`contracts/rpc.py` and `contracts/upload_urls.py` are vendored
byte-identical from `tools-hub/contracts/`. Any change must be landed
in both repos in lock-step, and `contracts/CONTRACTS_SHA256.lock` must
be refreshed in both. The drift guard CI job
(`.github/workflows/contracts-drift.yml`) blocks merges if the hashes
ever diverge.

`contracts/__init__.py` carries a per-repo sync-source comment and is
intentionally excluded from the lockfile.

## Layout

```
infrastructure/modal/  Modal app entrypoints (one per tool); see its README
docker/                Per-tool Dockerfile + run_pipeline.py (image-side)
backend/               Legacy backend (gpu provider, jobs, webhooks) used
                       for local smoke tests and the rollback path
contracts/             Vendored RPC contract (see above)
.github/workflows/     CI: contracts drift guard, Docker image builds,
                       Modal deploy, smoke tests
```

## Deploy

Modal deploys are CI-driven. Pushes to the trunk branch `master` that touch
`infrastructure/modal/**`, `docker/**`, `backend/pipelines/**`, or the
deploy workflow itself trigger `.github/workflows/deploy-modal.yml`,
which deploys all five apps to the `main` Modal environment in a
matrix job.

Note the two senses of "main": the git trunk is `master`
(`deploy-modal.yml` triggers on `branches: [master]`), while `main` is the
name of the production **Modal environment** it deploys into. This paragraph
said "pushes to `main`" until 2026-08-07, which read as a branch that does not
exist and understated the blast radius of a merge.

Never run `modal deploy` from a workstation as part of a release path.
The repo-secret-backed CI deploy is the single source of truth for
what is live.

PR builds deploy to the `staging` Modal environment and post a deploy
summary comment.

## Local development

The Modal infra README at `infrastructure/modal/README.md` covers
one-time Modal setup, env vars, and end-to-end smoke testing from the
backend. The Docker images live under `docker/<tool>/` and can be
built locally for offline reproduction of pipeline behavior.

## Rollback

The backend retains a RunPod-pods code path behind
`GPU_PROVIDER=runpod_emergency`. See `infrastructure/modal/README.md`
for the break-glass procedure.
