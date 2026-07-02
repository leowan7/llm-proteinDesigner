---
phase: 13-public-api
plan: 06
subsystem: frontend Settings — API key management UI
status: complete
tags: [phase-13, frontend, settings-ui, api-key-management]
requires: [13-04]
provides:
  - frontend api-keys typed client (listApiKeys / createApiKey / revokeApiKey)
  - ApiKey + CreatedApiKey TypeScript interfaces
  - ApiKeysTab (list + idle-badge + create/revoke wiring)
  - CreateApiKeyModal (2-stage plaintext-once flow, cannot-dismiss gate)
  - RevokeConfirmModal (type-name-to-confirm destructive gate)
  - SettingsPage "API Keys" tab (6th tab, between Privacy and Usage)
affects: [13-07]
tech-stack:
  added: []
  patterns:
    - controlled-dialog-onOpenChange-interceptor
    - type-name-to-confirm-destructive-action
    - two-stage-secret-reveal-with-confirmation-gate
key-files:
  created:
    - frontend/src/lib/api-keys.ts
    - frontend/src/components/api-keys/ApiKeysTab.tsx
    - frontend/src/components/api-keys/CreateApiKeyModal.tsx
    - frontend/src/components/api-keys/RevokeConfirmModal.tsx
    - frontend/src/components/api-keys/__tests__/ApiKeysTab.test.tsx
    - frontend/src/components/api-keys/__tests__/CreateApiKeyModal.test.tsx
    - frontend/src/components/api-keys/__tests__/RevokeConfirmModal.test.tsx
  modified:
    - frontend/src/pages/SettingsPage.tsx
decisions:
  - "The client wraps the WEB endpoints /user/api-keys (NOT /api/v1/api-keys) per RESEARCH §5.5. Because /user/* is not in the api.ts X-Org-Id opt-out list, the shared api() helper auto-attaches X-Org-Id — api.ts was left untouched as required."
  - "No shadcn Checkbox primitive exists under components/ui/. Rather than invent a new design-system primitive (plan gotcha #2), the confirmation checkbox uses a native <input type=checkbox className=accent-primary> inside a <Label>. It exposes role=checkbox for tests and keyboard users."
  - "The cannot-dismiss-stage-2 invariant is enforced by a single handleOpenChange interceptor. Base UI's Dialog routes Escape, backdrop click, and the X button all through onOpenChange(false); the interceptor early-returns when stage===2 && !confirmed, blocking every close vector at one choke point. showCloseButton is also set false in stage 2 so the X is absent entirely."
  - "CreatedApiKey extends ApiKey (per PATTERNS.md). The backend POST response only returns {id, name, prefix, plaintext, created_at}; role/last_used_at are absent at runtime, but the UI only reads plaintext/prefix/name from the created key, so the extends-ApiKey typing is safe."
  - "Idle detection (D-04): isIdle() returns true when last_used_at is null OR older than 30 days; the amber outline Badge shows 'Unused Nd' (or 'Unused' when never used)."
metrics:
  duration: ~30m
  completed: 2026-07-02
  tasks: 2 of 3 executed (Task 3 is a human-verify checkpoint — see below)
  files: 8 (7 created, 1 modified)
  tests_added: 19
  tests_total: 106
---

# Phase 13 Plan 06: API Keys Settings UI Summary

Shipped the Settings "API Keys" tab and its supporting typed client, list tab, and two modals for the plaintext-once create flow and type-to-confirm revoke flow. Tasks 1 and 2 (all code + all Vitest specs) are complete and committed; Task 3 is a `checkpoint:human-verify` gate that requires a live browser + backend and is recorded below as PENDING for a human to run.

## What was built

**Task 1 — client + components + specs (commit 5f4d765)**
- `frontend/src/lib/api-keys.ts` — `listApiKeys()` / `createApiKey(name)` / `revokeApiKey(id)` over `/user/api-keys`, `/user/api-keys` (POST), `/user/api-keys/{id}/revoke`; `ApiKey` + `CreatedApiKey` interfaces.
- `ApiKeysTab.tsx` — fetches keys on mount, renders a table (Name, Prefix, Created, Last used + amber "Unused Nd" idle badge per D-04), Create button, per-row Revoke; empty state + error state; hosts both modals.
- `CreateApiKeyModal.tsx` — stage 1 (name → Create) → stage 2 (plaintext in a monospaced read-only Input + Copy button + "I have saved this key" checkbox). `handleOpenChange` interceptor blocks Escape / backdrop / X in stage 2 until the checkbox is checked; plaintext is never re-displayed after close (state reset on close).
- `RevokeConfirmModal.tsx` — type-the-key-name-to-confirm; Revoke stays disabled unless `typedName === apiKey.name`; on success calls `onRevoked` so the tab refetches.
- 3 Vitest specs (19 tests) covering list/empty/idle-badge/create-open/error, the 2-stage flow + cannot-dismiss (Escape + backdrop) + copy, and the revoke type-to-confirm gate.

**Task 2 — SettingsPage integration (commit 31e4d39)**
- Imported `ApiKeysTab`, added `"api-keys"` to `VALID_SETTINGS_TABS` (enables `/settings?tab=api-keys` deep-link), inserted `<TabsTrigger value="api-keys">API Keys</TabsTrigger>` and `<TabsContent value="api-keys"><ApiKeysTab /></TabsContent>` between Privacy and Usage. Additive diff only; conditional Organization tab preserved as the 7th tab.

