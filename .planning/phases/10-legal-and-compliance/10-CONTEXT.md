---
phase: 10-legal-and-compliance
depends_on: [05-production-hardening]
status: context_gathered
created: 2026-04-23T12:45:00Z
---

# Phase 10: Legal & Compliance — Context

## Phase Goal (from ROADMAP)

Platform meets legal requirements for commercial operation and biopharma procurement. Scientists at regulated companies can get internal approval to use the platform.

## Success Criteria (7)

1. **Terms of Service** published and accepted on signup — covers IP ownership (user retains all rights to designs), data handling, liability limitations, acceptable use.
2. **Privacy Policy** published — GDPR and CCPA compliant, covers what data is collected (PDB uploads, job specs, usage metrics), retention periods, deletion rights.
3. **Cookie consent banner** implemented (platform uses HTTP-only auth cookies — minimal but must be disclosed).
4. **GDPR rights in settings** — user can request full data export (Article 20) and account deletion (Article 17) from settings.
5. **Data retention policy** — uploaded PDB files and job results auto-expire after configurable period (default 90 days); user notified before deletion.
6. **No training / no sharing clause** — ToS explicitly states user-uploaded structures are not used for model training or shared with third parties.
7. **Subprocessor list** documented (Supabase, Cloudflare, RunPod/Modal, Stripe, Anthropic) for enterprise procurement due diligence.

## Existing Code Touchpoints

| Area | Current State | Change Needed |
|------|---------------|---------------|
| Signup form | `frontend/src/pages/Signup.tsx` — no ToS/Privacy checkbox | Add "I agree to ToS + Privacy Policy" checkbox gate, links to legal pages |
| Cookies | HTTP-only access_token, refresh_token (path=/auth/refresh), csrftoken | Consent banner for first-visit; no consent required for strictly-necessary auth cookies but disclosure is required |
| Settings page | `frontend/src/pages/SettingsPage.tsx` tabs: Account / Notifications / Billing | Add "Privacy" tab with Export Data + Delete Account buttons |
| Data retention | Jobs/PDB files persist indefinitely in Supabase + MinIO | Add scheduled cron (arq) to expire jobs + objects after N days; email notification 7 days before |
| Email templates | `jobs/notifications.py` — completion, failure, daily progress | Add `send_retention_warning_email` + `send_account_deletion_confirmation_email` |
| DB schema | `users` table — no `tos_accepted_at`, `tos_version` columns | Add migration: `tos_accepted_at TIMESTAMPTZ`, `tos_version TEXT`, `data_retention_days INT DEFAULT 90` |
| Static pages | No `/legal/terms`, `/legal/privacy`, `/legal/subprocessors` routes | Add MDX-driven legal pages under `/legal/*` with versioned content |
| Export pipeline | No GDPR data export endpoint | Add `GET /user/data-export` returning ZIP of user data (jobs, sessions, messages, uploaded structures) via background task |
| Deletion pipeline | No account deletion endpoint | Add `POST /user/delete-account` → soft-delete → 30-day grace period → hard delete all PII + objects |

## Known Constraints

- **Supabase Auth** owns the `auth.users` table; we cannot simply `DELETE FROM auth.users`. Must use the Supabase Admin API `auth.admin.deleteUser(uid)` after anonymizing app data.
- **MinIO / S3** objects under `users/{user_id}/` must be enumerated and deleted via `list_objects_v2` → `delete_objects` batched.
- **Stripe Customer** must be deleted via `stripe.Customer.delete(id)` or anonymized (keep invoices for 7-year tax retention; detach PII).
- **Cookie consent** must distinguish strictly-necessary (no consent needed) from analytics (consent required). We currently use only strictly-necessary cookies, so the banner is a one-time disclosure rather than a granular opt-in, but must remain dismissible and re-accessible via a footer link.
- **ToS versioning** — when ToS changes, users must re-accept. Store version string and check on login; show re-acceptance modal if outdated.

## Provisional Plan Breakdown (target ~6 plans)

- **10-01: Legal content drafting** — write ToS, Privacy Policy, Subprocessors pages as versioned MDX files. Cover IP ownership (user retains all rights), no-training/no-sharing clause, GDPR/CCPA specifics, retention policy, subprocessor list.
- **10-02: ToS acceptance on signup** — DB migration adds `tos_accepted_at`, `tos_version`. Signup form adds acceptance checkbox. Login path enforces re-acceptance when version bumps.
- **10-03: Cookie consent banner** — React component shown on first visit; dismissible; stores consent timestamp in localStorage; footer link to re-open. Disclose strictly-necessary cookies only.
- **10-04: GDPR export + deletion endpoints** — `GET /user/data-export` produces a presigned ZIP; `POST /user/delete-account` soft-deletes with 30-day grace; scheduled job hard-deletes PII + objects + Supabase auth user + Stripe customer. Add "Privacy" tab to SettingsPage wiring both actions with confirmation dialogs.
- **10-05: Data retention cron** — arq cron finds jobs + uploaded structures past retention window; emails warning 7 days before; deletes MinIO objects + job rows on expiry. Per-user override in settings (configurable days, min 30 max 365).
- **10-06: Legal routes + footer + signup wiring** — Astro/Vite routes for `/legal/terms`, `/legal/privacy`, `/legal/subprocessors`, `/legal/cookies`; footer component linking to all; navbar link from settings.

## Decisions (locked 2026-04-23)

1. **Legal content sourcing** — self-drafted v1 with clear "draft — legal review pending" banner; Leo reviews with counsel before public marketing launch. v1 content uses defensible patterns from public open-source templates (Termly-style structure).
2. **Retention clock** — 90 calendar days from job creation (not last access). Simpler UX, easier to explain, users can re-run to refresh a record.
3. **Soft-delete grace period** — 30 days between account deletion request and hard-delete. Email-verifiable reactivation during grace period.
4. **Subprocessor list (confirmed via codebase scan 2026-04-23)** — 8 services handle PII:
   - Supabase (auth + Postgres)
   - Cloudflare (R2 object storage + cloudflared tunnels)
   - Modal (GPU compute — primary)
   - RunPod (GPU compute — quarantined emergency fallback, documented as "may be used if primary unavailable")
   - Stripe (billing)
   - Anthropic (Claude API)
   - Resend (transactional email)
   - Sentry (error tracking)
   Public APIs receive no PII and are not subprocessors: RCSB PDB, UniProt.
5. **Jurisdiction** — Ontario, Canada (Ranomics HQ); arbitration clause for international users.

## Risks

- **Jurisdictional scope creep**: GDPR (EU), CCPA (California), PIPEDA (Canada), UK-GDPR — compliance overlap but different specific requirements. v1 covers GDPR + CCPA per ROADMAP; PIPEDA compliance follows naturally from GDPR but should be confirmed.
- **Retention backfill**: existing jobs in the DB pre-date the retention policy. Policy should apply from effective date forward, not retroactively delete existing data without notification.
- **Testing account deletion**: full hard-delete must not be triggered accidentally in test runs. Gate behind feature flag + explicit confirmation token.

## Ready for Planning

Context gathered. Next step: `/gsd-plan-phase 10-legal-and-compliance` to generate PLAN.md for plan 10-01 (legal content drafting).
