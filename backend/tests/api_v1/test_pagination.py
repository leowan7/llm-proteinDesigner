"""Integration tests for cursor pagination on GET /api/v1/jobs.

Requirements: API-05 (opaque cursor, default limit 25, max 100, filters).
Downstream plan: Plan 13-03 ships api/v1/jobs.py with cursor-based list endpoint.
"""

import pytest


def test_cursor_stable_under_insert():
    """API-05: Cursor pagination is stable when new jobs are inserted between pages.

    The cursor encodes (created_at DESC, id DESC) as a tiebreaker so newly-inserted
    jobs do not shift page boundaries. A job created after the cursor is taken does
    not appear on the next page.
    Reference: RESEARCH §2.3 (unsigned base64-encoded JSON cursor design).
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/jobs.py with cursor-based list endpoint")
