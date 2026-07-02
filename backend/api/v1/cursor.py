"""Opaque cursor codec for keyset pagination (Phase 13, RESEARCH §2.3).

The cursor is an unsigned URL-safe base64-encoded JSON object ``{"c": iso, "i": id}``.
NO HMAC signing — cursor tampering is a non-threat because every list query is
already org-scoped (a tampered cursor at worst pages to a wrong-but-still-org-scoped
position). ``decode_cursor`` NEVER raises; garbage input returns ``None`` and the
router turns that into a 400.
"""

import base64
import json
from datetime import datetime


def encode_cursor(created_at: datetime, id: str) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps({"c": created_at.isoformat(), "i": id}).encode()
        )
        .decode()
        .rstrip("=")
    )


def decode_cursor(token: str) -> tuple[datetime, str] | None:
    try:
        padded = token + "=" * (4 - len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(payload["c"]), payload["i"]
    except Exception:
        return None  # Treat unparseable cursor as no-cursor; 400 from router.
