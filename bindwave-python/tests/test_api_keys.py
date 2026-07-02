"""ApiKeysResource tests (Phase 13, Plan 13-04). respx mocks the /api/v1 surface."""

import httpx
import respx

from bindwave import ApiKey, Client

BASE = "https://api.bindwave.test"


def _client() -> Client:
    return Client(api_key="bw_test_x", base_url=BASE)


@respx.mock
def test_list_returns_api_keys():
    respx.get(f"{BASE}/api/v1/api-keys/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "k-1",
                        "name": "ci",
                        "prefix": "bw_live_aaaa",
                        "created_at": "2026-06-05T12:00:00+00:00",
                        "last_used_at": None,
                    }
                ]
            },
        )
    )
    client = _client()
    keys = client.api_keys.list()
    assert len(keys) == 1
    assert isinstance(keys[0], ApiKey)
    assert keys[0].id == "k-1"
    assert keys[0].prefix == "bw_live_aaaa"
    # ApiKey never carries plaintext.
    assert not hasattr(keys[0], "plaintext")
    client.close()


@respx.mock
def test_revoke_returns_confirmation():
    route = respx.post(f"{BASE}/api/v1/api-keys/k-1/revoke").mock(
        return_value=httpx.Response(
            200, json={"id": "k-1", "revoked_at": "2026-06-05T12:00:00+00:00"}
        )
    )
    client = _client()
    result = client.api_keys.revoke("k-1")
    assert result["id"] == "k-1"
    assert "revoked_at" in result
    # Revoke is not a job submit → no Idempotency-Key.
    assert "Idempotency-Key" not in route.calls.last.request.headers
    client.close()


@respx.mock
def test_authorization_header_sent_on_api_keys():
    """D-01: Authorization Bearer present, X-Org-Id absent on api-keys calls."""
    route = respx.get(f"{BASE}/api/v1/api-keys/").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = _client()
    client.api_keys.list()
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer bw_test_x"
    assert "X-Org-Id" not in req.headers
    client.close()
