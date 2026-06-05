"""Contract test: OpenAPI surface matches the committed snapshot.

Requirements: API-09 (only /api/v1/* paths visible in the published spec).
Active test — not stubbed. Passes immediately after Wave 0 because:
  - All 12 legacy routers have include_in_schema=False (Task 2 in Plan 13-01)
  - No /api/v1/* router is mounted yet (Plan 13-03 lands the router)
  - Both sorted(spec['paths'].keys()) and the fixture body are effectively empty

Plan 13-07 regenerates the snapshot with the final post-D-15 surface.
"""

import os
from pathlib import Path

from main import app


def test_openapi_paths_match_snapshot():
    """API-09: The published OpenAPI path list matches the locked snapshot fixture.

    Any PR that adds a new /api/v1/* route OR accidentally exposes an internal route
    must deliberately update _openapi_paths_snapshot.txt. This makes the API surface
    change reviewable before it ships.

    Failure message is explicit: 'OpenAPI surface changed — review with the team
    and update the snapshot file deliberately'.
    """
    spec = app.openapi()
    # If spec has no 'paths' key (empty spec), treat as empty list.
    paths = sorted(spec.get("paths", {}).keys())

    snapshot_file = Path(__file__).parent / "_openapi_paths_snapshot.txt"
    with open(snapshot_file) as f:
        expected = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    assert paths == expected, (
        f"OpenAPI surface changed — review with the team and update the snapshot file deliberately.\n"
        f"Current paths:  {paths}\n"
        f"Expected paths: {expected}"
    )
