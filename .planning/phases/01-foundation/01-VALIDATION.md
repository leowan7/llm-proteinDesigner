---
phase: 1
slug: foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (backend) + vitest (frontend) |
| **Config file** | `backend/pytest.ini` or `backend/pyproject.toml` (Wave 0 installs) |
| **Quick run command** | `cd backend && pytest tests/ -x -q` |
| **Full suite command** | `cd backend && pytest tests/ && cd ../frontend && npx vitest run` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && pytest tests/ -x -q`
- **After every plan wave:** Run `cd backend && pytest tests/ && cd ../frontend && npx vitest run`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 0 | AUTH-01 | unit | `pytest tests/test_auth_register.py -x -q` | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 1 | AUTH-01 | integration | `pytest tests/test_auth_register.py -x -q` | ❌ W0 | ⬜ pending |
| 1-01-03 | 01 | 1 | AUTH-02 | integration | `pytest tests/test_auth_login.py -x -q` | ❌ W0 | ⬜ pending |
| 1-01-04 | 01 | 2 | AUTH-03 | integration | `pytest tests/test_auth_reset.py -x -q` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | AUTH-04 | integration | `docker compose config --quiet && docker compose up -d && sleep 5 && docker compose ps` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/conftest.py` — shared fixtures (test client, db session, test user factory)
- [ ] `backend/tests/test_auth_register.py` — stubs for AUTH-01 (register, email verify flow)
- [ ] `backend/tests/test_auth_login.py` — stubs for AUTH-02 (login, session persistence)
- [ ] `backend/tests/test_auth_reset.py` — stubs for AUTH-03 (password reset flow)
- [ ] `pytest` + `httpx` install in `backend/requirements-dev.txt`
- [ ] `vitest` + `@testing-library/react` in `frontend/package.json`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Email verification link delivered and clickable | AUTH-01 | Requires real email or Supabase local dashboard inspection | Start dev env, register user, open Supabase Studio at localhost:54323, check Inbucket at localhost:54324 for email, click link, verify user record shows `email_confirmed_at` set |
| Password reset email received | AUTH-03 | Requires real email or Inbucket check | Trigger reset from UI, open Inbucket at localhost:54324, click link, set new password, verify login works |
| Session persists across browser refresh | AUTH-02 | Browser state not testable in unit tests | Log in via frontend, close and reopen tab, confirm user remains authenticated without re-auth prompt |
| MinIO bucket created on dev-up | AUTH-04 | Docker/shell orchestration outside pytest | Run `./dev-up.sh`, open MinIO console at localhost:9001, confirm `protein-designs` bucket (or configured name) exists with per-user prefix structure |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
