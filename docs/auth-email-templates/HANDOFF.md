# Supabase Auth email hardening — 2026-06-03

## What was broken

Production Supabase Auth was sending verification + reset emails with
links pointing at `http://localhost:3000/#access_token=...` because the
default Site URL had never been changed after the project was created.
A real signup (`leo@ranomics.com`) on https://bindwave.com/signup
landed on a broken localhost link, surfacing the bug.

## What changed (live in production now)

All changes are on the Supabase project `omrhpkmgiqvuwpadhbsl`
(dashboard breadcrumb still reads "kendrew-prod" — that's the pre-rebrand
project name, the project ref is correct).

### 1. URL configuration

| Field | Before | After |
| --- | --- | --- |
| Site URL | `http://localhost:3000` | `https://bindwave.com` |
| Redirect URLs | empty | `https://bindwave.com/**`, `http://localhost:5173/**` |

The localhost entry uses Vite's default dev port confirmed at
[frontend/vite.config.ts](../../frontend/vite.config.ts) (no `server.port`
override, so the default 5173 is the right allowlist entry for `npm run
dev`).

### 2. Custom SMTP via Resend

| Field | Value |
| --- | --- |
| Host | `smtp.resend.com` |
| Port | `465` |
| Username | `resend` |
| Password | the existing `RESEND_API_KEY` already on Railway `backend/production` |
| Sender email | `jobs@bindwave.com` |
| Sender name | `Bindwave` |

`jobs@bindwave.com` is already Resend-verified — it's the address the
backend uses for transactional job notifications (`RESEND_FROM_EMAIL`
in Railway), so no new DNS work was needed. The Resend rate limit is now
the enforcing ceiling instead of Supabase's default 3/hr cap.

### 3. The six email templates

All six were replaced with Bindwave-voiced HTML committed to this
directory:

| Supabase template | Subject | File |
| --- | --- | --- |
| Confirm signup | "Confirm your Bindwave email" | [`confirm-signup.html`](./confirm-signup.html) |
| Invite user | "You're invited to Bindwave" | [`invite.html`](./invite.html) |
| Magic link or OTP | "Your Bindwave sign-in link" | [`magic-link.html`](./magic-link.html) |
| Change email address | "Confirm your new Bindwave email" | [`change-email.html`](./change-email.html) |
| Reset password | "Reset your Bindwave password" | [`reset-password.html`](./reset-password.html) |
| Reauthentication | "Confirm it's you on Bindwave" | [`reauthentication.html`](./reauthentication.html) |

Plain-text fallback bodies (`.txt`) are also committed for audit but
Supabase does not render them — clients fall back to the `<a>` URLs
inside the HTML body if they cannot render HTML.

## Frontend code path: unchanged

The existing implicit-flow handler at
[frontend/src/App.tsx:35-58](../../frontend/src/App.tsx) parses the
`#access_token=...&type=...` fragment Supabase appends after `/verify`
and routes:
- `type=signup` → `/email-confirmed` (drops the hash for security)
- `type=recovery` → `/reset-password/confirm` (keeps the hash for token exchange)

[`ResetPasswordConfirm.tsx`](../../frontend/src/pages/ResetPasswordConfirm.tsx)
then POSTs the access + refresh tokens to the backend's
`/auth/exchange-token` endpoint, which validates them and sets HTTP-only
cookies. PKCE was not adopted because the frontend deliberately does NOT
use the Supabase JS client — all auth flows through cookies set by the
FastAPI backend.

## End-to-end retest verified

A live password-reset for `leo@ranomics.com` was triggered through
`POST https://app.bindwave.com/auth/reset-password`. Verified items:

- Email arrived in Leo's inbox within ~60 seconds.
- `From:` header was `Bindwave <jobs@bindwave.com>` (NOT
  `noreply@mail.app.supabase.io`).
- Subject was the custom `Reset your Bindwave password`.
- Body rendered with the Bindwave wordmark, serif heading, indigo
  "Set a new password" button, and dark editorial palette.
- The action link redirected through
  `https://omrhpkmgiqvuwpadhbsl.supabase.co/auth/v1/verify?...&redirect_to=https://bindwave.com/reset-password/confirm`
  and landed on `https://bindwave.com/reset-password/confirm#access_token=...&type=recovery`.
- `curl -sI https://bindwave.com/reset-password/confirm` returns
  `HTTP/1.1 200 / Content-Type: text/html` — the
  [Vercel SPA-rewrite added in `4e14ff5`](../../frontend/vercel.json)
  handles direct-nav.

The very last verification step (actually setting a new password and
re-logging in) was deliberately skipped so as not to mutate Leo's real
production credential during a debugging session. The
`/auth/exchange-token` + `/auth/update-password` code paths are unchanged
from Phase 11 Plan 04 (WR-05 JWKS verifier work) and were last
end-to-end validated there.

## Re-apply path

If the dashboard is ever reset or the templates drift, generate a
Supabase Personal Access Token at
https://supabase.com/dashboard/account/tokens and run:

```bash
export SUPABASE_ACCESS_TOKEN=sbp_xxx
bash docs/auth-email-templates/apply-templates.sh
```

The script PATCHes all six `mailer_subjects_*` and
`mailer_templates_*_content` fields on
`/v1/projects/omrhpkmgiqvuwpadhbsl/config/auth` in a single call.

## Files staged but not committed

Per the user's no-auto-commit preference, the following new files are
left unstaged for Leo to review and commit:

- `docs/auth-email-templates/README.md`
- `docs/auth-email-templates/HANDOFF.md` (this file)
- `docs/auth-email-templates/apply-templates.sh`
- `docs/auth-email-templates/confirm-signup.{html,txt}`
- `docs/auth-email-templates/invite.{html,txt}`
- `docs/auth-email-templates/magic-link.{html,txt}`
- `docs/auth-email-templates/change-email.{html,txt}`
- `docs/auth-email-templates/reset-password.{html,txt}`
- `docs/auth-email-templates/reauthentication.{html,txt}`

No backend, frontend, or `.planning/` files were changed.
