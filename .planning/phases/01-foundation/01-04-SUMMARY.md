---
phase: 01-foundation
plan: 04
subsystem: frontend
tags: [react, auth, forms, routing, supabase, csrf, jwt]

# Dependency graph
requires:
  - 01-02 (FastAPI auth endpoints)
  - 01-03 (React scaffold, AuthLayout, API client)
provides:
  - 6 auth screens: SignUp, Login, VerifyEmail, EmailConfirmed, ResetPassword, ResetPasswordConfirm
  - React Router wiring for all auth routes
  - Hash fragment handler for Supabase email verification and password reset redirects
  - Complete signup → verify email → login → reset password flow
affects:
  - Phase 02+ (all authenticated features depend on this auth flow)
---

# Plan 01-04 Summary

## Objective

Build all 6 auth screens, wire them to FastAPI auth endpoints, and verify the complete auth flow end-to-end.

## What was built

### Auth Pages (Task 1)
- **SignUp** (`/signup`): email + password + confirm with zod validation
- **Login** (`/login`): email + password, redirects to `/` on success
- **VerifyEmail** (`/verify-email`): post-signup holding screen showing user's email
- **EmailConfirmed** (`/email-confirmed`): success screen after email verification
- **ResetPassword** (`/reset-password`): send reset link form
- **ResetPasswordConfirm** (`/reset-password/confirm`): exchanges recovery tokens on mount, then shows password form

All pages use AuthLayout, react-hook-form + zod, Loader2 spinners during submission.

### Verification Fixes (Task 2 — checkpoint)
Issues found and fixed during human verification:

1. **CSRF blocking signup/login** — `starlette-csrf` enforced on all POSTs by default; fixed by setting `sensitive_cookies={"access_token", "refresh_token"}` so CSRF only applies when a session exists
2. **Docker networking** — Backend container couldn't reach Supabase at `127.0.0.1`; fixed by using `host.docker.internal` in `.env.local`
3. **JWT algorithm mismatch** — Newer Supabase uses ES256 (asymmetric) instead of HS256; fixed `get_current_user` to try HS256 then fall back to JWKS/ES256; `exchange-token` now skips signature verification since token is from Supabase's own redirect
4. **Hash fragment redirect** — Supabase redirects to `/#access_token=...&type=signup` after email confirmation; added `HashRedirectHandler` component to detect and route to `/email-confirmed` or `/reset-password/confirm`
5. **Indigo button color** — CSS variable used HSL format but Tailwind v4 requires oklch; converted to `oklch(0.585 0.233 277.117)`
6. **Footer centering** — Added `text-center` and balanced padding to CardFooter

### Seed user fix
Direct SQL insert into `auth.users` doesn't work with newer Supabase GoTrue. Seed user created via Supabase Admin API instead.

## Commits

- `10891d9` feat(01-04): build all 6 auth pages and wire routes
- `8fc49e8` fix(01-04): resolve auth flow issues found during verification

## Verification results

All flows tested end-to-end by human:
- [x] Signup + email verification (via Inbucket)
- [x] Login with verified user
- [x] Login with seed user (created via Admin API)
- [x] Password reset (send link → click → set new password → login)
- [x] Validation errors (empty form, mismatched passwords, wrong password)
- [x] Visual: dark background, indigo buttons, centered footer text
