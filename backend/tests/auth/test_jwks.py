"""Unit tests for ``auth.jwks.SupabaseJWKSVerifier``.

Phase 11 Plan 04 sub-plan: JWT migration HS256 -> ES256 (ECC P-256). These
tests do not require a running Supabase stack; they generate ephemeral ES256
key pairs with ``cryptography`` and mock the JWKS endpoint with ``respx``.

Coverage matrix (matches the table in 11-04-JWT-MIGRATION-RESEARCH.md §5):

  1.  Happy path ES256
  2.  Happy path HS256 fallback
  3.  Expired token -> ExpiredSignatureError
  4.  Wrong issuer -> InvalidIssuerError
  5.  Wrong audience -> InvalidAudienceError
  6.  Missing kid on a non-HS256 token -> InvalidTokenError
  7.  JWKS cache hit avoids second HTTP call
  8.  JWKS cache miss with refresh-on-unknown-kid (success path)
  9.  JWKS endpoint unreachable -> PyJWTError (mapped to 401 upstream)
  10. Key rotation mid-request (kid present after refresh)
  11. Algorithm-confusion attempt rejected
  12. HS256 fallback disabled when SUPABASE_JWT_SECRET unset

The tests bypass ``conftest.client`` (which spins up the FastAPI ASGI app and
loads .env.local). They construct a fresh ``SupabaseJWKSVerifier`` per test
to avoid singleton state leaking across cases.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import patch

import httpx
import jwt
import pytest
import respx
from auth.jwks import SupabaseJWKSVerifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

JWKS_URL = "https://test.supabase.co/auth/v1/.well-known/jwks.json"
ISSUER = "https://test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
USER_SUB = "11111111-2222-3333-4444-555555555555"
HS256_SECRET = "legacy-hs256-secret-from-supabase-status"


# ---------------------------------------------------------------------------
# Helpers — ES256 key generation, JWK serialization, token minting
# ---------------------------------------------------------------------------


def _make_es256_keypair(kid: str) -> tuple[Any, dict[str, Any]]:
    """Generate a P-256 keypair and return (private_key_pem_bytes, public_jwk_dict)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Use PyJWT's own ECAlgorithm.to_jwk to render the public-key JWK so the
    # JWK shape matches exactly what PyJWT will accept on the verify side.
    public_jwk_str = ECAlgorithm.to_jwk(private_key.public_key())
    public_jwk = json.loads(public_jwk_str)
    public_jwk["kid"] = kid
    public_jwk["alg"] = "ES256"
    public_jwk["use"] = "sig"
    return private_pem, public_jwk


def _mint_es256_token(
    private_pem: bytes,
    kid: str,
    *,
    sub: str = USER_SUB,
    aud: str = AUDIENCE,
    iss: str = ISSUER,
    exp_offset_seconds: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_offset_seconds,
        "role": "authenticated",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        private_pem,
        algorithm="ES256",
        headers={"kid": kid},
    )


def _mint_hs256_token(
    secret: str,
    *,
    sub: str = USER_SUB,
    aud: str = AUDIENCE,
    iss: str = ISSUER,
    exp_offset_seconds: int = 3600,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_offset_seconds,
        "role": "authenticated",
    }
    # No kid header — Supabase HS256 tokens carry only ``alg`` in the header.
    return jwt.encode(payload, secret, algorithm="HS256")


def _jwks_payload(*public_jwks: dict[str, Any]) -> dict[str, Any]:
    return {"keys": list(public_jwks)}


def _make_verifier() -> SupabaseJWKSVerifier:
    """Fresh verifier instance per test — avoids singleton state leakage."""
    return SupabaseJWKSVerifier(jwks_url=JWKS_URL)


# ---------------------------------------------------------------------------
# Tests — happy paths
# ---------------------------------------------------------------------------


