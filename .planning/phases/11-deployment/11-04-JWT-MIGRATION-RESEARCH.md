---
phase: 11
plan: 04 (sub-plan)
type: research
status: ready-for-review
authored: 2026-04-29
amends: 11-04-PLAN.md
---

# JWT Verification Migration: HS256 → ES256 (ECC P-256)

## 1. Current State Summary

### Files involved

| File | Role |
|---|---|
| `backend/auth/dependencies.py` | `get_current_user` — the single FastAPI dependency gating every authenticated route. Tries HS256, falls back to `PyJWKClient` ES256. |
| `backend/auth/router.py` | `/auth/exchange-token` decodes recovery JWT HS256 only (line 242). `/auth/me` decodes without verify (line 306) to read `email`. `/auth/login`, `/auth/refresh`, `/auth/signup` delegate signing to Supabase via `supabase-py`. |
| `backend/middleware/rate_limit.py` | `get_rate_limit_key` decodes with `verify_signature=False` for the rate-limit bucket. Crypto-irrelevant. |
| `backend/middleware/logging.py` | `_extract_user_id` decodes with `verify_signature=False` for access logs. Crypto-irrelevant. |
| `backend/admin/dependencies.py` | `get_current_admin` chains off `get_current_user`; no direct decode. |
| `backend/config.py` | Holds `supabase_url`, `supabase_jwt_secret`. JWKS URL derives from `supabase_url`. |
| `backend/auth/admin_client.py` | Uses service-role key as long-lived API token; no end-user verification. Service-role tokens remain HS256 against the legacy secret per Supabase dashboard ("legacy JWT secret … is used to only verify JSON Web Tokens by Supabase products. This includes the anon and service_role JWT based API keys"). Untouched. |
| `backend/requirements.txt` | `PyJWT==2.12.1` ships `PyJWKClient`; `cryptography` is transitive. No new dependency. |

### Auth flow (in prose)

1. Browser hits `/auth/login` → backend `supabase-py` (anon key) calls Supabase Auth → Supabase returns a session containing an access JWT signed with the project's active signing key (now ES256/P-256).
2. Backend writes `access_token`/`refresh_token` as HTTP-only cookies. Frontend never sees Supabase directly.
3. Every subsequent authenticated request flows through `Depends(get_current_user)` — the only verification bottleneck. ~20 routers reach this dependency (jobs, sessions, agent, billing, user, admin, pdb).
4. `/auth/refresh` rotates the cookie via `supabase-py.refresh_session`; no local crypto.
5. `/auth/exchange-token` (password reset) is the **second** verifying surface, currently HS256-only.

### Bottleneck for the change

Two functions own all verification: `auth/dependencies.py::get_current_user` and `auth/router.py::exchange_token`. Everything else is crypto-free (anon-key API calls, decode-without-verify for telemetry) or transitively gated by `get_current_user`. Correctly changing those two is the entire migration.

### What is already partially in place

`get_current_user` already attempts a JWKS fallback. Confirmed by direct fetch on 2026-04-29, the prod JWKS returns `{"alg":"ES256","crv":"P-256","kid":"acb1a33c-...","kty":"EC"}` — the dashboard's "ECC P-256" maps to JWA `ES256`. Three defects in the current code:

