"""Tests for API key creation, format, verification, and revocation.

Requirements: API-01 (creation + hash-at-rest), API-03 (revocation).
Downstream plan: Plan 13-02 ships auth.api_keys + auth.api_key_dependencies.
Plan 13-04 ships the web endpoint POST /user/api-keys.
"""

import pytest


def test_create_returns_plaintext():
    """API-01: API key creation returns the plaintext exactly once at creation time.

    The plaintext is returned in the POST response body. The DB stores only the
    HMAC-SHA256 hex digest. Subsequent GET /user/api-keys responses omit plaintext.
    """
    pytest.skip("Pending: Plan 13-04 ships web create endpoint POST /user/api-keys")


def test_revoked_key_rejects():
    """API-03: A revoked API key is rejected with 401 immediately after revocation.

    Revoke sets revoked_at on the row. The get_current_api_key dep checks
    revoked_at IS NULL in the prefix lookup query.
    """
    pytest.skip("Pending: Plan 13-02 ships auth.api_key_dependencies + Plan 13-04 ships the revoke endpoint")


def test_generate_api_key_format():
    """API-01: generate_api_key returns plaintext matching bw_<env>_<random> format.

    The prefix is the first 12 characters of the plaintext.
    """
    pytest.skip("Pending: Plan 13-02 ships auth.api_keys.generate_api_key")


def test_verify_api_key_constant_time():
    """API-01: verify_api_key uses hmac.compare_digest for constant-time comparison.

    Prevents timing side-channel on the token verification path.
    """
    pytest.skip("Pending: Plan 13-02 ships auth.api_keys.verify_api_key")


def test_pepper_rotation():
    """API-01: verify_api_key falls back to api_key_pepper_prev during rotation window.

    When api_key_pepper is rotated, keys signed with the previous pepper continue
    to verify until api_key_pepper_prev is cleared.
    """
    pytest.skip("Pending: Plan 13-02 ships auth.api_keys.verify_api_key with dual-pepper rotation")
