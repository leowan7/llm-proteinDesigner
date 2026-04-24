"""Dual-secret webhook validation tests (D-10).

Phase 11 Plan 04 turned these tests GREEN by implementing
``webhooks.router.validate_webhook_signature`` with the dual-secret signature:

    def validate_webhook_signature(
        body: bytes,
        signature: str | None,
        current_secret: str,
        prev_secret: str | None = None,
    ) -> str  # "current" | "prev" | "dev-skip"

The rotation runbook (docs/deploy.md, Plan 11-05) operates the _PREV secret
fallback. A signature made with the previous secret is accepted AND logs a
WARNING containing "PREV secret" so the operator knows traffic is still
flowing against the old secret during rotation.
"""

import hashlib
import hmac
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "true")

from webhooks.router import validate_webhook_signature


def _sign(secret: str, body: bytes) -> str:
    """Compute hex HMAC-SHA256 signature the way webhooks.router does."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_current_secret_accepted():
    """Body signed with current_secret returns 'current'."""
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
    """Body signed with prev_secret returns 'prev' and logs a rotation warning."""
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
    assert any("PREV secret" in r.message for r in caplog.records)


def test_both_invalid_raises_401():
    """Body signed with an unrelated secret raises HTTPException(401)."""
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


def test_missing_signature_raises_401():
    """No signature header (while secrets are configured) raises HTTPException(401)."""
    with pytest.raises(HTTPException) as excinfo:
        validate_webhook_signature(b"body", None, "secret_current", "secret_prev")
    assert excinfo.value.status_code == 401


def test_empty_both_secrets_returns_dev_skip():
    """Empty current and prev secrets skip validation — local-dev behavior preserved."""
    assert validate_webhook_signature(b"body", None, "", "") == "dev-skip"


def test_deprecated_runpod_webhook_secret_alias_resolves():
    """Setting runpod_webhook_secret fills webhook_hmac_secret when the new one is empty.

    Phase 11 D-10 (amended 2026-04-24): the RUNPOD_WEBHOOK_SECRET env var is a
    deprecated alias retained for one release cycle so existing Railway
    Variables keep working. The Pydantic model_post_init hook resolves it.
    """
    from config import Settings

    s = Settings(runpod_webhook_secret="legacy", webhook_hmac_secret="")
    assert s.webhook_hmac_secret == "legacy"
