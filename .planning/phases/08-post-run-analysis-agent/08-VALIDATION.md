---
phase: 8
slug: post-run-analysis-agent
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-10
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | backend/pytest.ini |
| **Quick run command** | `cd backend && python -m pytest tests/ -x -q --timeout=30` |
| **Full suite command** | `cd backend && python -m pytest tests/ -v --timeout=60` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && python -m pytest tests/ -x -q --timeout=30`
- **After every plan wave:** Run `cd backend && python -m pytest tests/ -v --timeout=60`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Note: Task IDs will be populated after PLAN.md files are created.*

---

## Wave 0 Requirements

- [ ] `backend/tests/test_analysis_tools.py` — stubs for load_job_results, analyze_pdb, generate_report tools
- [ ] `backend/tests/conftest.py` — shared fixtures for mock job candidates and scores data

*Existing test infrastructure covers framework install.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| PDF report renders correctly with Kendrew branding | SC-7 | Visual layout verification | Generate report for a test job, open PDF, verify logo/header/table formatting |
| Metric explanations are contextually accurate | SC-3 | Requires domain expert review | Load results, ask agent to explain metrics, verify scientific accuracy |
| Next-step guidance is protocol-appropriate | SC-5 | Requires domain expert review | Review expression/purification recommendations against target type |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
