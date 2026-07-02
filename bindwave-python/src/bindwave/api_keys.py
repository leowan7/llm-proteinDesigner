"""ApiKeysResource — the api-keys facade on the sync Client (Phase 13, Plan 13-04).

Path constants are FULL ``/api/v1/...`` strings matching the published OpenAPI
spec verbatim (Plan 13-07 SDK⇄spec contract test). The SDK can only LIST
(metadata) and REVOKE — minting (which returns plaintext once) is a WEB-only flow
(POST /user/api-keys), never exposed through the SDK.
"""

from __future__ import annotations

from bindwave.types.api_key import ApiKey

# Full published paths (must match app.openapi()['paths'] verbatim — 13-07).
API_KEYS_PATH = "/api/v1/api-keys/"
API_KEY_REVOKE_PATH = "/api/v1/api-keys/{key_id}/revoke"

__all__ = ["ApiKeysResource"]


class ApiKeysResource:
    """List and revoke the caller's org API keys."""

    def __init__(self, client) -> None:
        self._client = client

    def list(self) -> list[ApiKey]:
        """List the caller's active org keys (revoked excluded server-side)."""
        response = self._client._request("GET", API_KEYS_PATH)
        data = response.json()
        return [ApiKey.model_validate(k) for k in data.get("data", [])]

    def revoke(self, key_id: str) -> dict:
        """Revoke a key by id. Returns the ``{id, revoked_at}`` confirmation dict."""
        response = self._client._request(
            "POST", API_KEY_REVOKE_PATH.format(key_id=key_id)
        )
        return response.json()
