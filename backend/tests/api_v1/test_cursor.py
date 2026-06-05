"""Unit tests for the cursor encode/decode utility (api/v1/cursor.py).

Requirements: API-05 (cursor encoding is round-trippable and garbage-input-safe).
Downstream plan: Plan 13-03 ships api/v1/cursor.py.
Pattern: pure-function test style from backend/tests/middleware/test_rate_limit.py:56-85.
"""

import pytest


def test_round_trip():
    """API-05: encode_cursor then decode_cursor returns the original (created_at, id).

    The cursor is a URL-safe base64-encoded JSON object with 'c' (ISO timestamp)
    and 'i' (UUID string) keys. decode_cursor(encode_cursor(ts, id)) == (ts, id).
    """
    try:
        from api.v1.cursor import encode_cursor, decode_cursor
    except ImportError:
        pytest.skip("Pending: Plan 13-03 ships api/v1/cursor.py")

    from datetime import datetime, timezone
    ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    job_id = "00000000-0000-0000-0000-000000000001"

    token = encode_cursor(ts, job_id)
    result = decode_cursor(token)

    assert result is not None, "decode_cursor should not return None for a valid token"
    decoded_ts, decoded_id = result
    assert decoded_ts == ts
    assert decoded_id == job_id


def test_garbage_input():
    """API-05: decode_cursor returns None for garbage/tampered input (does not raise).

    The router treats a None return as 'no cursor provided' and returns 400 to the caller.
    No exception should propagate from the decode function itself.
    """
    try:
        from api.v1.cursor import decode_cursor
    except ImportError:
        pytest.skip("Pending: Plan 13-03 ships api/v1/cursor.py")

    assert decode_cursor("not-base64!!!") is None
    assert decode_cursor("") is None
    assert decode_cursor("YWJj") is None  # valid base64 but not valid JSON cursor
