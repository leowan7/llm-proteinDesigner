"""API-key generation + verification (HMAC-SHA256 with a server-side pepper).

Phase 13, Plan 13-02 (RESEARCH §2.10). Mirrors the dual-secret rotation loop in
``backend/webhooks/router.py::validate_webhook_signature`` — the backend tries the
current pepper first, then ``api_key_pepper_prev`` during a rotation grace window.

Storage note: the ``api_keys.bcrypt_hash`` column name is retained for D-03
compatibility even though the value is an HMAC-SHA256 hex digest, NOT a bcrypt
hash. The 13-01 migration documents this in a ``COMMENT ON COLUMN``. Plaintext is
never stored; it is shown to the caller exactly once at creation time.

Fail-closed: ``verify_api_key`` returns ``False`` when both peppers are empty
strings, so a misconfigured deployment yields 401s rather than an auth bypass.
"""

import hashlib
import hmac
import logging
import secrets

from config import settings

logger = logging.getLogger(__name__)


def generate_api_key(env: str = "live") -> tuple[str, str, str]:
    """Mint a new API key.

    Args:
        env: "live" or "test" — drives the second token segment so keys from
             test fixtures are unambiguously distinguishable in logs.

    Returns:
        Three-tuple ``(plaintext, prefix, hash)``:
          plaintext - ``bw_<env>_<~32 urlsafe chars>`` — shown to the user ONCE
          prefix    - first 12 chars, stored in the DB for fast prefix lookup
          hash      - HMAC-SHA256 hex digest, peppered with settings.api_key_pepper
    """
    suffix = secrets.token_urlsafe(24)
    plaintext = f"bw_{env}_{suffix}"
    prefix = plaintext[:12]
    h = hmac.new(
        settings.api_key_pepper.encode(),
        plaintext.encode(),
        hashlib.sha256,
    ).hexdigest()
    return plaintext, prefix, h


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    """Constant-time verify against the current pepper, then the prev pepper.

    Dual-pepper rotation mirrors ``validate_webhook_signature``: during a
    rotation grace window, a key hashed with the previous pepper still verifies
    and a WARNING is logged so the operator knows old-pepper traffic persists.

    Returns ``False`` when both peppers are empty (fail-closed — no auth bypass
    on an unset pepper).
    """
    for label, secret in (
        ("current", settings.api_key_pepper),
        ("prev", settings.api_key_pepper_prev),
    ):
        if not secret:
            continue
        h = hmac.new(secret.encode(), plaintext.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(h, stored_hash):
            if label == "prev":
                logger.warning(
                    "API key verified with PREV pepper — rotation window active"
                )
            return True
    return False