1. **No JWKS caching configured.** `PyJWKClient(jwks_url)` is instantiated on every cache-miss path; the in-process cache resets each call, so every authenticated request will hit `/auth/v1/.well-known/jwks.json` once. ([PyJWT #615](https://github.com/jpadilla/pyjwt/issues/615))
2. **`PyJWKClient` is sync-only.** Internally uses `urllib.request.urlopen`; blocks the event loop inside the async dependency on every miss.
3. **Exception ladder is brittle.** `try/except (InvalidAlgorithmError, InvalidSignatureError)` silently masks any future algorithm change (e.g. RS256) as a 401.

Tolerable until now because no real ES256 token has hit production. Phase 11 deploy makes them load-bearing.

## 2. Three Options Ranked

### Option A — JWKS migration (recommended)

Replace the brittle HS256-first / ES256-fallback ladder with a single JWKS-based verifier supporting **both** algorithms during the Supabase rotation grace window. The `kid` header chooses the key path: tokens with a `kid` in the JWKS verify ES256; tokens without `kid` (header `{"alg":"HS256"}`) verify against `settings.supabase_jwt_secret`. After Supabase revokes the legacy key, drop the HS256 branch. This is Supabase's recommended pattern — their docs advise downstream services be configured with `JWT_JWKS` to verify both legacy HS256 and new ES256 tokens. ([Supabase signing-keys docs](https://supabase.com/docs/guides/auth/signing-keys))

### Option B — Adopt `sb_publishable_*` / `sb_secret_*` API keys

The new key system is a **separate** mechanism for API-gateway authorization, not end-user session verification. Per [Supabase API keys docs](https://supabase.com/docs/guides/api/api-keys): "Using a publishable key does not mean that your user is anonymous. You can authenticate your application with the publishable key, while your user is authenticated (via Supabase Auth) with their personal JWT." End-user session JWTs are signed with the asymmetric signing key regardless of which API key the client uses. Adopting publishable/secret keys does **not** eliminate the need for JWKS verification — it is additive work (swap `SUPABASE_ANON_KEY` for `SUPABASE_PUBLISHABLE_KEY` and `SUPABASE_SERVICE_ROLE_KEY` for `SUPABASE_SECRET_KEY`). Useful, but does not solve the Phase 11 blocker.

### Option C — Revert to HS256-only

Per [Supabase rotation docs](https://supabase.com/docs/guides/auth/signing-keys), a revoked key can be moved back to standby and re-promoted. In principle: create a new symmetric key, mark it active, revoke the ES256 key. But this undoes a security-positive platform default, swims against the 2025-05-01 default-asymmetric direction, and a future Supabase upgrade may force the migration anyway. Rejected.

### Ranking

1. **Option A (JWKS migration)** — small, surgical, durable, no infra dependency.
2. **Option B (new API keys)** — orthogonal; defer to post-launch backlog.
3. **Option C (unrotate)** — debt-creating; rejected.

## 3. Recommended Option with Rationale

**Adopt Option A.** Reasons:

- Two files do the heavy lifting (`auth/dependencies.py` + `auth/router.py::exchange_token`). PR diff is under 200 lines including tests.
- PyJWT 2.12.1 is already pinned; `cryptography` is transitively present via `supabase-py`. No new dependency.
- The dual-algorithm branch is **time-bounded**: once the legacy HS256 key is revoked, the HS256 branch becomes dead code to drop in the next release.
- 5-minute in-process JWKS TTL bounds the impact of any cache staleness.
- Frontend is unaffected; all Supabase auth calls route through the backend.

`pyjwt-key-fetcher` (async-native alternative) was considered and rejected — adding a dependency for what is a 30-line helper around `httpx.AsyncClient` with an `asyncio.Lock`.

## 4. File-by-File Change List

### Code changes

| File | Change |
|---|---|
| `backend/config.py` | Add `supabase_jwks_url: str = ""` field with a default derived from `supabase_url` in `model_post_init` (`f"{self.supabase_url}/auth/v1/.well-known/jwks.json"`). Keep `supabase_jwt_secret` (legacy HS256) — still required during the transition window. Add a comment marking it deprecated and pointing at this file. |
| `backend/auth/jwks.py` *(new)* | New module. Single class `SupabaseJWKSVerifier` exposing `async verify(token: str) -> dict[str, Any]`. Internals: in-process JWKS cache with TTL (default 300 s); single `asyncio.Lock` to coalesce cache-miss refresh; `kid` lookup; on `kid` miss, force one refresh and retry once before erroring; falls through to HS256 verification when the token header is `{"alg":"HS256"}` AND `settings.supabase_jwt_secret` is set; uses `httpx.AsyncClient` (already in requirements via tests + Supabase HTTP). Returns the decoded payload. Raises `jwt.PyJWTError` on any failure so the existing `get_current_user` exception ladder still maps to HTTP 401. |
| `backend/auth/dependencies.py` | Replace the inline try/except ladder with `await jwks_verifier.verify(access_token)`. Module-level singleton `jwks_verifier` instantiated once, audience validation `audience="authenticated"` and issuer validation `issuer=f"{settings.supabase_url}/auth/v1"` (per [Supabase JWT docs](https://supabase.com/docs/guides/auth/jwts)) preserved. Same `HTTPException` mapping. |
| `backend/auth/router.py` | `exchange_token` (line 242) now delegates to the same verifier instead of `jwt.decode(..., HS256)`. `/auth/me` decode-without-verify is unchanged (verification already happened in `get_current_user` upstream). |
| `backend/main.py` | Wire JWKS verifier startup-warmup in the existing `lifespan`: one `await jwks_verifier.refresh()` to fail-fast if the JWKS endpoint is unreachable at boot. Optional but cheap. |

### Test changes

| File | Change |
|---|---|
| `backend/tests/auth/test_jwks.py` *(new)* | Unit tests for `SupabaseJWKSVerifier` — happy path ES256, happy path HS256 fallback, expired token, wrong audience, wrong issuer, kid miss triggers refresh, kid miss after refresh fails closed, JWKS endpoint 5xx returns auth error not 200, cache hit avoids second network call. Use `respx` (already pinned 0.22.0) to mock JWKS HTTP. Generate ES256 test keys with `cryptography.hazmat.primitives.asymmetric.ec`. |
| `backend/tests/test_auth.py` | Existing tests assume real Supabase local stack (HS256 secret from `supabase status`). Add an env override or fixture that injects a mock JWKS verifier so tests can run with both an ES256 and HS256 token shape without spinning up Supabase. Mark current real-Supabase tests `@pytest.mark.integration` and gate them behind `SUPABASE_TESTING_MODE=local`. |
| `backend/tests/middleware/test_rate_limit.py` | No verification logic depends on signature; tests already use `verify_signature=False` semantics. **No change needed**. |
| `backend/tests/middleware/test_logging.py` | Same — no change needed. |
| `backend/tests/auth/test_signup_tos.py` | Uses `_fake_supabase_success` mock; backend never calls `jwt.decode` on the signup flow. **No change needed**. |
| `backend/tests/conftest.py` | Add a `mock_jwks_verifier` fixture and a helper to mint signed-with-test-key ES256 tokens for use across auth integration tests. |

### Configuration

| File | Change |
|---|---|
| `.env.example` | Add `SUPABASE_JWKS_URL=` with the comment `# Optional — derived from SUPABASE_URL when empty. Set explicitly to override (e.g. self-hosted Supabase).` Keep `SUPABASE_JWT_SECRET` with an updated comment: `# Legacy HS256 secret — accepted during dual-key window, remove after Supabase signing key revocation.` |

### Documentation

| File | Change |
|---|---|
| `docs/deploy.md` | Append a "JWT signing key rotation runbook" section: how to confirm Supabase has rotated, how to verify the JWKS endpoint, the operator command to drop `SUPABASE_JWT_SECRET` from Railway after revocation. Cross-reference [Supabase rotation guide](https://supabase.com/docs/guides/auth/signing-keys). |
| `.planning/phases/11-deployment/11-CONTEXT.md` | No change — D-03 already references this plan. |
| `.planning/phases/11-deployment/11-04-PLAN.md` | Append a note pointing at this sub-plan; do not edit existing tasks. |

## 5. Test Strategy

### Existing tests that need updating

- `backend/tests/test_auth.py` (`test_login_then_me`, `test_me_without_cookie`, `test_logout_clears_cookies`) exercise the live Supabase local stack (HS256). They will continue to work. Add **mirrored** tests using `mock_jwks_verifier` so the asymmetric path is exercised even when the dev stack is HS256. Mark live-Supabase variants `@pytest.mark.integration`.

### New tests required

All in `tests/auth/test_jwks.py`:

| Scenario | Assertion |
|---|---|
| Happy path ES256 | Token signed with mock private key + matching `kid` verifies, payload has `sub` |
| Expired token | `exp` in past → `ExpiredSignatureError` → 401 |
| Wrong audience | `aud="anon"` → `InvalidAudienceError` → 401 |
| Wrong issuer | `iss="https://other.supabase.co/auth/v1"` → `InvalidIssuerError` → 401 |
| Cache hit | Two verifies, same `kid` → one HTTP call (respx assertion) |
| New `kid` triggers refresh | New `kid` → second HTTP call; same `kid` again → no third call |
| Unknown `kid` after refresh | `kid=garbage` → 401, only one refresh attempted (no retry storm) |
| JWKS endpoint 503 | 503 → request returns 401 (not 500), Sentry breadcrumb |
| Algorithm-confusion | `{"alg":"none"}` rejected; `{"alg":"HS256"}` signed with JWKS public key rejected |
| Dual-key HS256 | `{"alg":"HS256"}` signed with `supabase_jwt_secret` verifies during transition |
| HS256 after secret unset | Same token with `supabase_jwt_secret=""` → 401 |
| Concurrent refresh | 50 concurrent verifies on unseen `kid` → exactly one HTTP call (`asyncio.Lock` coalescing) |

### Coverage expectations

The new `auth/jwks.py` module should have ≥95% line coverage. Existing `auth/dependencies.py` coverage should not regress. The new tests do not require Supabase running.

## 6. Rollback Plan

Three layers in order of severity:

1. **In-app (instant).** If the verifier misbehaves but deploy is live, ensure Railway env `SUPABASE_JWT_SECRET` is set to the legacy secret from `11-02-PROVISIONING.md`. The HS256 fallback accepts tokens Supabase still signs with the legacy secret. Critical: Supabase did **not** auto-revoke the legacy key on auto-migration — only rotated to a new active asymmetric key. The legacy secret remains a valid verifier until explicitly revoked. This rollback path is real for the entire transition window.

2. **Code rollback (one git revert).** Railway redeploys the previous immutable image from deploy history.

3. **Supabase-side rollback (slow).** Move the previously-used HS256 key to standby and re-promote to active via the dashboard. ~20 min for edge cache propagation. Use only if 1 and 2 fail.

Plan 11-05 smoke test hits `/auth/me` post-deploy; failure triggers Slack alert via Sentry → `#kendrew-alerts`. Manual rollback per Phase 9 D-12.

## 7. Effort Estimate

| Component | Hours |
|---|---|
| `backend/auth/jwks.py` module + httpx-based JWKS fetcher | 3 |
| Refactor `auth/dependencies.py` and `auth/router.py::exchange_token` | 1 |
| `tests/auth/test_jwks.py` (10–11 cases) | 4 |
| `tests/conftest.py` mock JWKS fixture + ES256 token helper | 1 |
| Update `.env.example` + add `SUPABASE_JWKS_URL` config field | 0.5 |
| `docs/deploy.md` rotation runbook | 1 |
| Manual end-to-end verification in staging (login flow against real Supabase staging project) | 1.5 |
| Buffer for unknown unknowns (algorithm-confusion edge cases, async lock semantics) | 2 |
| **Total** | **~14 hours** (≈2 focused days) |

## 8. Risks + Mitigations

### Security

| Risk | Mitigation |
|---|---|
| **Algorithm confusion** — attacker submits `{"alg":"HS256"}` token signed with the public JWK as the HMAC secret (CVE-2017-11424 family). | Pin algorithm per code path: ES256 branch passes `algorithms=["ES256"]` only; HS256 branch `["HS256"]` only. Never combine them in one `jwt.decode` call. Explicit test. |
| **JWKS spoofing** | JWKS URL is HTTPS-only, derived from `settings.supabase_url` (not user-controlled). TLS to `*.supabase.co` is operated by Supabase. |
| **Cache poisoning** | JWKS served under Supabase TLS with public CA — same trust as every `supabase-py` call. |
| **Revoked-key cache lag** — PyJWT's `PyJWKClient` LRU cache does not honor TTL ([#1051](https://github.com/jpadilla/pyjwt/issues/1051)). | New `SupabaseJWKSVerifier` owns its own dict+timestamp cache with explicit 300s TTL and invalidate-on-`kid`-miss. Bug does not apply. |
| **HS256 secret left in env after migration** | After Supabase revokes legacy HS256 key, remove `SUPABASE_JWT_SECRET` from Railway. Documented in deploy runbook. |

### Operational

| Risk | Mitigation |
|---|---|
| **Lockout if JWKS unreachable at boot** | Warmup is advisory — log Sentry warning, do not raise. First auth request still attempts fetch; failure returns 401, not 500. |
| **JWKS unreachable mid-flight** | Stale cache serves for the 5-min TTL. After TTL, cached `kid`s still verify; unseen `kid`s 401. No worse than today's Supabase dependency. |
| **Dual-key window confusion** | `.env.example` comments call out the active key path. Runbook includes a JWKS curl check. |
| **Test flakiness from real-network JWKS** | All unit tests mock JWKS via `respx`. Only manual staging verification touches real Supabase. |

## 9. Dependencies Already Resolved

- `PyJWT==2.12.1` — has `PyJWKClient` and ES256 support via `cryptography`. Pinned.
- `httpx==0.28.1` — pinned, used elsewhere in tests; the same pin will host the JWKS GET.
- `respx==0.22.0` — pinned, will mock JWKS in tests.
- `cryptography` — pulled in transitively by `supabase` and `PyJWT[crypto]`.

No `requirements.txt` change needed for this migration.

## 10. Open Questions for Reviewer

- **Q1.** Should the HS256 fallback be **time-bounded by config** (e.g. `LEGACY_HS256_FALLBACK_UNTIL=2026-06-01`) or operator-driven (drop `SUPABASE_JWT_SECRET` from env when ready)? Operator-driven is simpler; the config flag adds a forcing function but also a foot-gun. Recommendation: operator-driven with a calendar reminder.
- **Q2.** Add Sentry breadcrumb for every `kid` cache miss? Useful for spotting unexpected key rotation; cheap.
- **Q3.** Should we also adopt `sb_publishable_*` / `sb_secret_*` API keys (Option B) in this same PR, or split into a follow-up phase? Recommendation: follow-up. Not coupled.

---

## References

- [Supabase JWT signing keys docs](https://supabase.com/docs/guides/auth/signing-keys)
- [Supabase JWT docs (claims structure)](https://supabase.com/docs/guides/auth/jwts)
- [Supabase API keys docs (sb_publishable / sb_secret)](https://supabase.com/docs/guides/api/api-keys)
- [Supabase blog: Introducing JWT Signing Keys](https://supabase.com/blog/jwt-signing-keys)
- [Live JWKS endpoint, kendrew-prod](https://omrhpkmgiqvuwpadhbsl.supabase.co/auth/v1/.well-known/jwks.json) — confirmed ES256/P-256 on 2026-04-29
- [PyJWT API reference](https://pyjwt.readthedocs.io/en/stable/api.html)
- [PyJWT issue #615 — JWKS caching semantics](https://github.com/jpadilla/pyjwt/issues/615)
- [PyJWT issue #1051 — cache_keys=True serves revoked keys](https://github.com/jpadilla/pyjwt/issues/1051)
- [pyjwt-key-fetcher (async alternative, considered and rejected)](https://github.com/ioxiocom/pyjwt-key-fetcher)
- [objectgraph.com migration recipe](https://objectgraph.com/blog/migrating-supabase-jwt-jwks/)
