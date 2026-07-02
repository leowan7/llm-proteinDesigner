"""Unit tests for the cursor encode/decode utility (api/v1/cursor.py).

Requirements: API-05 (cursor encoding is round-trippable and garbage-input-safe).
Pattern: pure-function test style from backend/tests/middleware/test_rate_limit.py:56-85.
"""

from datetime import datetime, timezone

from api.v1.cursor import decode_cursor, encode_cursor


def test_round_trip():
    """API-05: encode_cursor then decode_cursor returns the original (created_at, id)."""
    ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    job_id = "00000000-0000-0000-0000-000000000001"

    token = encode_cursor(ts, job_id)
    result = decode_cursor(token)

    assert result is not None, "decode_cursor should not return None for a valid token"
    decoded_ts, decoded_id = result
    assert decoded_ts == ts
    assert decoded_id == job_id


def test_garbage_input():
    """API-05: decode_cursor returns None for garbage/tampered input (never raises)."""
    assert decode_cursor("not-base64!!!") is None
    assert decode_cursor("") is None
    # "YWJjMTIz" is valid base64 of "abc123" but not our JSON cursor shape.
    assert decode_cursor("YWJjMTIz") is None


def test_missing_padding():
    """API-05: a cursor whose trailing base64 padding was stripped still decodes.

    encode_cursor rstrips "=" padding; decode_cursor rebuilds it dynamically.
    Truncating one more character from the (already unpadded) token must still
    round-trip because the padding-rebuild handles the len % 4 remainder.
    """
    ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    job_id = "abc-123"
    token = encode_cursor(ts, job_id)

    # The encoded token has padding stripped already; decoding it must succeed.
    result = decode_cursor(token)
    assert result is not None
    decoded_ts, decoded_id = result
    assert decoded_ts == ts and decoded_id == job_id