class TestHappyPaths:
    """1, 2: ES256 and HS256 happy paths."""

    @pytest.mark.anyio
    @respx.mock
    async def test_es256_happy_path(self):
        """ES256-signed token with matching kid in JWKS verifies to sub claim."""
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )
        token = _mint_es256_token(private_pem, kid)

        verifier = _make_verifier()
        payload = await verifier.verify(token, issuer=ISSUER)

        assert payload["sub"] == USER_SUB
        assert payload["aud"] == AUDIENCE
        assert payload["iss"] == ISSUER

    @pytest.mark.anyio
    @respx.mock
    async def test_hs256_fallback_happy_path(self):
        """HS256-signed token verifies against ``SUPABASE_JWT_SECRET`` when set.

        No JWKS HTTP call should happen — the HS256 branch is keyed off the
        token header alone. ``respx.mock`` rejects any unmocked request so an
        accidental fetch would surface as a test failure.
        """
        token = _mint_hs256_token(HS256_SECRET)

        verifier = _make_verifier()
        with patch("auth.jwks.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = HS256_SECRET
            mock_settings.supabase_url = "https://test.supabase.co"
            payload = await verifier.verify(token, issuer=ISSUER)

        assert payload["sub"] == USER_SUB


# ---------------------------------------------------------------------------
# Tests — invalid claim shapes
# ---------------------------------------------------------------------------


class TestInvalidClaims:
    """3, 4, 5: expired, wrong issuer, wrong audience."""

    @pytest.mark.anyio
    @respx.mock
    async def test_expired_token_raises(self):
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )
        token = _mint_es256_token(private_pem, kid, exp_offset_seconds=-60)

        verifier = _make_verifier()
        with pytest.raises(jwt.ExpiredSignatureError):
            await verifier.verify(token, issuer=ISSUER)

    @pytest.mark.anyio
    @respx.mock
    async def test_wrong_issuer_raises(self):
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )
        token = _mint_es256_token(
            private_pem, kid, iss="https://attacker.supabase.co/auth/v1"
        )

        verifier = _make_verifier()
        with pytest.raises(jwt.InvalidIssuerError):
            await verifier.verify(token, issuer=ISSUER)

    @pytest.mark.anyio
    @respx.mock
    async def test_wrong_audience_raises(self):
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )
        token = _mint_es256_token(private_pem, kid, aud="anon")

        verifier = _make_verifier()
        with pytest.raises(jwt.InvalidAudienceError):
            await verifier.verify(token, issuer=ISSUER)


# ---------------------------------------------------------------------------
# Tests — JWKS cache behavior
# ---------------------------------------------------------------------------


