"""Stripe-style idempotency state machine backed by Postgres (RESEARCH §2.9).

Three-state lifecycle on ``public.api_key_idempotency``:
  pending   — a request claimed the (api_key_id, idempotency_key) slot and is
              still dispatching.
  completed — the response_status + response_body are persisted; replay them.

Router decision tree (RESEARCH §2.9 steps 1-5):
  1. ``existing = await try_begin(...)`` -> None -> dispatch + mark_complete.
  2. existing["status"] == "pending"                    -> 409 in-progress.
  3. existing["request_body_hash"] != hash_body(body)   -> 422 key-conflict.
  4. else (completed match)                             -> replay stored response.
"""

import hashlib
import json

import asyncpg


def canonicalize_body(body: dict) -> str:
    """Stable string for hashing. Sorted keys, no whitespace.

    Sorting keys means a reorder-but-same-content body does NOT trigger a 422.
    """
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def hash_body(body: dict) -> str:
    return hashlib.sha256(canonicalize_body(body).encode()).hexdigest()


async def try_begin(
    conn: asyncpg.Connection,
    api_key_id: str,
    idempotency_key: str,
    body_hash: str,
) -> dict | None:
    """Try to claim the idempotency slot.

    Returns ``None`` on success (caller proceeds to dispatch the job).
    Returns the existing row dict on conflict (caller routes to replay/409/422).
    """
    inserted = await conn.fetchrow(
        """INSERT INTO public.api_key_idempotency
               (api_key_id, idempotency_key, request_body_hash, status)
           VALUES ($1, $2, $3, 'pending')
           ON CONFLICT (api_key_id, idempotency_key) DO NOTHING
           RETURNING status""",
        api_key_id,
        idempotency_key,
        body_hash,
    )
    if inserted is not None:
        return None
    # Conflict — read existing row.
    return await conn.fetchrow(
        """SELECT status, request_body_hash, response_status, response_body
           FROM public.api_key_idempotency
           WHERE api_key_id = $1 AND idempotency_key = $2""",
        api_key_id,
        idempotency_key,
    )


async def mark_complete(
    conn: asyncpg.Connection,
    api_key_id: str,
    idempotency_key: str,
    response_status: int,
    response_body: dict,
) -> None:
    await conn.execute(
        """UPDATE public.api_key_idempotency
           SET status = 'completed',
               response_status = $3,
               response_body = $4::jsonb,
               completed_at = now()
           WHERE api_key_id = $1 AND idempotency_key = $2""",
        api_key_id,
        idempotency_key,
        response_status,
        json.dumps(response_body),
    )