## Verification (Tasks 1-2)

| Check | Command | Result |
|-------|---------|--------|
| New specs | `npx vitest run src/components/api-keys/__tests__` | 3 files / **19 passed** |
| Full suite (no regression) | `npx vitest run` | 17 files / **106 passed** |
| Typecheck | `npx tsc -b` | exit 0, no errors |
| Production build | `npm run build` (`tsc -b && vite build`) | exit 0 (pre-existing chunk-size + dynamic-import warnings only, unrelated to these files) |
| Tab order | grep `TabsTrigger value=` | Account, Billing, Privacy, **API Keys**, Usage, Notifications (+ Organization conditional) |
| Client target | grep `/user/api-keys` in api-keys.ts | 3 refs to `/user/api-keys`; no `/api/v1` usage (only a comment noting what is NOT used) |

Note: `package.json` has no `typecheck` script (the plan's `<verify>` assumed one). Used `npx tsc -b`, which is exactly the type-check phase of the `build` script (`tsc -b && vite build`). Documented as a minor plan/verify mismatch; the intent (types compile clean) is fully satisfied.

## Deviations from Plan

- **[Rule 3 - blocking] No `typecheck` npm script.** The plan's `<verify>` calls `npm run typecheck`, which does not exist. Substituted `npx tsc -b` (the build's own type-check step) — equivalent coverage, exit 0.
- **[Rule 3 - blocking] No shadcn Checkbox primitive.** `components/ui/` has no `checkbox.tsx`. Used a native `<input type="checkbox">` styled with `accent-primary` inside a `<Label>` rather than inventing a new primitive (per gotcha #2). Exposes `role="checkbox"` for tests/a11y.
- **Test-only type fix.** `CREATED` fixture in `CreateApiKeyModal.test.tsx` was typed as `CreatedApiKey` (was inferring `last_used_at: null` as the `null` literal, breaking the `onCreated` prop type). No production-code change.

No architectural (Rule 4) changes. `frontend/src/lib/api.ts` (X-Org-Id opt-out list) was NOT modified, as required.

## Task 3 — PENDING human-verify (NOT executed)

Task 3 is `type="checkpoint:human-verify"` and cannot be automated: it requires a real browser session + running backend against local Supabase to confirm the plaintext-once UX and clipboard behavior. Vitest already asserts the cannot-dismiss invariant against jsdom, but the plan requires a human to confirm it against real browser timing + the live clipboard API. This gate is left OPEN for a human to run. The Tasks 1-2 deliverable is complete and green.

**Exact manual steps the human must perform** (from the plan):

1. Start dev server (`cd frontend && npm run dev`) + backend (`cd backend && uvicorn main:app --reload`) against local Supabase. Log in as a user in an organization.
2. Navigate to `/settings?tab=api-keys`. Confirm the "API Keys" tab is between "Privacy" and "Usage". Click it.
3. **Empty state:** with no keys, confirm the empty-state message + Create button appear.
4. **Create:** click "Create new key", type a name (e.g. "Local dev"), click Create.
5. **Stage 2 plaintext:** confirm a `bw_live_XXXX…` key shows in a monospaced input. Click Copy; paste into another app to confirm the clipboard has the plaintext.
6. **Cannot-dismiss invariant (CRITICAL)** — without checking the box:
   a. Press **Escape** → modal must NOT close.
   b. Click the **backdrop** → modal must NOT close.
   c. Click the **X** (if present) → modal must NOT close. (Note: in this build the X is intentionally hidden in stage 2 via `showCloseButton={false}`.)
   d. Confirm the **Close button is disabled**.
7. **Confirm-then-close:** check "I have saved this key" → Close becomes enabled → click Close → modal closes.
8. **Plaintext gone forever:** refresh; the list shows the key with its prefix (`bw_live_XXXX`) but no plaintext, and no re-reveal path.
9. **Idle badge:** if a key has `last_used_at` > 30d ago, confirm the "Unused Nd" amber badge renders (or skip if no such key).
10. **Revoke:** click Revoke on the new key → type-name modal opens. Type a WRONG name → Revoke stays disabled. Type the CORRECT name → Revoke enables → click Revoke.
11. **Revoke effective:** row disappears from the list; a `curl` to `/api/v1/jobs` with the plaintext returns 401.

**Resume signal:** human types "approved" if all steps pass, otherwise describes the failure and which modal needs adjustment.

## Threat surface

No new security surface beyond the plan's `<threat_model>`. The plaintext lives only in React state for the modal's lifetime and is cleared on close (T-13-01 mitigation), the revoke gate mitigates fat-finger (T-13-02), and the client relies on the backend's `require_role` for authorization (T-13-03). No `/api/v1` or `api.ts` changes.

## Commits

- `5f4d765` — feat(13-06): API-key Settings UI client + tab + create/revoke modals
- `31e4d39` — feat(13-06): wire API Keys tab into SettingsPage between Privacy and Usage

## Self-Check: PASSED

- All 7 created files exist on disk; SettingsPage.tsx modified.
- Both commits present in `git log`.
- 19 new specs + 106 full-suite tests pass; tsc + build exit 0.
