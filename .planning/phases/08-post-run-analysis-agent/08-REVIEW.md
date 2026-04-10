---
phase: 08-post-run-analysis-agent
reviewed: 2026-04-10T00:00:00Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - backend/agent/analysis/__init__.py
  - backend/agent/analysis/cache.py
  - backend/agent/analysis/pdb_features.py
  - backend/agent/analysis/ranking.py
  - backend/agent/analysis/refolding.py
  - backend/agent/analysis/report.py
  - backend/agent/analysis/tools.py
  - backend/agent/router.py
  - backend/tests/agent/test_analysis_tools.py
  - backend/tests/agent/test_pdb_features.py
  - backend/tests/agent/test_refolding.py
  - backend/tests/agent/test_report.py
  - frontend/src/components/chat/ChatPage.tsx
  - frontend/src/pages/JobPage.tsx
findings:
  critical: 1
  warning: 5
  info: 4
  total: 10
status: issues_found
---

# Phase 08: Code Review Report

**Reviewed:** 2026-04-10T00:00:00Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

This phase introduces the post-run analysis agent subsystem: an in-memory candidate cache, Pandas-based ranking/filtering, BioPython structural feature extraction, a refolding job submission handler, and a three-format report generator (PDF/CSV/Markdown). The router file and two frontend pages were also reviewed.

Overall the architecture is well-structured and security-conscious. Ownership checks are consistently applied at DB query boundaries, the DoS cap on shortlist size is enforced in two layers, and PDB path construction is correctly sanitised. The most significant finding is an information-exposure bug in the router's SSE error handler that leaks internal exception details to the client. There are also several logic-correctness warnings worth addressing before production use.

---

## Critical Issues

### CR-01: Internal exception details leaked to SSE client in router error handler

**File:** `backend/agent/router.py:189`

**Issue:** The `except anthropic.APIError` branch correctly sanitises the error message via `getattr(exc, "message", str(exc))`. However, a bare `except Exception` block directly below it silently swallows the exception and returns a generic string — this part is fine. The problem is in the `APIError` branch: `getattr(exc, "message", str(exc))` will fall back to `str(exc)` for any `APIError` subclass that does not carry a `.message` attribute (e.g. `anthropic.RateLimitError`, `anthropic.InternalServerError`). `str(exc)` on those objects includes the raw HTTP response body and request headers, which may contain the full API key value in the `x-api-key` header reflected in error responses from certain SDK versions.

Additionally, within `event_generator`, if a DB or storage exception escapes before the `anthropic.APIError` catch (e.g. inside `dispatch_tool`), it is caught by the bare `except Exception` block and a generic message is returned correctly — but the history-persistence calls (`update_agent_history`, `append_message`) outside the try block at lines 164–173 are *inside* the `try` scope and their exceptions are also caught by the bare handler, silently dropping the persistence without any log entry.

**Fix:**

```python
except anthropic.APIError as exc:
    # Use only a stable, safe attribute — never str(exc) which may include headers/body
    error_msg = getattr(exc, "message", None) or type(exc).__name__
    yield f"data: {json.dumps({'type': 'error', 'text': f'Agent error: {error_msg}'})}\n\n"
except Exception:
    logger.exception("Unexpected error in agent SSE stream for session %s", req.session_id)
    yield f"data: {json.dumps({'type': 'error', 'text': 'An unexpected error occurred. Please try again.'})}\n\n"
```

---

## Warnings

### WR-01: Cache hit path in handle_load_job_results bypasses ownership check

**File:** `backend/agent/analysis/tools.py:155-156`

