---
phase: 11-deployment
plan: 05
subsystem: monitoring-docs-roadmap
status: partial-complete-blocked-on-human-verify
tags: [deployment, sentry, monitoring, docs, roadmap-correction, checkpoint-human-verify]

requires:
  - phase: 11-deployment
    plan: "01"
    provides: /debug/sentry-test route + scripts/rollback_drill.sh (referenced from docs/deploy.md)
  - phase: 11-deployment
    plan: "03"
    provides: frontend-bundle secret-leak grep in .github/workflows/test.yml (SC 7 guard - moved here per revision fix Blocker 3 Option A; not duplicated by Plan 11-05)
  - phase: 11-deployment
    plan: "04"
    provides: WEBHOOK_HMAC_SECRET + WEBHOOK_HMAC_SECRET_PREV + .env.example runtime-tag matrix (source for docs/deploy.md rotation runbook + env matrix)
provides:
  - Hot-path Sentry Performance sampling via traces_sampler in backend/main.py (D-14)
  - docs/deploy.md operational runbook (env matrix + D-10 rotation runbook + SC 9 rollback drill + monitoring + deployment flow + subprocessor reference)
  - .planning/ROADMAP.md Phase 11 SC 6 corrected to Modal primary + RunPod quarantined
  - .planning/ROADMAP.md Phase 11 SC 8 corrected to Sentry + UptimeRobot + #kendrew-alerts Slack
  - PENDING: Sentry kendrew-backend + kendrew-frontend projects; SENTRY_DSN / VITE_SENTRY_DSN_FRONTEND pasted into Railway + Vercel; Slack integration live; 2 UptimeRobot monitors; synthetic-error + hot-path transaction validation; SC 6 Modal GPU smoke; SC 9 wall-clock rollback drill recorded in docs/deploy.md Last Drill
affects: [Phase 11 overall status - SC 8 + SC 9 cannot be marked green until Task 3 human-verify completes; SC 6 smoke cross-validates Plan 11-03 Modal deploy-modal.yml]

tech-stack:
  added: []
  patterns:
    - "Sentry traces_sampler allowlist: explicit _HOT_PATHS set; return 1.0 on match, 0.0 otherwise (free-tier-safe; denylist would quietly blow quota if a new route is added)"
    - "docs/deploy.md single-source runbook: env matrix table + dated Last Rotations + Last Drill tables as operational audit trail instead of scattered wiki pages"

key-files:
  created:
    - docs/deploy.md
  modified:
    - backend/main.py
    - .planning/ROADMAP.md

key-decisions:
  - "Kept profiles_sample_rate=0.0 and both existing integrations (StarletteIntegration + FastApiIntegration with transaction_style='endpoint') unchanged in sentry_sdk.init - Task 1 is additive (traces_sampler), not a rewrite."
  - "_traces_sampler defensively handles None transaction_context and None name (sampling_context.get(..., {}) or {}, then name or '') - sampling_context shape is not strictly contracted by Sentry and a missing key must fall through to 0.0, not crash the SDK."
  - "Wrote docs/deploy.md with a TBD/pending Last Drill row deliberately. Plan 11-05 Task 3 (human-verify) is where the real wall-clock seconds get recorded; this commit provides the schema, not the data."
  - "ROADMAP SC 6 correction is additive narrative - kept the 'from production backend' phrasing inside the new sentence so the SC still reads as a single complete criterion, not two fragments."
  - "ROADMAP SC 8 correction keeps PagerDuty/Opsgenie in parenthetical deferral note ('deferred until a paying customer with an uptime SLO') rather than deleting outright - preserves the original roadmap intent as a future-growth signal without it counting as an active SC."

patterns-established:
  - "Per-plan Sentry config: add hot paths to _HOT_PATHS set in backend/main.py with a D-14 comment; no per-route annotations or decorators."
  - "Documentation commits for Phase 11 go to docs/deploy.md (not a new top-level README or wiki). Rotation/drill tables are dated and additive."

requirements-completed: []

duration: ~8 min (Tasks 1 and 2 only; Task 3 pending external human-verify gate)
completed: 2026-04-24 (Tasks 1 and 2 only; Task 3 PENDING)
completion: PARTIAL. Tasks 1 and 2 DONE and committed; Task 3 BLOCKED on Leo's Sentry + UptimeRobot + validation work. Plan 11-05 is NOT overall complete.
---

