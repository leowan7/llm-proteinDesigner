"""/api/v1/* package — the public API surface (Phase 13).

Task 1 ships this as a bare package marker so the extract modules (cursor,
idempotency, errors) import cleanly. Task 2 replaces this with the router
aggregator once api.v1.jobs exists.
"""