**Issue:** When `get_cached(job_id)` returns a non-None value, the function immediately calls `_format_load_response(cached, job_id, tool="cached")` without verifying that the requesting `user_id` owns the cached job. The comment on line 8 says the cache is "only populated by ownership-checked load_job_results calls" — but this is a process-level assertion, not an enforced invariant. In a multi-user process (single worker handling multiple users' requests in the same event loop), if user A loads job X, user B can then call `load_job_results` with job X's ID and receive the cached result without any ownership verification.

This is the T-08-02 threat the docstring acknowledges but does not fully mitigate. The cache key is just `job_id` — there is no `(user_id, job_id)` composite key.

**Fix:** Change the cache key to `f"{user_id}:{job_id}"` in both `set_cached` and `get_cached` calls, or store the owning `user_id` alongside the candidates and check it on retrieval.

```python
# In tools.py handle_load_job_results
cache_key = f"{user_id}:{job_id}"
cached = get_cached(cache_key)
if cached is not None:
    return _format_load_response(cached, job_id, tool="cached")
# ... after DB fetch ...
set_cached(cache_key, candidates)
```

Apply the same composite key pattern in `handle_analyze_candidates`, `handle_flag_red_flags`, `handle_submit_refolding_job`, and `handle_generate_report`.

---

### WR-02: handle_analyze_candidates passes ascending=False for all sort_by metrics

**File:** `backend/agent/analysis/tools.py:341`

**Issue:** `rank_candidates(candidates, sort_by=sort_by)` is called with only two arguments. The `rank_candidates` function signature has `ascending=False` as default, which is correct for metrics like `ipTM` where higher is better. But for metrics like `dG` (lower/more negative is better), `Relaxed_Clashes` (lower is better), and `Surface_Hydrophobicity` (lower is better), the default `ascending=False` will rank the *worst* candidates first. The `METRIC_THRESHOLDS` dict already has `lower_is_better` flags for exactly this purpose, but `handle_analyze_candidates` never consults them.

**Fix:** Derive the `ascending` argument from `METRIC_THRESHOLDS` before calling `rank_candidates`:

```python
threshold_config = METRIC_THRESHOLDS.get(sort_by, {})
ascending = threshold_config.get("lower_is_better", False)
ranked = rank_candidates(candidates, sort_by=sort_by, ascending=ascending)
```

---

### WR-03: report.py imports handle_flag_red_flags and calls it in-process, doubling DB fetch

**File:** `backend/agent/analysis/report.py:27,632`

**Issue:** `handle_generate_report` calls `await handle_flag_red_flags({"job_id": job_id}, user_id=user_id)` at line 632. `handle_flag_red_flags` does not hit the DB — it reads from cache — so there is no redundant DB round-trip. However, the import at line 27 is `from agent.analysis.tools import METRIC_THRESHOLDS, _assess_threshold, handle_flag_red_flags`. The leading underscore on `_assess_threshold` signals it is a private helper; importing it across module boundaries creates a coupling that will silently break if `tools.py` is refactored. This is a real maintainability risk in a small team codebase.

More critically: if the cache is empty when `handle_generate_report` is called (which the early-exit check at line 580 is meant to prevent), `handle_flag_red_flags` will return `status: error`. Line 634 checks `red_flags_data.get("status") == "success"` and falls back to `[]`, so the report proceeds with no red flags and no warning to the user or logs. The silent empty fallback obscures a logic error.

**Fix:** Add a log warning when the red_flags call returns a non-success status:

```python
red_flags_data = json.loads(red_flags_json)
if red_flags_data.get("status") != "success":
    logger.warning(
        "handle_flag_red_flags returned non-success for job %s: %s",
        job_id, red_flags_data.get("message"),
    )
red_flags = red_flags_data.get("red_flags", []) if red_flags_data.get("status") == "success" else []
```

Move `_assess_threshold` to a shared location (e.g. `agent.analysis.thresholds`) if it needs to be used in both `tools.py` and `report.py`.

---

### WR-04: refolding.py partial INSERT failure leaves orphaned draft rows

**File:** `backend/agent/analysis/refolding.py:155-184`

**Issue:** The loop that creates refolding jobs (lines 155–184) inserts each job individually with `await conn.execute(INSERT ...)` inside a single `async with pool.acquire() as conn` block — but there is no database transaction wrapping the loop. If the INSERT for candidate rank 2 succeeds but rank 3 fails (e.g. due to a constraint violation or transient error), the function catches the exception at line 186 and returns an error response, but the row for rank 2 is already committed. The caller receives `status: error` and believes no jobs were created, while an orphaned draft row exists in the DB.

**Fix:** Wrap the loop in an explicit transaction:

```python
async with conn.transaction():
    for rank in candidate_ranks:
        await conn.execute(INSERT ...)
        created_jobs.append(...)
```

---

### WR-05: router.py uses synchronous Anthropic client in async context without executor

**File:** `backend/agent/router.py:99`

**Issue:** `client.messages.create(...)` at line 99 is the *synchronous* Anthropic SDK call (`anthropic.Anthropic`, not `anthropic.AsyncAnthropic`). This is called directly inside `async def event_generator()` without `asyncio.run_in_executor`. A blocking synchronous HTTP call here will block the entire asyncio event loop for the duration of the API request (potentially several seconds per inference turn), preventing any other coroutines — including other users' requests — from running.

`_generate_title_background` correctly wraps its client call in `loop.run_in_executor` (line 215), showing the pattern is known. The main loop at line 99 does not use it.

**Fix:** Either switch to `anthropic.AsyncAnthropic` and `await client.messages.create(...)`, or wrap the synchronous call in `run_in_executor`:

```python
# Option A (preferred): use async client
client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
response = await client.messages.create(...)

# Option B: executor wrapper
loop = asyncio.get_event_loop()
response = await loop.run_in_executor(None, lambda: client.messages.create(...))
```

---

## Info

### IN-01: filter_candidates silently skips candidates missing a metric rather than treating them as non-matching

**File:** `backend/agent/analysis/ranking.py:102-105`

**Issue:** When a candidate does not have the filtered metric in its `scores` dict, it is silently excluded from results (`continue`). This means `filter_candidates(candidates, {"dG": {"<": -30}})` will drop candidates that have no `dG` score rather than keeping or explicitly flagging them. Depending on the expected data shape, missing-metric candidates may represent failed computations that should still be surfaced to the user. The current behaviour is undocumented.

**Fix:** Add a docstring note clarifying that missing-metric candidates are excluded. If the desired behaviour should be to treat missing as non-matching (which is reasonable), that should be explicit:

```python
if value is None:
    # Candidates without this metric do not match the filter
    continue
```

---

### IN-02: Bare `os` import inside a loop in refolding.py

**File:** `backend/agent/analysis/refolding.py:149`

**Issue:** `import os` appears inside the fallback branch of the target PDB source resolution logic (line 149). Module-level imports executed inside function bodies work correctly but violate PEP 8 and CLAUDE.md coding conventions. Since `os` is used only in the fallback path, move it to the top of the file.

**Fix:** Add `import os` at the top of `refolding.py` alongside the other stdlib imports.

---

### IN-03: test_refolding.py uses @pytest.mark.asyncio while test_analysis_tools.py uses @pytest.mark.anyio — inconsistent async test marker across the same test suite

**File:** `backend/tests/agent/test_refolding.py:65`, `backend/tests/agent/test_report.py:263`, `backend/tests/agent/test_analysis_tools.py:277`

**Issue:** `test_analysis_tools.py` uses `@pytest.mark.anyio` while `test_refolding.py` and `test_report.py` use `@pytest.mark.asyncio`. This inconsistency indicates different async test runner plugins are expected (`anyio` vs `pytest-asyncio`). If only one plugin is installed, half the async tests will silently not execute (the markers are ignored rather than raising an error unless `asyncio_mode = "strict"` is configured). This can result in the CI pipeline reporting all tests as passing while half the async tests were skipped.

**Fix:** Standardise on one marker across all test files. If `pytest-anyio` is the project standard (it handles both asyncio and trio), replace `@pytest.mark.asyncio` with `@pytest.mark.anyio` in `test_refolding.py` and `test_report.py`. Verify `anyio` is in `requirements.txt` or `pyproject.toml`.

---

### IN-04: JobPage Export Report button navigates to /chat without a sessionId, URL encoding uses encodeURIComponent which is correct but the ?prompt param only works if the user lands on a session — the bare /chat route redirect may lose the query param

**File:** `frontend/src/pages/JobPage.tsx:192-194`

**Issue:** The Export Report button navigates to `/chat?prompt=<encoded_text>`. The bare `/chat` route triggers the `resolveSession` effect which calls `navigate(..., { replace: true })` on line 201 — a `replace` navigation to `/chat/<sessionId>` that does not preserve query parameters from the original URL. As a result, the `?prompt=` param is lost and the pre-filled prompt never appears in the ChatInput.

`ChatPage` reads `searchParams` on line 96 inside a `useEffect` with an empty dependency array `[]`, so it fires once on mount — but by the time the session redirect resolves and the component remounts at the new URL, `searchParams` is empty.

**Fix:** Pass the prompt through the navigation state instead of a query param, or include it when redirecting:

```typescript
// In JobPage
navigate(`/chat?prompt=${encodeURIComponent(prompt)}`);

// In ChatPage resolveSession, preserve query params:
navigate(`/chat/${newSession.id}${window.location.search}`, { replace: true });
// and in existing session navigation:
navigate(`/chat/${sessions[0].id}${window.location.search}`, { replace: true });
```

---

_Reviewed: 2026-04-10T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
