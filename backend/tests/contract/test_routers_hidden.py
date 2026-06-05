"""Contract test: all legacy routers have include_in_schema=False.

Requirements: API-09 (D-15 — only /api/v1/* routes appear in the OpenAPI spec).
Active test — not stubbed. Passes immediately after Task 2 in Plan 13-01 flips
include_in_schema=False on all 12 legacy routers.

Test approach: iterate app.routes and assert that any route whose tags include
a legacy-router tag has include_in_schema=False. Routes tagged with legacy namespaces
must not appear in the published spec.
"""

from main import app


# Tags that belong to legacy (non-v1) routers per RESEARCH §2.8 table.
_LEGACY_TAGS = {
    "auth", "agent", "admin", "billing", "debug",
    "jobs", "organizations", "invitations", "pdb",
    "sessions", "user", "webhooks",
}


def test_legacy_routers_hidden_from_schema():
    """API-09: Every route tagged with a legacy router tag has include_in_schema=False.

    Checks that the D-15 flip landed correctly on all 12 legacy routers. Any
    route that is tagged with a legacy namespace AND has include_in_schema=True
    (or missing the flag) would pollute the published OpenAPI spec.
    """
    violations = []

    for route in app.routes:
        # Only check routes that have explicit tags (APIRouter routes)
        route_tags = set(getattr(route, "tags", None) or [])
        if not route_tags.intersection(_LEGACY_TAGS):
            continue

        # The route has a legacy tag — it MUST have include_in_schema=False
        if getattr(route, "include_in_schema", True):
            violations.append(
                f"Route {getattr(route, 'path', '?')} "
                f"(tags={route_tags}) has include_in_schema not False"
            )

    assert not violations, (
        "Legacy routes are visible in the OpenAPI spec — add include_in_schema=False "
        "to their APIRouter constructors:\n" + "\n".join(violations)
    )
