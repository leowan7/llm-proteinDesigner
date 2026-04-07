---
phase: 6
slug: ui-improvements
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-07
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | vitest (frontend) / pytest 7.x (backend) |
| **Config file** | `frontend/vitest.config.ts` / `backend/pytest.ini` |
| **Quick run command** | `cd frontend && npx vitest run --reporter=verbose` |
| **Full suite command** | `cd frontend && npx vitest run && cd ../backend && python -m pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd frontend && npx vitest run --reporter=verbose`
- **After every plan wave:** Run `cd frontend && npx vitest run && cd ../backend && python -m pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | SC-1 (sessions persist) | integration | `npx vitest run session` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | SC-2 (sidebar nav) | component | `npx vitest run sidebar` | ❌ W0 | ⬜ pending |
| 06-02-01 | 02 | 1 | SC-3 (job history table) | component | `npx vitest run jobs` | ❌ W0 | ⬜ pending |
| 06-02-02 | 02 | 1 | SC-4 (settings page) | component | `npx vitest run settings` | ❌ W0 | ⬜ pending |
| 06-03-01 | 03 | 2 | SC-5 (onboarding prompts) | component | `npx vitest run greeting` | ❌ W0 | ⬜ pending |
| 06-03-02 | 03 | 2 | SC-6 (WCAG 2.2 AA) | a11y | `npx vitest run a11y` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `frontend/src/__tests__/setup.ts` — test setup with jsdom
- [ ] `frontend/vitest.config.ts` — vitest configuration if not present
- [ ] `eslint-plugin-jsx-a11y` — install for accessibility linting

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Sidebar responsive collapse | SC-2 | Visual layout at breakpoints | Resize browser to 768px, 1024px; verify sidebar collapses to overlay/sheet |
| Color contrast WCAG AA | SC-6 | Requires visual contrast checker | Run axe-core audit on all new pages; verify no contrast failures |
| Keyboard navigation flow | SC-6 | Tab order requires manual verification | Tab through sidebar → chat → job history; verify focus indicators visible |
| Stripe Customer Portal redirect | SC-4 | External service integration | Click "Manage payment method" → verify Stripe portal opens |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
