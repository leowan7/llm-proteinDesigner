"""Idempotency-Key generation (Phase 13, Plan 13-04).

The backend ``POST /api/v1/jobs/`` requires an ``Idempotency-Key`` header. The SDK
auto-generates a fresh uuid4 hex per submit() call so the caller never sees a 400
for a missing key; the caller MAY override it to make a retry idempotent.
"""

import uuid


def generate_idempotency_key() -> str:
    """Return a fresh 32-char uuid4 hex string."""
    return uuid.uuid4().hex
