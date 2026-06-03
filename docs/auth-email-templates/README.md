# Bindwave Auth Email Templates

These six HTML templates power Supabase Auth's transactional email for the
Bindwave project (`omrhpkmgiqvuwpadhbsl`). They are committed here as the
audited source of truth so they can be re-applied verbatim if the Supabase
dashboard ever gets reset, the project is recreated, or another reviewer
needs to see what users receive.

## Project setup that must already be in place

The templates assume the following is configured in the Supabase Auth
dashboard. If any of this drifts, links in emails will break.

| Setting | Value | Where |
| --- | --- | --- |
| Site URL | `https://bindwave.com` | Authentication -> URL Configuration |
| Redirect URLs | `https://bindwave.com/**`, `http://localhost:5173/**` | Authentication -> URL Configuration |
| Custom SMTP | enabled | Authentication -> Emails -> SMTP Settings |
| SMTP host | `smtp.resend.com` | same |
| SMTP port | `465` | same |
| SMTP username | `resend` | same |
| SMTP password | `RESEND_API_KEY` from Railway `backend/production` | same |
| Sender email | `jobs@bindwave.com` | same |
| Sender name | `Bindwave` | same |

## The six templates and their subjects

| Supabase template name | File | Subject line |
| --- | --- | --- |
| Confirm signup | [`confirm-signup.html`](./confirm-signup.html) | `Confirm your Bindwave email` |
| Invite user | [`invite.html`](./invite.html) | `You're invited to Bindwave` |
| Magic Link | [`magic-link.html`](./magic-link.html) | `Your Bindwave sign-in link` |
| Change Email Address | [`change-email.html`](./change-email.html) | `Confirm your new Bindwave email` |
| Reset Password | [`reset-password.html`](./reset-password.html) | `Reset your Bindwave password` |
| Reauthentication | [`reauthentication.html`](./reauthentication.html) | `Confirm it's you on Bindwave` |

Plain-text fallbacks live alongside each HTML file with the `.txt` extension.
Supabase does not surface a separate plain-text body in the dashboard, so
the `.txt` files are an audit artifact only. Email clients that cannot
render HTML will fall back to the `<a>` URL in the HTML, which is also
exposed as a plain-text "copy this link" line at the bottom of each body.

## How to apply via the dashboard (manual)

1. Open https://supabase.com/dashboard/project/omrhpkmgiqvuwpadhbsl/auth/templates
2. For each of the six tabs, paste the matching `.html` file contents into
   the "Message body" editor and the matching subject line into the
   "Subject heading" input.
3. Click "Save changes" at the bottom of each tab.

## How to apply via the Management API (for re-apply / drift correction)

Run [`apply-templates.sh`](./apply-templates.sh) with a Supabase Personal
Access Token in `SUPABASE_ACCESS_TOKEN`:

```bash
export SUPABASE_ACCESS_TOKEN=sbp_xxxxxxxxxxxxxxxxxxxxxxxxx
bash docs/auth-email-templates/apply-templates.sh
```

The script PATCHes
`/v1/projects/omrhpkmgiqvuwpadhbsl/config/auth` with all six
`mailer_subjects_*` and `mailer_templates_*_content` fields in one call.
Generate a token at https://supabase.com/dashboard/account/tokens.

## Variables used by these templates

| Variable | Meaning | Used in |
| --- | --- | --- |
| `{{ .ConfirmationURL }}` | Full callback URL with action token | confirm-signup, invite, magic-link, change-email, reset-password |
| `{{ .Token }}` | One-time code (6 digit) | reauthentication |
| `{{ .Email }}` | Current account email | change-email |
| `{{ .NewEmail }}` | New account email | change-email |

Reauthentication never has a `ConfirmationURL` because it is a code the
user must type back into the application, not a click-through link.

## Design notes

- Dark editorial palette: warm dark grey background (`#1a1a1d`), bone text
  (`#e8e8e8`), Bindwave indigo accent (`#7873ff`).
- Web-safe font stack only (Georgia for display, system sans for body) so
  no `@import` is required and Gmail / Outlook / Apple Mail all render
  consistently.
- Table-based layout with `bgcolor` attributes for Outlook compatibility.
- `color-scheme: dark` meta hint so iOS Mail and supporting clients do not
  invert the palette.
- No external resources (no images, no remote CSS), so the email body is
  fully self-contained and renders offline.
- No emoji, no em or en dashes, no condensed uppercase, in line with the
  brand voice rules.
