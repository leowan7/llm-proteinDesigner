---
phase: 13-public-api
plan: 05
subsystem: bindwave-python SDK (async + convenience layer)
status: complete
tags: [phase-13, sdk-async, pagination, wait-and-download]
requires: [13-04]
provides:
  - AsyncClient (httpx.AsyncClient transport, async context manager)
  - AsyncJobsResource + AsyncApiKeysResource
  - iter_all / iter_all_async cursor auto-paginators
  - Job.wait_until_complete / await_until_complete
  - Job.download_results / download_results_async
  - Job._client PrivateAttr back-reference
affects: [13-06, 13-07]
tech-stack:
  added: []
  patterns: [pydantic-PrivateAttr-backref, cursor-paginator-generator, poll-until-terminal]
key-files:
  created:
    - bindwave-python/src/bindwave/_async_client.py
    - bindwave-python/src/bindwave/_pagination.py
    - bindwave-python/tests/test_async_client.py
    - bindwave-python/tests/test_pagination.py
    - bindwave-python/tests/test_job_wait.py
    - bindwave-python/tests/test_job_download.py
    - bindwave-python/examples/batch_submit.py
    - bindwave-python/examples/async_pipeline.py
  modified:
    - bindwave-python/src/bindwave/jobs.py
    - bindwave-python/src/bindwave/api_keys.py
    - bindwave-python/src/bindwave/types/job.py
    - bindwave-python/src/bindwave/__init__.py
    - bindwave-python/CHANGELOG.md
decisions:
  - "_client back-reference implemented as a Pydantic v2 PrivateAttr (leading-underscore name) rather than a Field(exclude=True); PrivateAttr is the v2 idiom for a non-serializing private attribute and satisfies the plan's exclusion requirement (verified: absent from model_dump / model_dump_json)."
  - "iter_all/iter_all_async take the jobs *resource* (client.jobs) and call resource.list(cursor=...), matching the plan's iter_all(jobs_resource, **filters) signature and PATTERNS.md body."
  - "wait/timeout tests drive terminal state via respx side_effects with sleep monkeypatched to a no-op and poll_every=0; the timeout test injects a fake time.monotonic sequence so the loop exits instantly — zero real waiting."
metrics:
  duration: ~25m
  completed: 2026-07-02
  tasks: 2
  files: 13
  tests_added: 19
  tests_total: 41
---

# Phase 13 Plan 05: SDK Async + Pagination + Wait/Download Summary

AsyncClient mirrors the sync Client 1:1 over httpx.AsyncClient (same retry, Idempotency-Key auto-gen, and RFC 7807 exception parsing); adds cursor auto-pagination (`iter_all`/`iter_all_async`), poll-until-terminal (`wait_until_complete`/`await_until_complete`), and presigned-URL result download (`download_results`/`download_results_async`) — closing API-12 and making the SDK feature-complete for 13-06/13-07.

## What Shipped

**Task 1 — AsyncClient + async resources + paginator scaffolding** (commit `9a66325`)
- `_async_client.py`: `AsyncClient` mirroring `_client.py` line-for-line with `httpx.AsyncClient`, `await self._http.request(...)`, `await asyncio.sleep(...)` on the 429/5xx/network retry paths, `aclose()`, and `__aenter__`/`__aexit__`.
- `jobs.py`: `AsyncJobsResource` (async `submit`/`get`/`list`/`cancel`).
- `api_keys.py`: `AsyncApiKeysResource` (async `list`/`revoke`).
- `_pagination.py`: `iter_all` + `iter_all_async` cursor walkers (created here because `__init__.py` imports them).
- `__init__.py`: replaced the `NotImplementedError` AsyncClient placeholder with the real class; exported async resources + paginators.
- `test_async_client.py`: 10 respx-backed async tests.

**Task 2 — iter_all methods + Job convenience + `_client` pinning** (commit `9e5ff3b`)
- `jobs.py`: `JobsResource.iter_all` + `AsyncJobsResource.iter_all_async`; F6 retrofit pins `job._client = self._client` on all 8 sync+async submit/get/list/cancel returns.
- `types/job.py`: `Job._client` PrivateAttr (never serialized), `TERMINAL_STATES = {COMPLETE, FAILED, CANCELLED}`, and the four convenience methods.
- `test_pagination.py`, `test_job_wait.py`, `test_job_download.py`: 9 tests.
- `examples/batch_submit.py` + `async_pipeline.py`; `CHANGELOG.md` 0.1.0 release.

