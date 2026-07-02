"""Typed API-key model (Phase 13, Plan 13-04).

Note: no ``plaintext`` field. The plaintext key is shown exactly once by the WEB
create flow (POST /user/api-keys); the SDK never receives it — the SDK only
lists (prefix + metadata) and revokes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiKey(BaseModel):
    """An API key as returned by GET /api/v1/api-keys (metadata only)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
