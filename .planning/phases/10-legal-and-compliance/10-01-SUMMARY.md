---
phase: 10-legal-and-compliance
plan: 01
subsystem: legal
tags: [tos, privacy, subprocessors, cookies, gdpr, ccpa, ontario]
dependency_graph:
  requires: []
  provides: [legal-content-v1, version-string-canonical]
  affects: [frontend-routes, signup-form, settings-page]
tech_stack:
  added: []
  patterns: [versioned-legal-content, tsx-components-for-legal-pages]
key_files:
  created:
    - frontend/src/pages/legal/versions.ts
    - frontend/src/pages/legal/LegalLayout.tsx
    - frontend/src/pages/legal/Terms.tsx
    - frontend/src/pages/legal/Privacy.tsx
    - frontend/src/pages/legal/Subprocessors.tsx
    - frontend/src/pages/legal/Cookies.tsx
decisions:
  - "Content format is .tsx components, not MDX — avoids build-config changes; versioned via shared versions.ts"
  - "Single LegalLayout wraps all four pages with a 'Draft — legal review pending' banner; remove only after counsel sign-off"
  - "Version strings initialized to 2026-04-23; bump on every material revision"
  - "Subprocessor list finalized from codebase scan: 8 processors (Supabase, Cloudflare, Modal, RunPod emergency, Stripe, Anthropic, Resend, Sentry); RCSB + UniProt classified as public APIs, not subprocessors"
  - "Retention default 90 days from job creation (30-365 configurable per user); 30-day grace before hard-delete on account deletion"
  - "Jurisdiction: Ontario, Canada; arbitration via ADR Institute of Canada for international users"
  - "No-training / no-sharing clause explicit in both ToS (§4) and Privacy Policy (§3); Anthropic workspace has zero-data-retention opt-in documented in Subprocessors table"
metrics:
  duration: 30min
  completed: "2026-04-23T13:30:00Z"
  tasks: 6
  files: 6
  approximate_word_count: 4800
---

# Phase 10 Plan 1: Legal Content v1

Draft v1 of the four legal pages as React components under `frontend/src/pages/legal/`. Each page carries a version string consumed by downstream plans for acceptance-gate and consent-banner logic.

## What landed

- `frontend/src/pages/legal/versions.ts` — canonical version strings (TOS_VERSION, PRIVACY_VERSION, COOKIES_VERSION, SUBPROCESSORS_VERSION, all "2026-04-23").
- `frontend/src/pages/legal/LegalLayout.tsx` — shared chrome with the "Draft — legal review pending" banner, title, last-updated date, prose styling, and return-to-app footer.
- `frontend/src/pages/legal/Terms.tsx` — 14 sections covering acceptance, service description, IP ownership (§3), no-training clause (§4), acceptable use (including dual-use/biosecurity exclusion), payment, retention, termination, warranty disclaimer and liability cap, indemnification, changes, Ontario governing law with arbitration fallback, and contact.
- `frontend/src/pages/legal/Privacy.tsx` — 11 sections with explicit per-category legal basis under GDPR Article 6, subprocessor reference, retention windows, security measures, full GDPR/UK-GDPR/PIPEDA rights, CCPA/CPRA rights, international transfer safeguards (SCCs + UK IDTA), children, change notification, contact.
- `frontend/src/pages/legal/Subprocessors.tsx` — structured table of 8 subprocessors (Supabase, Cloudflare, Modal, RunPod emergency, Stripe, Anthropic, Resend, Sentry), each with service, data handled, region, privacy policy link, and DPA note. 30-day advance notice promise for changes. Explicit carve-out that RCSB + UniProt are public APIs receiving no PII.
- `frontend/src/pages/legal/Cookies.tsx` — inventory of three strictly-necessary cookies (access_token, refresh_token, csrftoken) with purpose, expiry, path, flags; explicit statement that no analytics/advertising/tracking cookies are set; clarification that localStorage UI prefs are not cookies.

## Verification

- `npx tsc --noEmit` passes cleanly against the new files.
- All anchor IDs used in cross-references (`#ip-ownership`, `#no-training`, `#retention`, `#subprocessors`, etc.) are defined as `id` attributes on `h2`/`h3` elements in the source files.
- Each page's stated coverage maps to ROADMAP Phase 10 success criteria:
  - SC1 (ToS + IP ownership + no-training + acceptable use) → `Terms.tsx` §3, §4, §5.
  - SC2 (Privacy + GDPR/CCPA + retention + deletion rights) → `Privacy.tsx` §5, §7.
  - SC3 (Cookie consent banner disclosure) → `Cookies.tsx` inventory + consent section; banner component itself lands in Plan 10-03.
  - SC6 (no training / no sharing) → `Terms.tsx` §4, `Privacy.tsx` §3.
  - SC7 (subprocessor list) → `Subprocessors.tsx` full table.
- Subprocessor list confirmed against codebase: config.py + imports across backend + frontend package.json.

## Out of scope (downstream plans)

- Routing pages at `/legal/{terms,privacy,subprocessors,cookies}` → Plan 10-06.
- Signup acceptance checkbox + DB migration storing `tos_accepted_at` + `tos_version` → Plan 10-02.
- Cookie consent banner React component + localStorage persistence → Plan 10-03.
- GDPR data-export + account-deletion endpoints + Privacy tab in SettingsPage → Plan 10-04.
- Data retention cron (arq) with 7-day warning email → Plan 10-05.
- Counsel review + removal of "Draft" banner → post-launch, tracked separately.

## Follow-ups captured

- Counsel review checklist to build before marketing launch (biosecurity clause, liability cap, arbitration venue, international transfer clauses, CCPA Right to Know fulfilment SLAs).
- Privacy tab UI spec depends on Plan 10-04 endpoint contracts — create UI-SPEC.md at start of that plan.