## Key Facts (per handoff requirements)

- **JobStatus terminal members used:** `JobStatus.COMPLETE` (`"complete"`), `JobStatus.FAILED` (`"failed"`), `JobStatus.CANCELLED` (`"cancelled"`) — read from the real enum in `bindwave-python/src/bindwave/types/job.py` (shipped by 13-04). Defined once as `TERMINAL_STATES` in that module.
- **`_client` pinning:** applied on all 8 job methods — sync `JobsResource.submit/get/list/cancel` and async `AsyncJobsResource.submit/get/list/cancel`. The field is a Pydantic `PrivateAttr` (leading-underscore `_client`), which is excluded from serialization by construction — verified absent from both `model_dump()` and `model_dump_json()`.
- **No real sleeps:** `wait`/`timeout` tests monkeypatch `time.sleep`/`asyncio.sleep` to no-ops, use `poll_every=0`, and (timeout case) a fake `time.monotonic` sequence. Full 41-test suite runs in ~4s.
- **Scope:** both commits touch only `bindwave-python/` paths (verified via `git diff --name-only 9a66325^ 9e5ff3b`). Backend untouched.

## Test Results

`cd bindwave-python && PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest -q` → **41 passed in 4.06s** (22 pre-existing + 10 async + 9 convenience; 0 failed). Wall-clock ~5s including interpreter startup — proof nothing sleeps for real.

## Deviations from Plan

**1. [Rule 3 - Blocking] `_pagination.py` created in Task 1 rather than Task 2**
- **Found during:** Task 1 (`__init__.py` verify).
- **Issue:** The plan lists `_pagination.py` under Task 2, but Task 1's `__init__.py` update imports `iter_all`/`iter_all_async`, and Task 1's acceptance check (`from bindwave import AsyncClient`) exercises that import chain. Deferring the file would make Task 1's own verify fail.
- **Fix:** Created the full, self-contained `_pagination.py` during Task 1 and committed it there; the `iter_all`/`iter_all_async` *resource methods* that consume it (and their tests) landed in Task 2 as planned. No behavioral divergence from the plan — only file-to-commit assignment.
- **Files:** `bindwave-python/src/bindwave/_pagination.py`
- **Commit:** `9a66325`

**2. [Rule 1 - Correctness] `_client` as Pydantic `PrivateAttr` instead of `Field(exclude=True)`**
- **Found during:** Task 2.
- **Issue:** The plan text says "Pydantic Field with exclude=True". In Pydantic v2, a leading-underscore attribute (`_client`) cannot be a regular `Field` — it is a private attribute and must use `PrivateAttr`. `PrivateAttr` is already excluded from serialization, which is exactly the plan's stated intent ("so it does not serialize").
- **Fix:** Declared `_client: Any | None = PrivateAttr(default=None)` with `arbitrary_types_allowed=True`. Verified `_client` is absent from `model_dump()` and `model_dump_json()`, satisfying the exclusion requirement (T-13-09 accept disposition).
- **Files:** `bindwave-python/src/bindwave/types/job.py`
- **Commit:** `9e5ff3b`

## Threat Model Adherence

- **T-13-09** (accept): `_client` back-reference dropped on serialize — realized via `PrivateAttr`; documented in the `Job` docstring with the re-attach instruction.
- **T-13-06** (mitigate/accept): `wait_until_complete` default `poll_every=30` + `timeout` kwarg bound wall-clock; `iter_all` unbounded-iteration caveat documented in its docstring (`itertools.islice`).

## Known Stubs

None. All methods are fully wired and exercised by tests against mocked HTTP.

## Self-Check: PASSED
- All created files verified present on disk (`_async_client.py`, `_pagination.py`, 4 test files, 2 examples).
- Both commits verified in `git log` (`9a66325`, `9e5ff3b`).
- Full SDK suite: 41 passed, 0 failed.
- Scope verified: only `bindwave-python/` paths in both commits.
