"""Tests for API key creation, format, verification, and revocation.

Requirements: API-01 (creation + hash-at-rest), API-03 (revocation).
Plan 13-02 ships auth.api_keys + auth.api_key_dependencies.
Plan 13-04 ships the web endpoint POST /user/api-keys.
"""

import re

import pytest

from auth import api_keys as api_keys_module
from auth.api_keys import generate_api_key, verify_api_key
from config import settings


def test_create_returns_plaintext():
    """API-01: API key creation returns the plaintext exactly once at creation time.

    The plaintext is returned in the POST response body. The DB stores only the
    HMAC-SHA256 hex digest. Subsequent GET /user/api-keys responses omit plaintext.
    """
    pytest.skip("Pending: Plan 13-04 ships web create endpoint POST /user/api-keys")


def test_revoked_key_rejects():
    """API-03: A revoked API key is rejected with 401 immediately after revocation.

    Revoke sets revoked_at on the row. The get_current_api_key dep checks
    revoked_at IS NULL in the prefix lookup query. See test_auth.py::
    test_revoked_key_rejects for the dep-level assertion (fetchrow -> None).
    """
    pytest.skip("Pending: Plan 13-04 ships the revoke endpoint POST /user/api-keys/{id}/revoke")


def test_generate_api_key_format():
    """API-01: generate_api_key returns plaintext matching bw_<env>_<random> format."""
    plaintext, prefix, h = generate_api_key("live")
    assert plaintext.startswith("bw_live_")
    assert re.match(r"^bw_live_[A-Za-z0-9_-]{30,32}$", plaintext), plaintext
    assert len(prefix) == 12
    assert prefix == plaintext[:12]
    assert len(h) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", h)

    # env="test" switches the second segment.
    test_plain, _, _ = generate_api_key("test")
    assert test_plain.startswith("bw_test_")


def test_verify_api_key_constant_time():
    """API-01: verify_api_key round-trips a matching pair and rejects mismatches."""
    plaintext, _prefix, h = generate_api_key("test")
    assert verify_api_key(plaintext, h) is True
    # Tamper with the plaintext -> hash no longer matches.
    assert verify_api_key(plaintext + "x", h) is False
    # Tamper with the stored hash -> mismatch.
    assert verify_api_key(plaintext, h[:-1] + ("0" if h[-1] != "0" else "1")) is False


def test_verify_api_key_empty_pepper_fails_closed(monkeypatch):
    """API-01: with both peppers empty, verify returns False (no auth bypass)."""
    plaintext, _prefix, h = generate_api_key("test")
    monkeypatch.setattr(settings, "api_key_pepper", "")
    monkeypatch.setattr(settings, "api_key_pepper_prev", "")
    assert verify_api_key(plaintext, h) is False


def test_pepper_rotation(monkeypatch):
    """API-01: a key hashed with the OLD pepper still verifies after rotation.

    Simulate rotation: the key was minted under the original pepper; the operator
    rotates api_key_pepper to a new value and moves the original into
    api_key_pepper_prev. verify_api_key must fall back to the prev pepper.
    """
    original_pepper = settings.api_key_pepper
    # Mint under the original (current test) pepper.
    plaintext, _prefix, old_hash = generate_api_key("test")

    # Rotate: new current pepper, original demoted to prev.
    monkeypatch.setattr(settings, "api_key_pepper", "rotated_new_pepper_value")
    monkeypatch.setattr(settings, "api_key_pepper_prev", original_pepper)

    # New pepper alone would not match the old hash...
    new_hash = generate_api_key("test")[2]
    assert new_hash != old_hash
    # ...but the prev-pepper fallback still verifies the old hash.
    assert verify_api_key(plaintext, old_hash) is True


def test_pepper_rotation_logs_prev_match(monkeypatch, caplog):
    """API-01: a prev-pepper match emits a WARNING for the rotation runbook."""
    import logging

    original_pepper = settings.api_key_pepper
    plaintext, _prefix, old_hash = generate_api_key("test")
    monkeypatch.setattr(settings, "api_key_pepper", "rotated_new_pepper_value")
    monkeypatch.setattr(settings, "api_key_pepper_prev", original_pepper)

    with caplog.at_level(logging.WARNING, logger=api_keys_module.__name__):
        assert verify_api_key(plaintext, old_hash) is True
    assert any("PREV pepper" in r.message for r in caplog.records)
