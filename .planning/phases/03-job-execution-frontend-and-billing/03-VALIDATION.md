---
phase: 3
slug: job-execution-frontend-and-billing
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-19
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.5 + pytest-asyncio 0.24.0 |
| **Config file** | `backend/pyproject.toml [tool.pytest.ini_options]` |
| **Quick run command** | `cd backend && pytest tests/jobs/ tests/billing/ tests/gpu/ -x -q` |
| **Full suite command** | `cd backend && pytest -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && pytest tests/jobs/ tests/billing/ tests/gpu/ -x -q`
- **After every plan wave:** Run `cd backend && pytest -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | JOB-01 | unit | `pytest tests/jobs/test_status_stream.py -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | JOB-02 | unit (mock Resend) | `pytest tests/jobs/test_notifications.py -x` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | JOB-03 | unit (mock RunPod) | `pytest tests/jobs/test_cancel.py -x` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | RESULT-01 | unit (mock S3) | `pytest tests/jobs/test_download.py -x` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 1 | RESULT-02 | unit | `pytest tests/jobs/test_download.py::test_report_in_zip -x` | ❌ W0 | ⬜ pending |
| 03-02-03 | 02 | 1 | RESULT-03 | unit | `pytest tests/jobs/test_results.py::test_next_steps -x` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 1 | BILL-01 | unit (mock Stripe) | `pytest tests/billing/test_meter.py -x` | ❌ W0 | ⬜ pending |
| 03-03-02 | 03 | 1 | BILL-02 | unit | `pytest tests/billing/test_estimate.py -x` | ❌ W0 | ⬜ pending |
| 03-03-03 | 03 | 1 | BILL-03 | unit (mock Stripe customer) | `pytest tests/billing/test_payment_gate.py -x` | ❌ W0 | ⬜ pending |
| 03-03-04 | 03 | 1 | BILL-04 | unit (mock + call order) | `pytest tests/jobs/test_dispatch.py::test_db_before_runpod -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/jobs/test_status_stream.py` — stubs for JOB-01
- [ ] `backend/tests/jobs/test_notifications.py` — stubs for JOB-02 (mock resend.Emails.send)
- [ ] `backend/tests/jobs/test_cancel.py` — stubs for JOB-03 (mock RunPodProvider)
- [ ] `backend/tests/jobs/test_download.py` — stubs for RESULT-01, RESULT-02 (mock S3)
- [ ] `backend/tests/jobs/test_results.py` — stubs for RESULT-03
- [ ] `backend/tests/jobs/test_dispatch.py` — stubs for BILL-04 (mock + call order assertion)
- [ ] `backend/tests/billing/test_meter.py` — stubs for BILL-01 (mock stripe.billing.MeterEvent.create)
- [ ] `backend/tests/billing/test_estimate.py` — stubs for BILL-02
- [ ] `backend/tests/billing/test_payment_gate.py` — stubs for BILL-03 (mock stripe.Customer.retrieve)
- [ ] `backend/tests/gpu/test_runpod_provider.py` — unit tests for RunPodProvider ABC implementation

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSE updates render in browser without refresh | JOB-01 | Requires browser + live SSE connection | Open `/jobs/{id}`, trigger status change, verify DOM updates |
| Email received on job complete | JOB-02 | Requires actual Resend delivery | Complete a job in staging, check inbox |
| Stripe Checkout redirect works | BILL-03 | Requires live Stripe session | Click "Launch job" without payment method, verify redirect |
| Cost shown on results page | BILL-01 | Requires end-to-end billing flow | Complete job, check results page shows GPU cost |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
