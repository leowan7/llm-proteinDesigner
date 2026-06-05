"""Tests for Stripe-style Idempotency-Key header behavior on POST /api/v1/jobs.

Requirements: API-04 (idempotency replay, body mismatch 422, in-progress 409).
Downstream plan: Plan 13-03 ships api/v1/jobs.py + api/v1/idempotency.py.
Schema reference: RESEARCH §2.9 (3-state lifecycle: pending | completed).
"""

import pytest


def test_replay():
    """API-04: Same (api_key_id, idempotency_key, body) within 24h replays stored response.

    The second identical POST returns the exact same response body + status code
    as the first, with X-Idempotency-Replay: 1 header added.
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/jobs.py + api/v1/idempotency.py")


def test_body_mismatch_returns_422():
    """API-04: Same idempotency_key with a different request body returns 422.

    Body mismatch is detected by comparing sha256(canonicalize(body)) against the
    stored request_body_hash. Returns application/problem+json per API-07.
    Reference: RESEARCH §2.9 lifecycle step 4.
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/jobs.py + api/v1/idempotency.py")


def test_pending_returns_409():
    """API-04: A concurrent request with the same key (status='pending') returns 409.

    The idempotency row is inserted at 'pending' when the first request begins
    processing. A concurrent retry hitting the same key sees 'pending' and gets 409
    with a 'retry after a few seconds' hint. Reference: RESEARCH §2.9 lifecycle step 3.
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/jobs.py + api/v1/idempotency.py")
