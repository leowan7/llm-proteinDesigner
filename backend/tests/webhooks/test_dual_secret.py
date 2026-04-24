"""Wave 0 RED tests for dual-secret webhook validation (D-10).

These tests reference a NOT-YET-EXISTING function
``webhooks.router.validate_webhook_signature``. Plan 11-04 implements that
function with the dual-secret signature:

    def validate_webhook_signature(
        body: bytes,
        signature: str | None,
        current_secret: str,
        prev_secret: str | None = None,
    ) -> str  # "current" | "prev" | "dev-skip"

Until that plan lands, these assertions will fail. We mark the module
``xfail(strict=False)`` so CI does not hard-fail Wave 0 — the whole point of
this file is to exist as RED scaffolding for Plan 11-04 to turn GREEN.

Per the execute-phase contract: ``pytest --collect-only`` MUST exit 0 on this
file (xfail collected, not import errored).
"""

import hashlib
import hmac
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "true")

# Whole-module xfail — Plan 11-04 flips these to GREEN by implementing D-10.
pytestmark = pytest.mark.xfail(
    reason="Wave 0 RED -- Plan 11-04 implements D-10 dual-secret validate_webhook_signature",
    strict=False,
)


def _sign(secret: str, body: bytes) -> str:
    """Compute hex HMAC-SHA256 signature the way webhooks.router does."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_current_secret_accepted():
    """Body signed with current_secret returns 'current'."""
    # Import inside the test so collection does not fail if the function is
    # not yet exported — xfail still catches the AttributeError at call time.
    from webhooks.router import validate_webhook_signature

    body = b'{"id":"job-1","status":"COMPLETED"}'
    sig = _sign("secret_current", body)

    which = validate_webhook_signature(
        body,
        sig,
        "secret_current",
        "secret_prev",
    )
    assert which == "current"


def test_prev_secret_accepted_with_warning(caplog):
    """Body signed with prev_secret returns 'prev' (allows rotation overlap).

    The caller is expected to log a warning on the prev-secret path; we accept
    either a return value of 'prev' or a warning entry in caplog.
    """
    from webhooks.router import validate_webhook_signature

    body = b'{"id":"job-2","status":"COMPLETED"}'
    sig = _sign("secret_prev", body)

    with caplog.at_level("WARNING"):
        which = validate_webhook_signature(
            body,
            sig,
            "secret_current",
            "secret_prev",
        )
    assert which == "prev"


def test_both_invalid_raises_401():
    """Body signed with an unrelated secret raises HTTPException(401)."""
    from webhooks.router import validate_webhook_signature

    body = b'{"id":"job-3","status":"COMPLETED"}'
    sig = _sign("other_secret", body)

    with pytest.raises(HTTPException) as excinfo:
        validate_webhook_signature(
            body,
            sig,
            "secret_current",
            "secret_prev",
        )
    assert excinfo.value.status_code == 401
