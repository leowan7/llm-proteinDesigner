---
phase: 2
slug: agent-and-structure-input
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-19
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.5 + pytest-asyncio 0.24.0 (backend), vitest 4.x (frontend) |
| **Config file** | `backend/pytest.ini` (backend), `frontend/vite.config.ts` (frontend) |
| **Quick run command** | `cd backend && pytest tests/ -x -q` |
| **Full suite command** | `cd backend && pytest tests/ && cd ../frontend && npx vitest run` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && pytest tests/ -x -q`
- **After every plan wave:** Run `cd backend && pytest tests/ && cd ../frontend && npx vitest run`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 0 | INPUT-01 | unit | `pytest tests/pdb/test_normalize.py::test_upload_valid_pdb -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 0 | INPUT-05 | unit | `pytest tests/pdb/test_normalize.py::test_mse_conversion -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 0 | INPUT-02 | integration | `pytest tests/pdb/test_fetch.py::test_fetch_by_pdb_id -x` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 0 | INPUT-03 | integration | `pytest tests/pdb/test_fetch.py::test_uniprot_to_pdb -x` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 0 | INPUT-04 | integration | `pytest tests/pdb/test_fetch.py::test_nl_to_pdb -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 0 | AGENT-04 | unit | `pytest tests/pdb/test_validate.py::test_buried_hotspot -x` | ❌ W0 | ⬜ pending |
| 02-04-01 | 04 | 0 | AGENT-01 | unit | `pytest tests/agent/test_tools.py::test_classify_intent -x` | ❌ W0 | ⬜ pending |
| 02-04-02 | 04 | 0 | AGENT-02 | unit | `pytest tests/agent/test_tools.py::test_tool_recommendation -x` | ❌ W0 | ⬜ pending |
| 02-05-01 | 05 | 0 | AGENT-03 | integration | `pytest tests/agent/test_session.py::test_wizard_completion -x` | ❌ W0 | ⬜ pending |
| 02-05-02 | 05 | 0 | AGENT-05 | unit | `pytest tests/agent/test_jobspec.py::test_warn_blocks_dispatch -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/pdb/__init__.py` + `backend/tests/pdb/test_normalize.py` — stubs for INPUT-01, INPUT-05
- [ ] `backend/tests/pdb/test_fetch.py` — stubs for INPUT-02, INPUT-03, INPUT-04; requires `respx` for httpx mocking
- [ ] `backend/tests/pdb/test_validate.py` — stubs for AGENT-04; requires test PDB fixture file
- [ ] `backend/tests/agent/__init__.py` + `backend/tests/agent/test_tools.py` — stubs for AGENT-01, AGENT-02; mock Anthropic client
- [ ] `backend/tests/agent/test_session.py` — stubs for AGENT-03; requires Redis test fixture
- [ ] `backend/tests/agent/test_jobspec.py` — stubs for AGENT-05
- [ ] `backend/tests/conftest.py` update — add Redis mock fixture and test PDB fixture file path
- [ ] `pip install respx` — httpx mock library needed for external API tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Chat UI renders agent messages with typing indicator | AGENT-03 | Visual rendering check | Open chat, send message, observe typing dots appear then resolve to text |
| Drag-drop PDB upload triggers file parse | INPUT-01 | Browser drag-drop event | Open chat, drag .pdb file onto input, verify upload indicator appears |
| Review card displays before job launch | AGENT-05 | Visual layout and content check | Complete wizard flow, verify review card shows tool, target, parameters, cost |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