# Phase 11 Plan 05: Monitoring + Docs + ROADMAP Correction Summary (PARTIAL)

**Sentry hot-path traces_sampler wired into backend/main.py, docs/deploy.md created as the single-source deployment runbook (env matrix + D-10 rotation + SC 9 rollback drill), and .planning/ROADMAP.md Phase 11 SC 6 + SC 8 corrected to match Phase 10/11 CONTEXT decisions. Task 3 (Sentry project setup, UptimeRobot monitors, synthetic-error + hot-path + Modal GPU + rollback validation) is a human-verify checkpoint and remains PENDING.**

## Status

- **Task 1 (Sentry hot-path Performance in backend/main.py): DONE** - commit `bca945b`
- **Task 2 (docs/deploy.md + ROADMAP SC 6 + SC 8 corrections): DONE** - commit `84bdc85`
- **Task 3 (Sentry projects + UptimeRobot monitors + live validation): PENDING** - requires Leo to execute Blocks A-F from `11-05-PLAN.md §how-to-verify` in Sentry, UptimeRobot, Railway, Vercel, and against staging

## Performance

- **Duration (Tasks 1-2):** ~8 min
- **Started:** 2026-04-24
- **Tasks 1-2 completed:** 2026-04-24
- **Full plan completion:** pending Leo's Task 3 work
- **Tasks executed autonomously:** 2 of 3

## Task 1 Accomplishments (DONE)

Commit `bca945b`: `feat(11-05): wire Sentry hot-path Performance via traces_sampler`

1. **`backend/main.py` (modified)** - added `_HOT_PATHS` frozen-style set enumerating the 5 D-14 endpoints (`POST /agent/message`, `POST /jobs/launch`, `POST /webhooks/runpod`, `POST /webhooks/heartbeat`, `POST /jobs/{job_id}/upload-urls`) and a module-level `_traces_sampler(sampling_context: dict) -> float` that returns `1.0` on hot-path match and `0.0` otherwise.
2. **`sentry_sdk.init(...)` updated** - replaced `traces_sample_rate=0.0` with `traces_sampler=_traces_sampler`; preserved `profiles_sample_rate=0.0`, `StarletteIntegration(transaction_style="endpoint")`, `FastApiIntegration(transaction_style="endpoint")`, and the `environment=` flag. Net: hot-paths now collect Sentry Performance transactions at 100%, cold paths at 0%, staying inside the Sentry free-tier quota (RESEARCH §Code Example 3, T-11-05-08 mitigation).
3. **Backwards-compat preserved** - `if settings.sentry_dsn:` guard unchanged; when the DSN is empty (local dev without a Sentry project), the init call is skipped entirely and no traces are generated.

## Task 1 Acceptance Verification (all pass)

- `grep -c "traces_sampler" backend/main.py` = 3 (definition line `def _traces_sampler`, docstring mention "traces_sampler per Phase 11 D-14", init arg `traces_sampler=_traces_sampler`) >= 2
- `grep -c "_HOT_PATHS" backend/main.py` = 2 (set definition + `name in _HOT_PATHS` check)
- `grep "POST /jobs/launch" backend/main.py` - matches
- `grep "POST /webhooks/runpod" backend/main.py` - matches
- `grep "POST /webhooks/heartbeat" backend/main.py` - matches
- `grep "POST /jobs/{job_id}/upload-urls" backend/main.py` - matches
- `grep "POST /agent/message" backend/main.py` - matches
- `cd backend && python -c "import main"` - EXIT 0 (import OK)
- `pytest -k "not e2e and not integration"` - 169 passed, 1 pre-existing-unrelated fail (`test_login_returns_cookies` requires local Supabase not running; failure reproduces identically on pre-edit baseline via `git stash` - NOT caused by this task, logged in "Deferred Issues" below per scope-boundary rule)
- (Inherited) `grep "SUPABASE_SERVICE_ROLE_KEY" .github/workflows/test.yml` - matches (landed in Plan 11-03 Task 1 commit `f81be7b`, not duplicated here)

