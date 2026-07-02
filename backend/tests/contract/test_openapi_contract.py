"""Contract test: every endpoint the SDK calls MUST appear in the OpenAPI spec.

Requirements: API-09 / API-12 — the published /api/v1/* surface is the contract
the bindwave-python SDK depends on. This test locks the SDK's frozen endpoint
inventory (_sdk_contract_v0_1_0.py) against app.openapi()['paths'].

If this test fails after a backend change, you either:
  (a) accidentally removed/renamed an endpoint the SDK depends on
      (rollback, or bump the SDK contract to a new frozen file), or
  (b) intentionally retired an endpoint and need to bump the SDK contract major
      version (freeze _sdk_contract_v0_2_0.py and update the SDK).

Import convention (F9): `cd backend && pytest` puts `backend/` on sys.path as
the root, so this test imports `from main import app` and
`from tests.contract._sdk_contract_v0_1_0 import ...` — NOT `from backend.*`.
This matches every existing backend test (e.g. backend/tests/jobs/*).
"""

from main import app

from tests.contract._sdk_contract_v0_1_0 import SDK_CONTRACT_V0_1_0


def test_openapi_contains_sdk_contract():
    spec = app.openapi()
    for entry in SDK_CONTRACT_V0_1_0:
        method, path = entry["method"], entry["path"]
        assert path in spec["paths"], f"{method} {path} not in spec — SDK is broken"
        path_methods = spec["paths"][path]
        assert method.lower() in path_methods, f"{method} {path} method missing"
        op = path_methods[method.lower()]
        # Assert the declared status code is documented on the operation.
        assert str(entry["status"]) in op.get("responses", {}), \
            f"{method} {path} response {entry['status']} not documented"


def test_sdk_contract_endpoints_are_only_v1():
    """Defensive: no entry in the SDK contract should point outside /api/v1/*."""
    for entry in SDK_CONTRACT_V0_1_0:
        assert entry["path"].startswith("/api/v1/"), \
            f"SDK contract points outside /api/v1/: {entry['path']}"
