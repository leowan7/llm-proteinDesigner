---
phase: 9
slug: testing-ci-cd
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-10
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (backend), vitest (frontend unit), playwright (frontend E2E) |
| **Config file** | `backend/pytest.ini`, `frontend/vitest.config.ts`, `frontend/playwright.config.ts` |
| **Quick run command** | `cd backend && python -m pytest tests/ -x -q --timeout=30` |
| **Full suite command** | `cd backend && python -m pytest tests/ --cov=. --cov-report=term && cd ../frontend && npx vitest run && npx playwright test` |
| **Estimated runtime** | ~120 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && python -m pytest tests/ -x -q --timeout=30`
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | SC-1 | — | N/A | unit | `python -m pytest tests/ -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pytest-cov` — add to backend requirements.txt for coverage reporting
- [ ] `frontend/vitest.config.ts` — configure if missing
- [ ] `frontend/playwright.config.ts` — configure with webServer block

*Existing pytest infrastructure covers backend test requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Smoke test rollback | SC-5 | Requires production deploy | Trigger deploy, verify health check, confirm alert on failure |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