## Task 2 Accomplishments (DONE)

Commit `84bdc85`: `docs(11-05): add deploy runbook + correct ROADMAP SC 6 and SC 8`

1. **`docs/deploy.md` (created, 172 lines, zero emojis)** - six top-level sections:
   - **Environments** table mapping prod/staging/local to frontend, backend, DB, Redis, R2, and Modal env values.
   - **Env Variable Matrix (Phase 11 D-12)** - 31-row table scoping every prod-relevant env var across Railway backend, Railway worker, Vercel, and Modal Secrets. Explicit `NO - never Vercel` on `SUPABASE_SERVICE_ROLE_KEY` (Pitfall 5 reference). Explicit `NO (backend config.py alias only)` on `RUNPOD_WEBHOOK_SECRET` (post-Plan 11-04 deprecation).
   - **Secret Rotation Runbook (Webhook HMAC - D-10)** - 6-step procedure: `openssl rand -hex 32`, Railway Variables rotation, Modal `modal secret create --env main kendrew-webhook` + redeploy, 60-min wait with Sentry "Webhook signed with PREV secret" observation, clear PREV, record in Last Rotations table. Seeded with 2026-04-24 baseline row.
   - **Rollback Drill (SC 9 - 5-minute rollback)** - references `scripts/rollback_drill.sh --dry-run` (safe anytime) and the interactive mode; manual steps for Railway and Vercel CLI/UI rollback; `/health` verification target under 2 min after rollback; seeded `Last Drill` table with `TBD / staging / pending` row for Task 3 to fill.
   - **Monitoring** - signal-tool-channel map (application errors -> Sentry -> #kendrew-alerts Slack, external liveness -> UptimeRobot, daily GPU spend -> arq cron -> Resend, post-deploy smoke -> smoke.yml). Enumerates all 5 D-14 hot paths.
   - **Deployment Flow** - branch protection gates -> Railway + Vercel auto-deploy -> Modal deploy-modal.yml on path-touch -> smoke.yml informational (D-08). Explicit mention of `supabase db push` as preDeployCommand from `railway.toml`.
   - **Subprocessors** - points at the canonical `frontend/src/content/legal/subprocessors.mdx` list (Phase 10).

2. **`.planning/ROADMAP.md` (modified, 2 line changes, no reordering)**:
   - **SC 6 rewrite** - old: `GPU jobs dispatch to RunPod from production backend` -> new: `GPU jobs dispatch to Modal (primary GPU provider per Phase 10) from production backend; RunPod remains quarantined as an emergency fallback only`. Brings the ROADMAP into agreement with Phase 10 D-7 subprocessor decision and Plan 11-03 Task 1's deploy-modal.yml enablement.
   - **SC 8 rewrite** - old: `Monitoring: Sentry for errors, uptime monitoring with PagerDuty/Opsgenie alerting` -> new: `Monitoring: Sentry for errors + UptimeRobot for uptime, both routing to #kendrew-alerts Slack (PagerDuty/Opsgenie deferred until a paying customer with an uptime SLO)`. Matches Phase 5 + Phase 11 D-13 decision; the PagerDuty/Opsgenie reference is kept in-parenthetical as future-growth signal, not as an active SC.

## Task 2 Acceptance Verification (all pass)

- `test -f docs/deploy.md` - YES
- `grep -c "WEBHOOK_HMAC_SECRET_PREV" docs/deploy.md` = 3 (matrix row + runbook steps 2 + 5)
- `grep -c "openssl rand -hex 32" docs/deploy.md` = 2 (rotation step 1 + env matrix CSRF note)
- `grep -c "rollback_drill.sh" docs/deploy.md` = 2 (dry-run + interactive invocations)
- `grep -c "supabase db push" docs/deploy.md` = 1 (Deployment Flow step 3)
- `grep -cE "pooler.supabase.com:6543|Supavisor transaction pooler" docs/deploy.md` = 1 (DATABASE_URL matrix row includes both literals in the Notes column)
- `grep -c "Pitfall 5" docs/deploy.md` = 1 (SUPABASE_SERVICE_ROLE_KEY matrix Notes column)
- `grep -c "UptimeRobot" docs/deploy.md` = 1 (Monitoring section row; also appears in table headers without the exact token)
- `grep -cE "POST /agent/message|POST /jobs/launch|POST /webhooks/runpod|POST /webhooks/heartbeat|POST /jobs/\{job_id\}/upload-urls" docs/deploy.md` = 5 (Monitoring section hot-path bullets)
- `grep -ci "Modal.*primary GPU provider" .planning/ROADMAP.md` = 1 (SC 6 corrected)
- `grep -ci "RunPod.*quarantined" .planning/ROADMAP.md` = 1 (SC 6 corrected)
- `grep -cE "UptimeRobot.*Slack|Sentry.*UptimeRobot" .planning/ROADMAP.md` = 2 (SC 8 corrected)
- `grep -c "uptime monitoring with PagerDuty/Opsgenie alerting" .planning/ROADMAP.md` = 0 (old active-SC wording fully removed)
- `grep -c "RunPod from production backend" .planning/ROADMAP.md` = 0 (old SC 6 wording fully removed)
- `python -c "import re,sys; sys.exit(bool(re.search(r'[\U0001F300-\U0001F9FF\u2600-\u27BF]', open('docs/deploy.md',encoding='utf-8').read())))"` - EXIT 0 (zero emoji characters)

## Task 3 Status: PENDING (human-verify gate)

Task 3 is a `type="checkpoint:human-verify"` step. It is NOT code work; it requires Leo to execute six blocks in external dashboards and run two validation scripts that depend on live Railway + Vercel (which are themselves gated on Plan 11-03 Task 2's human-action block).

| Block | Dashboard / script | What Leo does | Approx. time |
|-------|--------------------|----------------|--------------|
| A | sentry.io + Railway Variables + Vercel Env + Sentry Slack integration | Create `kendrew-backend` (Python/FastAPI) + `kendrew-frontend` (React) Sentry projects; copy DSNs; paste `SENTRY_DSN` into Railway backend-prod + backend-staging; paste `VITE_SENTRY_DSN_FRONTEND` into Vercel Production + Preview; add Slack `#kendrew-alerts` integration; trigger Railway + Vercel redeploys to pick up env vars. | ~15 min |
| B | curl + Sentry dashboard + Slack | `curl -i https://app-staging.kendrew.ai/debug/sentry-test` (expect 500); confirm Sentry -> kendrew-backend -> Issues shows `ZeroDivisionError` within 1 min; confirm #kendrew-alerts receives Sentry post within 2 min; launch a draft job on staging and confirm Sentry Performance shows a `POST /jobs/launch` transaction with `traces_sample_rate=1.0`. | ~10 min |
| C | uptimerobot.com | Create HTTPS monitors for `https://app.kendrew.ai/health` and `https://app-staging.kendrew.ai/health` at 5-min intervals; add Slack webhook + email alert contacts; wait 10 min; confirm both show "Up". | ~10 min |
| D | Modal CLI + scripts/validate_prod_gpu.sh staging | Configure Modal credentials via `modal token set --profile kendrew` (keychain-style; NOT `export MODAL_TOKEN_SECRET=...` which lands in `~/.bash_history` permanently - T-11-05-09); run `bash scripts/validate_prod_gpu.sh staging`; expect EXIT 0 and `PASS: Modal smoke run completed in env=staging`. | ~15 min |
| E | railway CLI + curl loop | Identify previous staging deploy SHA via `railway deployments list --service kendrew-backend-staging --limit 2 --json`; `railway rollback --service kendrew-backend-staging --deployment <sha>`; start wall-clock timer; poll `/health` every 5s until 200; target < 5 min; record wall-clock seconds in `docs/deploy.md` Last Drill row. Deliberately NO intentional-breakage branch (T-11-05-10 mitigation). | ~15 min |
| F | dashboards (optional) | Paste `SENTRY_DSN` + `VITE_SENTRY_DSN_FRONTEND` into `PROVISIONING.md` (gitignored) for reference. | ~2 min |

**Task 3 resume signal (from 11-05-PLAN.md §resume-signal):** Leo types `"validated"` with Sentry kendrew-backend issue URL, UptimeRobot monitor list, `validate_prod_gpu.sh staging` stdout, wall-clock seconds from Block E, and a #kendrew-alerts screenshot/paste.

Plan 11-05 cannot be marked overall-complete until Task 3 is signed off against its `<acceptance_criteria>` block (Sentry projects live, DSNs set, UptimeRobot shows "Up" for both monitors 10+ min, synthetic error + Slack post + hot-path transaction all visible, Modal staging smoke EXIT 0 with no shell-history leak, rollback drill < 5 min with wall-clock recorded in docs/deploy.md Last Drill).

## Decisions Made

- **Allowlist over denylist for `_traces_sampler`.** Per T-11-05-08 mitigation, `_traces_sampler` returns `1.0` only for names in `_HOT_PATHS` and `0.0` for everything else. A denylist (`return 1.0 if name not in _COLD_PATHS else 0.0`) would blow the Sentry free-tier quota the moment a new route is added without a corresponding entry; an allowlist fails closed.
- **Preserve existing Sentry init surface.** Kept `profiles_sample_rate=0.0`, both integrations with `transaction_style="endpoint"`, the `environment=` flag, and the `if settings.sentry_dsn:` guard unchanged. Only swapped `traces_sample_rate=0.0` for `traces_sampler=_traces_sampler`. Smaller diff = smaller blast radius; future phases that add Profiles or integrations need not worry this edit drifted them.
- **Defensive sampling_context parsing.** `sampling_context.get("transaction_context", {}) or {}` handles both "key missing" (returns `{}`) and "key present but None" (still `{}`). Same pattern for `name`. Sentry's sampling_context shape is not strictly contracted, and a KeyError inside `_traces_sampler` would disable sampling globally without the operator noticing.
- **Kept all three old integration args exactly.** The pre-edit init used `StarletteIntegration(transaction_style="endpoint")` + `FastApiIntegration(transaction_style="endpoint")`. Phase 11 RESEARCH Code Example 3 showed `FastApiIntegration()` (no args); kept the stricter `transaction_style="endpoint"` form because that is how Sentry reliably matches the `POST /jobs/launch` transaction name format - without it, Sentry would use the function name (`launch_job`) instead of the HTTP route, and `_HOT_PATHS` would never match.
- **`docs/deploy.md` Last Drill row seeded with TBD/pending.** Block E of Task 3 is where the real wall-clock gets recorded. Shipping with TBD is deliberate - the schema is the artifact; the data is Leo's.
- **ROADMAP SC 8 keeps PagerDuty/Opsgenie as a parenthetical deferral.** The plan's acceptance criterion allows the old text to remain in "deferred notes/context" but forbids it as an active SC. Preserving it in-parenthetical documents the original intent without counting toward compliance - a reader scanning SC 8 sees the current-state answer first, then the deferral rationale.
- **ROADMAP SC 6 reads as one sentence, not two.** `GPU jobs dispatch to Modal (primary GPU provider per Phase 10) from production backend; RunPod remains quarantined as an emergency fallback only` - kept the semicolon construction so a future `grep "SC 6"` picks up the full criterion as a single line.

## Deviations from Plan

No deviations. Plan executed as written. Both task commits stage only the files listed in `<files>` (Task 1: `backend/main.py`; Task 2: `docs/deploy.md`, `.planning/ROADMAP.md`); no incidental files swept into either commit despite ~35 other modified files in the working tree.

## Deferred Issues

**1. [Out of scope - Pre-existing Windows local-env test failures]** Two backend pytest tests fail on the current Windows machine against the pre-edit baseline (verified by stashing `backend/main.py` and rerunning):
- `tests/integration/test_agent_flow.py::test_agent_conversation_flow_resolve_to_launch` - `OSError: [WinError 121] The semaphore timeout period has expired` on an asyncio socket connect. The test is flagged as requiring `supabase start` per its own module docstring; local Supabase is not running on this workstation.
- `tests/test_auth.py::test_login_returns_cookies` - `assert 401 == 200`. Same root cause - backend auth requires the local Supabase stack to be running so the JWT verify path has a real project-ref to check.

Both failures reproduce identically with and without the Task 1 Sentry edit (verified via `git stash -u -- backend/main.py` then re-run). Per the scope-boundary rule, these are NOT caused by this plan and are NOT fixed here. The CI matrix (`.github/workflows/test.yml`) stands up Supabase + Redis in Docker services and does not see these failures.

**2. [Out of scope - Pydantic v2 deprecation]** `backend/config.py` class-based `class Config:` still emits `PydanticDeprecatedSince20` warning. Already logged in Plan 11-04 deferred issues; still deferred here.

## Issues Encountered

None during Tasks 1-2. Task 3 issues can only surface during Leo's dashboard work; capture them in a follow-up note before Plan 12 scheduling.

## User Setup Required

**All of Task 3 is required user setup.** See "Task 3 Status" table above. Nothing further is required from Claude's side - `backend/main.py`, `docs/deploy.md`, and the ROADMAP are ready for the human-verify gate.

## Next Phase Readiness

- **Phase 11 overall:** PARTIAL. Plan 11-05 is not overall complete; Plan 11-03 Task 2 is also PENDING (Cloudflare DNS + Railway + Vercel + GitHub secrets, a separate human-action gate). SC 8 and SC 9 cannot be marked green until Plan 11-05 Task 3 completes. SC 1, SC 2, SC 6, SC 7 remain transitively gated on Plan 11-03 Task 2.
- **Phase 12 (Teams & Organizations):** Not scheduled. Depends on Phase 11 fully green. Do not schedule until all 9 Phase 11 SCs verify.

---
*Phase: 11-deployment*
*Tasks 1 and 2 completed: 2026-04-24*
*Task 3 completion: PENDING human-verify gate (Sentry + UptimeRobot + synthetic-error + Modal smoke + rollback drill)*
*Overall plan status: NOT COMPLETE*

## Self-Check

- FOUND: backend/main.py (modified - traces_sampler wired)
- FOUND: docs/deploy.md (created)
- FOUND: .planning/ROADMAP.md (SC 6 + SC 8 corrected)
- FOUND: .planning/phases/11-deployment/11-05-SUMMARY.md (this file)
- FOUND commit: bca945b (Task 1)
- FOUND commit: 84bdc85 (Task 2)
- VERIFIED: grep -c "traces_sampler" backend/main.py = 3 (>= 2)
- VERIFIED: grep -c "_HOT_PATHS" backend/main.py = 2
- VERIFIED: all 5 D-14 hot paths present in backend/main.py
- VERIFIED: python -c "import main" EXIT 0
- VERIFIED: grep -c "WEBHOOK_HMAC_SECRET_PREV" docs/deploy.md = 3
- VERIFIED: grep -c "openssl rand -hex 32" docs/deploy.md = 2
- VERIFIED: grep -c "rollback_drill.sh" docs/deploy.md = 2
- VERIFIED: grep -c "supabase db push" docs/deploy.md = 1
- VERIFIED: grep -cE "pooler.supabase.com:6543|Supavisor transaction pooler" docs/deploy.md = 1
- VERIFIED: grep -c "Pitfall 5" docs/deploy.md = 1
- VERIFIED: grep -c "UptimeRobot" docs/deploy.md = 1
- VERIFIED: grep -cE "POST /agent/message|POST /jobs/launch|POST /webhooks/runpod|POST /webhooks/heartbeat|POST /jobs/\{job_id\}/upload-urls" docs/deploy.md = 5
- VERIFIED: grep -ci "Modal.*primary GPU provider" .planning/ROADMAP.md = 1
- VERIFIED: grep -ci "RunPod.*quarantined" .planning/ROADMAP.md = 1
- VERIFIED: grep -cE "UptimeRobot.*Slack|Sentry.*UptimeRobot" .planning/ROADMAP.md = 2
- VERIFIED: grep -c "RunPod from production backend" .planning/ROADMAP.md = 0
- VERIFIED: grep -c "uptime monitoring with PagerDuty/Opsgenie alerting" .planning/ROADMAP.md = 0
- VERIFIED: docs/deploy.md contains zero emoji characters (Python regex scan EXIT 0)
- VERIFIED: 11-05-SUMMARY.md contains zero emoji characters (scan below)
- NOT APPLICABLE (Task 3 pending external human-verify): Sentry projects, UptimeRobot monitors, synthetic error + Slack routing, Modal smoke, rollback wall-clock