class TestJWKSCache:
    """6, 7, 8, 10: missing kid, cache hit, refresh-on-unknown-kid, key rotation."""

    @pytest.mark.anyio
    @respx.mock
    async def test_missing_kid_in_header_is_rejected(self):
        """A token with ``alg=ES256`` but no kid header fails closed.

        Manually construct the token with a missing kid — PyJWT does not put a
        kid on the header by default unless we ask it to.
        """
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )
        # Mint a token without supplying ``headers={"kid": ...}``.
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": USER_SUB,
                "aud": AUDIENCE,
                "iss": ISSUER,
                "iat": now,
                "exp": now + 3600,
            },
            private_pem,
            algorithm="ES256",
        )

        verifier = _make_verifier()
        with pytest.raises(jwt.InvalidTokenError):
            await verifier.verify(token, issuer=ISSUER)

    @pytest.mark.anyio
    @respx.mock
    async def test_cache_hit_avoids_second_http_call(self):
        """Two verifies with the same kid trigger exactly one JWKS GET."""
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        route = respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )

        verifier = _make_verifier()
        token = _mint_es256_token(private_pem, kid)

        await verifier.verify(token, issuer=ISSUER)
        await verifier.verify(token, issuer=ISSUER)

        assert route.call_count == 1

    @pytest.mark.anyio
    @respx.mock
    async def test_unknown_kid_after_refresh_fails_closed(self):
        """A token referencing a kid not in the JWKS errors out after one refresh."""
        kid = "key-1"
        _, public_jwk = _make_es256_keypair(kid)
        route = respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )

        # Mint a token with a *different* kid — even though we have a key in
        # the JWKS, the kid pointer doesn't match anything.
        rogue_private_pem, _ = _make_es256_keypair("garbage-kid")
        rogue_token = _mint_es256_token(rogue_private_pem, "garbage-kid")

        verifier = _make_verifier()
        with pytest.raises(jwt.InvalidTokenError) as exc_info:
            await verifier.verify(rogue_token, issuer=ISSUER)
        assert "garbage-kid" in str(exc_info.value)
        # Exactly one refresh attempt — no retry storm.
        assert route.call_count == 1

    @pytest.mark.anyio
    @respx.mock
    async def test_key_rotation_mid_request_triggers_refresh(self):
        """A new kid (rotation) triggers a second GET, then the new key verifies."""
        old_kid = "key-old"
        new_kid = "key-new"
        old_private_pem, old_public_jwk = _make_es256_keypair(old_kid)
        new_private_pem, new_public_jwk = _make_es256_keypair(new_kid)

        # First call returns only the old key; second call returns both.
        route = respx.get(JWKS_URL).mock(
            side_effect=[
                httpx.Response(200, json=_jwks_payload(old_public_jwk)),
                httpx.Response(
                    200, json=_jwks_payload(old_public_jwk, new_public_jwk)
                ),
            ]
        )

        verifier = _make_verifier()
        # Verify with the old key — populates cache from the first response.
        old_token = _mint_es256_token(old_private_pem, old_kid)
        await verifier.verify(old_token, issuer=ISSUER)
        assert route.call_count == 1

        # New token signed with the rotated key — kid miss, triggers refresh.
        new_token = _mint_es256_token(new_private_pem, new_kid)
        payload = await verifier.verify(new_token, issuer=ISSUER)
        assert payload["sub"] == USER_SUB
        assert route.call_count == 2


# ---------------------------------------------------------------------------
# Tests — operational failure modes
# ---------------------------------------------------------------------------


