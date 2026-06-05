"""Tests for API key authentication dependency.

Requirements: API-02 (org_id + role tuple returned, no X-Org-Id needed).
Downstream plan: Plan 13-02 ships auth.api_key_dependencies.get_current_api_key.
"""

import pytest


def test_returns_org_role_tuple():
    """API-02: get_current_api_key returns (org_id, role) tuple matching get_active_org shape.

    API key calls authenticate as the org the key was created for, with the
    creator's role at creation time. No X-Org-Id header is required or used.
    The returned tuple is compatible with require_role_api (Plan 13-02).
    """
    pytest.skip("Pending: Plan 13-02 ships auth.api_key_dependencies.get_current_api_key")
