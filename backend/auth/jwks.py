"""JWKS verifier for Supabase ES256 (ECC P-256) signed access tokens.

Phase 11 Plan 04 (sub-plan 11-04-JWT-MIGRATION-RESEARCH.md): Supabase auto-migrated
both kendrew-prod and kendrew-staging from HS256 (legacy shared secret) to ES256
(asymmetric ECC P-256). End-user session JWTs now carry a ``kid`` header pointing
at the active signing key in the project's JWKS. This module owns:

  * Async-native JWKS fetching via ``httpx.AsyncClient`` (PyJWT's bundled
    ``PyJWKClient`` is sync-only and would block the event loop).
  * In-process TTL cache (default 300 s) with explicit invalidate-on-``kid``-miss;
    sidesteps PyJWT issue #1051 where the LRU cache serves revoked keys.
  * ``asyncio.Lock`` coalescing so a swarm of concurrent unseen-``kid`` verifies
    triggers exactly one upstream JWKS GET.
  * Strict per-code-path algorithm pinning. ES256 verification passes
    ``algorithms=["ES256"]`` only; HS256 fallback passes ``algorithms=["HS256"]``
    only. The two paths are NEVER combined in one ``jwt.decode`` call —
    combining them is the algorithm-confusion attack (CVE-2017-11424 family).
  * Operator-driven HS256 retirement: the legacy fallback is alive only while
    ``settings.supabase_jwt_secret`` is set. Drop the env var post-deploy
    verification to retire the legacy path; no calendar flag.

References
    Supabase signing keys docs: https://supabase.com/docs/guides/auth/signing-keys
    PyJWT issue #615: https://github.com/jpadilla/pyjwt/issues/615
    PyJWT issue #1051: https://github.com/jpadilla/pyjwt/issues/1051
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import ECAlgorithm

from config import settings


logger = logging.getLogger(__name__)


# Five-minute in-process TTL on JWKS responses. Bounds the staleness window
# after a Supabase key rotation; the verifier also force-refreshes whenever a
# token's ``kid`` is not in the current cache.
_JWKS_CACHE_TTL_SECONDS = 300

# Conservative HTTP timeout. Supabase JWKS is small JSON and lives at the edge.
_JWKS_HTTP_TIMEOUT_SECONDS = 5.0


class SupabaseJWKSVerifier:
    """Dual-algorithm verifier for Supabase access tokens.

    Order of operations on ``verify``:

      1. Inspect the token's unverified header.
      2. If ``alg == "HS256"``: fall through to the HS256 branch when
         ``settings.supabase_jwt_secret`` is set; otherwise fail closed.
      3. Otherwise (ES256 path): look up the token's ``kid`` in the cached
         JWKS. On cache miss, force one refresh and retry. Decode with
         ``algorithms=["ES256"]`` and the matching public key.

    Audience defaults to ``"authenticated"`` (the value Supabase puts on
    end-user session JWTs); callers may override per-call. Issuer is the
    Supabase auth root derived from ``settings.supabase_url``.

    On any failure the verifier raises ``jwt.PyJWTError`` (or one of its
    subclasses) so the existing ``get_current_user`` exception ladder still
    maps it to HTTP 401 without leaking implementation details.
    """

    def __init__(
        self,
        jwks_url: str | None = None,
        cache_ttl_seconds: int = _JWKS_CACHE_TTL_SECONDS,
        http_timeout_seconds: float = _JWKS_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._jwks_url = jwks_url or self._derive_jwks_url()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http_timeout_seconds = http_timeout_seconds

        # Cache of ``kid`` -> PyJWK-style key dict. Populated by ``_refresh``.
        # Empty dict (not None) until first successful fetch.
        self._jwks_keys: dict[str, dict[str, Any]] = {}
        self._jwks_fetched_at: float = 0.0

        # Coalesces concurrent refreshes so a thundering-herd of unseen-kid
        # verifies triggers exactly one upstream GET per cache window.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def verify(
        self,
        token: str,
        *,
        audience: str = "authenticated",
        issuer: str | None = None,
    ) -> dict[str, Any]:
        """Verify a Supabase access token and return the decoded payload.

        Args:
            token: The encoded JWT (the cookie value).
            audience: Required ``aud`` claim. Supabase end-user tokens use
                ``"authenticated"``.
            issuer: Required ``iss`` claim. Defaults to
                ``f"{settings.supabase_url}/auth/v1"``.

        Returns:
            The decoded claims dict.

        Raises:
            jwt.PyJWTError: On any verification failure. Callers map this to
                an HTTP 401.
        """
        if issuer is None:
            issuer = self._derive_issuer()

        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise

        alg = unverified_header.get("alg")

        # HS256 branch — strict opt-in via legacy env var. NEVER falls through
        # from a failed ES256 verify; only triggers when the token itself
        # claims HS256 in its header. This preserves the algorithm-confusion
        # boundary: an attacker presenting an ES256-shaped token cannot trick
        # the verifier into treating its public-key bytes as an HMAC secret.
        if alg == "HS256":
            return self._verify_hs256(token, audience=audience, issuer=issuer)

        # ES256 (or any future asymmetric alg) — JWKS path.
        kid = unverified_header.get("kid")
        if not kid:
            # Tokens issued by current Supabase always carry a kid. Missing
            # kid on a non-HS256 token is either a malformed token or a
            # downgrade attempt. Fail closed.
            raise jwt.InvalidTokenError("Missing kid in token header")

        signing_key = await self._get_signing_key(kid)
        return jwt.decode(
            token,
            signing_key,
            algorithms=["ES256"],
            audience=audience,
            issuer=issuer,
        )

    async def refresh(self) -> None:
        """Force-fetch JWKS now (e.g. for startup warmup).

        Errors propagate as ``jwt.PyJWTError`` so callers can choose between
        fail-fast and log-and-continue. Used by tests and (optionally) by an
        application-startup hook.
        """
        await self._refresh()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_jwks_url() -> str:
        """Default JWKS URL derived from the configured Supabase project URL."""
        base = settings.supabase_url.rstrip("/")
        return f"{base}/auth/v1/.well-known/jwks.json"

    @staticmethod
    def _derive_issuer() -> str:
        """Default issuer is the project's auth root (matches Supabase JWT iss)."""
        base = settings.supabase_url.rstrip("/")
        return f"{base}/auth/v1"

    def _verify_hs256(
        self,
        token: str,
        *,
        audience: str,
        issuer: str,
    ) -> dict[str, Any]:
        """Verify a legacy HS256 token against ``settings.supabase_jwt_secret``.

        Operator-driven kill switch: when ``supabase_jwt_secret`` is empty, the
        fallback is disabled and any HS256-claiming token is rejected.
        Algorithm pinned to ``["HS256"]`` only — never combined with ES256.
        """
        secret = settings.supabase_jwt_secret
        if not secret:
            # Kill switch: HS256 fallback retired by the operator.
            raise jwt.InvalidTokenError(
                "HS256 fallback disabled (SUPABASE_JWT_SECRET not set)"
            )
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=issuer,
        )

    async def _get_signing_key(self, kid: str) -> Any:
        """Return the ES256 public key for ``kid``, refreshing once on miss.

        Strategy:
          1. If kid is in the cache and the cache is fresh enough, use it.
          2. Otherwise, take the lock and refresh once.
          3. After refresh, look up kid again. If still missing, fail closed
             (no second refresh — prevents retry storms on a garbage kid).
        """
        cached_key = self._lookup_cached_key(kid)
        if cached_key is not None:
            return cached_key

        async with self._lock:
            # Double-check inside the lock — another coroutine may have
            # refreshed while we were waiting.
            cached_key = self._lookup_cached_key(kid)
            if cached_key is not None:
                return cached_key
            await self._refresh()

        cached_key = self._lookup_cached_key(kid, ignore_ttl=True)
        if cached_key is None:
            raise jwt.InvalidTokenError(
                f"No JWKS key matches token kid={kid!r}"
            )
        return cached_key

    def _lookup_cached_key(
        self,
        kid: str,
        *,
        ignore_ttl: bool = False,
    ) -> Any | None:
        """Return the cached PyJWK key for ``kid`` if present and fresh."""
        if not self._jwks_keys:
            return None
        if not ignore_ttl:
            age = time.monotonic() - self._jwks_fetched_at
            if age > self._cache_ttl_seconds:
                return None
        jwk = self._jwks_keys.get(kid)
        if jwk is None:
            return None
        # PyJWT exposes ECAlgorithm.from_jwk to materialize the cryptography
        # public key from the JWK dict. Doing this on every verify is cheap
        # (JWK -> EC public key construction is deterministic and small).
        return ECAlgorithm.from_jwk(jwk)

    async def _refresh(self) -> None:
        """Fetch JWKS over HTTPS and replace the cache atomically.

        On failure leaves the existing cache intact (so an unrelated network
        blip during the TTL window does not invalidate previously-good keys)
        and raises ``jwt.PyJWTError``.
        """
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_seconds) as client:
                response = await client.get(self._jwks_url)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("JWKS fetch failed for %s: %s", self._jwks_url, exc)
            raise jwt.PyJWTError(f"JWKS endpoint unreachable: {exc}") from exc
        except ValueError as exc:  # JSON decode error
            logger.warning("JWKS response was not valid JSON: %s", exc)
            raise jwt.PyJWTError(f"JWKS payload invalid: {exc}") from exc

        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise jwt.PyJWTError("JWKS payload missing 'keys' array")

        # Build a kid -> jwk dict. Ignore entries without a kid (a Supabase
        # signing key always has one; entries without one are not
        # addressable here).
        new_keys: dict[str, dict[str, Any]] = {}
        for jwk in keys:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            if isinstance(kid, str) and kid:
                new_keys[kid] = jwk

        self._jwks_keys = new_keys
        self._jwks_fetched_at = time.monotonic()


# Module-level singleton. Imported by ``auth.dependencies`` and
# ``auth.router.exchange_token`` so JWKS state is shared across all auth
# verification paths in the process.
jwks_verifier = SupabaseJWKSVerifier()
