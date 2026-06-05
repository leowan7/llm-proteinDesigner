"""Integration tests for RFC 7807 problem+json error responses on /api/v1/* paths.

Requirements: API-07 (application/problem+json on /api/v1/*; web-flow keeps existing shape).
Downstream plan: Plan 13-03 ships api/v1/errors.py exception handlers + registers them in main.py.
"""

import pytest


def test_problem_json():
    """API-07: HTTP errors on /api/v1/* paths return application/problem+json.

    The response Content-Type is 'application/problem+json'.
    The body has: type (URL), title, status (int), detail (str), instance (path).
    Example: 404 on GET /api/v1/jobs/nonexistent returns the RFC 7807 envelope.
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/errors.py + wires exception handlers in main.py")


def test_validation_error_problem_json():
    """API-07: Pydantic RequestValidationError on /api/v1/* returns application/problem+json.

    The 422 response includes an 'errors' list with loc/msg/type per API-07.
    The web-flow 422 response keeps its default FastAPI shape (no problem+json).
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/errors.py + wires validation exception handler")


def test_web_flow_regression():
    """API-07: Errors on non-/api/v1/* paths keep the existing FastAPI error shape.

    The exception handler checks request.url.path.startswith('/api/v1/') and falls
    through to the FastAPI default handler for web-flow routes.
    This test ensures Plan 13-03 does not regress web-flow error responses.
    """
    pytest.skip("Pending: Plan 13-03 ships api/v1/errors.py + wires exception handlers in main.py")
