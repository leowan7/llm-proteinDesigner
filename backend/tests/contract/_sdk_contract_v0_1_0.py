"""Frozen SDK contract for bindwave-python 0.1.0.

This file is the source of truth for what endpoints the SDK calls.
The contract test in test_openapi_contract.py reads this list and asserts
the FastAPI OpenAPI spec covers every entry.

IMPORTANT — paths match the EMITTED OpenAPI spec verbatim, including the
trailing slash on collection paths. Under a prefixed router, `@router.post("/")`
and `@router.get("/")` emit `/api/v1/jobs/` and `/api/v1/api-keys/` (with the
trailing slash) into app.openapi()['paths']. The contract test does an exact
`assert path in spec["paths"]`, so a slashless entry would fail. The SDK calls
these same strings. See _openapi_paths_snapshot.txt for the ground-truth surface.

The two `/api/v1/jobs/` entries share a path but are distinct method operations
(POST submit + GET list), so this list has 6 entries across 5 unique paths.

DO NOT EDIT without bumping bindwave-python version (freeze the file as
_sdk_contract_v0_2_0.py for the next surface and keep this one for regression).
"""

SDK_CONTRACT_V0_1_0 = [
    {"method": "POST", "path": "/api/v1/jobs/",
     "req_fields": ["tool", "parameters"], "resp_fields": ["id", "status"],
     "status": 201, "since": "0.1.0"},
    {"method": "GET", "path": "/api/v1/jobs/{job_id}",
     "req_fields": [], "resp_fields": ["id", "status", "candidates"],
     "status": 200, "since": "0.1.0"},
    {"method": "GET", "path": "/api/v1/jobs/",
     "req_fields": [], "resp_fields": ["data", "next_cursor"],
     "status": 200, "since": "0.1.0"},
    {"method": "POST", "path": "/api/v1/jobs/{job_id}/cancel",
     "req_fields": [], "resp_fields": ["id", "status"],
     "status": 200, "since": "0.1.0"},
    {"method": "GET", "path": "/api/v1/api-keys/",
     "req_fields": [], "resp_fields": ["data"],
     "status": 200, "since": "0.1.0"},
    {"method": "POST", "path": "/api/v1/api-keys/{key_id}/revoke",
     "req_fields": [], "resp_fields": ["id", "revoked_at"],
     "status": 200, "since": "0.1.0"},
]
