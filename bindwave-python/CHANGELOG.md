# Changelog

All notable changes to `bindwave` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-04

First feature-complete SDK release.

### Added
- Package skeleton + public surface contract (Plan 13-02): importable `Client`,
  `AsyncClient`, exception hierarchy, and typed models.
- Synchronous `Client` (Plan 13-04): `jobs.submit` / `get` / `list` / `cancel`
  and `api_keys.list` / `revoke`, with `Authorization: Bearer` auth, 429/5xx
  auto-retry (exponential backoff, `Retry-After` honored), and `Idempotency-Key`
  auto-generation on job submit.
- Typed models: `Job`, `JobStatus`, `Candidate`, `JobListPage`, `ApiKey`.
- Exception hierarchy with RFC 7807 `application/problem+json` parsing:
  `BindwaveError`, `BindwaveAuthError`, `BindwaveRateLimitError`,
  `BindwaveValidationError`, `BindwaveJobError`, `BindwaveAPIError`.
- Asynchronous `AsyncClient` (Plan 13-05): a 1:1 async mirror of `Client` via
  `httpx.AsyncClient`, same retry / idempotency / exception behavior, usable as
  `async with AsyncClient(...) as client`.
- Cursor auto-paginator (Plan 13-05): `client.jobs.iter_all(**filters)` (lazy
  generator) and `client.jobs.iter_all_async(**filters)` (async generator) walk
  the cursor until exhausted.
- Convenience methods on `Job` (Plan 13-05): `wait_until_complete` /
  `await_until_complete` (poll until terminal status, with `timeout`) and
  `download_results` / `download_results_async` (fetch each candidate's presigned
  PDB to `dest_dir/{job_id}-candidate-{rank}.pdb`, returning `{rank: Path}`).
