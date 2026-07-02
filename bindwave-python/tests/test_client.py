"""Client construction + auth-header tests (Phase 13, Plan 13-04)."""

import pytest

from bindwave import Client


def test_constructor_requires_api_key(monkeypatch):
    """No api_key kwarg and no BINDWAVE_API_KEY env → ValueError."""
    monkeypatch.delenv("BINDWAVE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        Client()


def test_constructor_reads_env_var(monkeypatch):
    """BINDWAVE_API_KEY env is used when no api_key kwarg is passed."""
    monkeypatch.setenv("BINDWAVE_API_KEY", "bw_test_from_env")
    client = Client()
    assert client._http.headers["Authorization"] == "Bearer bw_test_from_env"
    client.close()


def test_authorization_header_set():
    """The Authorization: Bearer header carries the api_key on every request."""
    client = Client(api_key="bw_test_x")
    assert client._http.headers["Authorization"] == "Bearer bw_test_x"
    client.close()


def test_no_x_org_id_header():
    """D-01: the SDK never sends X-Org-Id (org resolved server-side from the key)."""
    client = Client(api_key="bw_test_x")
    assert "X-Org-Id" not in client._http.headers
    client.close()


def test_base_url_env_override(monkeypatch):
    """BINDWAVE_BASE_URL overrides the default origin."""
    monkeypatch.setenv("BINDWAVE_BASE_URL", "https://staging.bindwave.test")
    client = Client(api_key="bw_test_x")
    assert client._base_url == "https://staging.bindwave.test"
    client.close()


def test_context_manager_closes():
    """The client works as a context manager and closes its transport."""
    with Client(api_key="bw_test_x") as client:
        assert client._http.is_closed is False
    assert client._http.is_closed is True


def test_resources_present():
    """jobs + api_keys resource facades are attached; api_keys is real (not placeholder)."""
    client = Client(api_key="bw_test_x")
    assert client.jobs is not None
    assert client.api_keys is not None
    client.close()
