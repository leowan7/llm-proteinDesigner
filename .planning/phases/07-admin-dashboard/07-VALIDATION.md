---
phase: 07
slug: admin-dashboard
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-09
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.5 (backend) / vitest (frontend — if installed) |
| **Config file** | `backend/pytest.ini` or `pyproject.toml` |
| **Quick run command** | `cd backend && python -m pytest tests/ -x -q` |
| **Full suite command** | `cd backend && python -m pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && python -m pytest tests/ -x -q`
- **After every plan wave:** Run `cd backend && python -m pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | SC-5 | T-07-01 | Admin auth returns 403 for non-admin users | unit | `pytest tests/admin/test_dependencies.py` | W0 (Plan 02) | ⬜ pending |
| 07-01-02 | 01 | 1 | SC-1, SC-2 | T-07-04 | Admin endpoints return correct data shapes | integration | `pytest tests/admin/test_router.py` | W0 (Plan 02) | ⬜ pending |
| 07-01-03 | 01 | 1 | SC-2 | T-07-05 | Cancel service records billing correctly | unit | `pytest tests/admin/test_service.py` | W0 (Plan 02) | ⬜ pending |
| 07-02-01 | 02 | 2 | SC-5 | T-07-01 | Dependency tests verify 403 behavior | unit | `pytest tests/admin/test_dependencies.py -v` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 2 | SC-1, SC-2, SC-3, SC-6 | — | Router tests cover all 7 endpoint groups | integration | `pytest tests/admin/test_router.py -v` | ❌ W0 | ⬜ pending |
| 07-02-03 | 02 | 2 | SC-2 | — | Service tests cover cancel + billing | unit | `pytest tests/admin/test_service.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Plan 02 (Wave 2) creates the test files that serve as the Wave 0 test scaffolds for Plan 01's production code:

- [ ] `backend/tests/admin/__init__.py` — test package init
- [ ] `backend/tests/admin/test_dependencies.py` — admin auth dependency tests (3 tests)
- [ ] `backend/tests/admin/test_router.py` — admin endpoint tests (8+ tests covering all 7 endpoint groups)
- [ ] `backend/tests/admin/test_service.py` — shared cancel service tests (3 tests)

Plan 02 depends on Plan 01 (wave 2 after wave 1), so tests are written against real Plan 01 code.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Admin layout renders correctly | SC-1 | Visual verification | Navigate to /admin, verify sidebar nav and dark theme |
| Revenue charts display correctly | SC-3 | Visual verification | Check Recharts bar chart renders with test data; verify cost-of-goods/margin cards show values or N/A |
| Non-admin redirect to /chat | SC-5 | Browser behavior | Login as non-admin, navigate to /admin, verify silent redirect |
| System status indicators | SC-4 | Visual verification | Check green/red dots render for API/DB/Redis status |
| Audit action labels | SC-6 | Visual verification | Check audit entries show "Viewed Users", "Cancelled Job" etc. |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (Plan 02 creates test files for Plan 01)
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