class TestNetworkAndOperational:
    """9, 12: JWKS endpoint unreachable, fallback disabled when env var unset."""

    @pytest.mark.anyio
    @respx.mock
    async def test_jwks_endpoint_503_raises_pyjwt_error(self):
        """A 5xx from JWKS surfaces as ``PyJWTError`` (-> 401 upstream)."""
        respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
        kid = "key-1"
        private_pem, _ = _make_es256_keypair(kid)
        token = _mint_es256_token(private_pem, kid)

        verifier = _make_verifier()
        with pytest.raises(jwt.PyJWTError):
            await verifier.verify(token, issuer=ISSUER)

    @pytest.mark.anyio
    @respx.mock
    async def test_hs256_fallback_disabled_when_env_var_unset(self):
        """Without ``SUPABASE_JWT_SECRET`` set, HS256-claiming tokens are rejected.

        This is the operator-driven kill switch: dropping the env var post-deploy
        retires the legacy fallback path.
        """
        token = _mint_hs256_token(HS256_SECRET)

        verifier = _make_verifier()
        with patch("auth.jwks.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = ""
            mock_settings.supabase_url = "https://test.supabase.co"
            with pytest.raises(jwt.InvalidTokenError):
                await verifier.verify(token, issuer=ISSUER)


# ---------------------------------------------------------------------------
# Tests — algorithm confusion (CVE-2017-11424 family)
# ---------------------------------------------------------------------------


class TestAlgorithmConfusion:
    """11: Algorithm-confusion attempts must be rejected.

    The classic attack: take an asymmetric public key, use its raw bytes as
    the HMAC secret, and forge a token with ``{"alg":"HS256"}`` that the
    server's "permissive" decode will accept. The mitigation is to pin the
    algorithm per code path: ES256 branch passes ``algorithms=["ES256"]``
    only, HS256 branch passes ``algorithms=["HS256"]`` only — and never
    combine them in one ``jwt.decode`` call.
    """

    @pytest.mark.anyio
    @respx.mock
    async def test_alg_none_is_rejected(self):
        """A token with ``alg=none`` must fail closed."""
        kid = "key-1"
        _, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )
        # PyJWT refuses to encode alg=none unless explicitly told the key is
        # empty; build the token by hand.
        import base64

        def _b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        header = _b64(json.dumps({"alg": "none", "kid": kid}).encode())
        payload = _b64(
            json.dumps(
                {
                    "sub": USER_SUB,
                    "aud": AUDIENCE,
                    "iss": ISSUER,
                    "exp": int(time.time()) + 3600,
                }
            ).encode()
        )
        token = f"{header}.{payload}."

        verifier = _make_verifier()
        # alg=none is neither HS256 nor ES256 from our code's POV. The kid is
        # in the JWKS so we proceed to the ES256 branch, which will raise
        # InvalidAlgorithmError because the token's alg disagrees.
        with pytest.raises(jwt.PyJWTError):
            await verifier.verify(token, issuer=ISSUER)

    @pytest.mark.anyio
    @respx.mock
    async def test_hs256_signed_with_jwks_public_key_rejected(self):
        """Forge a token with ``alg=HS256`` using the ES256 public-key bytes
        as the HMAC secret. The HS256 branch verifies against
        ``settings.supabase_jwt_secret`` only — never the JWKS public key —
        so the forgery must fail closed.

        Note: PyJWT 2.12.1 also refuses to encode HS256 with PEM-formatted key
        material at the algorithm layer (defense in depth). To make the attack
        token at all we use the raw uncompressed EC point bytes — the most
        permissive attacker shape that the encoder will still accept.
        """
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )

        # Use the uncompressed-point representation of the EC public key
        # (the same bytes a JWK ``x``/``y`` pair concatenates to). Bypasses
        # PyJWT's PEM-rejection guard at the encoder.
        public_key = ECAlgorithm.from_jwk(public_jwk)
        public_point_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )

        now = int(time.time())
        forged_token = jwt.encode(
            {
                "sub": "attacker",
                "aud": AUDIENCE,
                "iss": ISSUER,
                "exp": now + 3600,
            },
            public_point_bytes,  # EC point bytes used as HMAC secret — the attack.
            algorithm="HS256",
        )

        verifier = _make_verifier()
        with patch("auth.jwks.settings") as mock_settings:
            # Even with the legacy secret set, the forgery must fail because
            # the secret is the real Supabase HS256 secret, NOT the public
            # key. Verifying against ``HS256_SECRET`` rejects the signature.
            mock_settings.supabase_jwt_secret = HS256_SECRET
            mock_settings.supabase_url = "https://test.supabase.co"
            with pytest.raises(jwt.InvalidSignatureError):
                await verifier.verify(forged_token, issuer=ISSUER)


# ---------------------------------------------------------------------------
# Tests — concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Bonus: asyncio.Lock coalesces concurrent unseen-kid refreshes."""

    @pytest.mark.anyio
    @respx.mock
    async def test_concurrent_refreshes_coalesce(self):
        """N concurrent verifies on a cold cache trigger exactly one HTTP call."""
        kid = "key-1"
        private_pem, public_jwk = _make_es256_keypair(kid)
        route = respx.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json=_jwks_payload(public_jwk))
        )

        verifier = _make_verifier()
        token = _mint_es256_token(private_pem, kid)

        results = await asyncio.gather(
            *(verifier.verify(token, issuer=ISSUER) for _ in range(50))
        )

        assert all(r["sub"] == USER_SUB for r in results)
        assert route.call_count == 1
