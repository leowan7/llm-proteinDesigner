---
phase: 06-ui-improvements
plan: 02
subsystem: api
tags: [fastapi, asyncpg, stripe, postgres, pagination]

requires:
  - phase: 03-job-execution-frontend-and-billing
    provides: billing/router.py with _resolve_stripe_customer helper, stripe_client.py
  - phase: 04-pipeline-validation
    provides: jobs/router.py with job endpoints

provides:
  - GET /jobs with keyset pagination (cursor-based on created_at) and status filter
  - GET /user/usage (monthly billing summary with recent_charges list)
  - GET /user/settings (email, display_name, notification_preferences)
  - PUT /user/settings (update display_name and notification_preferences)
  - GET /billing/payment-method (Stripe card brand/last4/exp_month/exp_year)

affects: [06-ui-improvements, frontend job history page, frontend settings page]

tech-stack:
  added: []
  patterns:
    - Keyset pagination via created_at cursor (before param) — no OFFSET
    - ALLOWED_STATUS_FILTERS constant restricts filter values to D-17 spec
    - Defensive NULL handling for new user columns (display_name, notification_preferences)
    - stripe.Customer.retrieve with expand parameter for payment method details

key-files:
  created:
    - backend/user/__init__.py
    - backend/user/router.py
  modified:
    - backend/jobs/router.py
    - backend/billing/router.py
    - backend/main.py

key-decisions:
  - "Rate limiting not added — no slowapi/rate-limiting library in requirements.txt; consistent with all existing endpoints which also lack rate limiting"
  - "user/router.py handles NULL display_name and notification_preferences defensively — columns added by Plan 01 migration which runs in parallel"
  - "stripe import added directly to billing/router.py for get_payment_method — stripe.Customer.retrieve used directly rather than adding a new function to stripe_client.py"
  - "ALLOWED_STATUS_FILTERS = {'running', 'complete', 'failed'} per D-17 spec — 'cancelled' and 'queued' deliberately excluded"

patterns-established:
  - "Keyset pagination: query uses AND ($3::timestamptz IS NULL OR created_at < $3) cursor pattern; has_more = len(rows) == limit"
  - "Status validation: check against a set constant, return 400 with sorted list of valid values for actionable error message"

requirements-completed: [UI-03, UI-04]

duration: 12min
completed: 2026-04-07
---

# Phase 06 Plan 02: Backend API Endpoints for Job History and Settings Summary

**Four new authenticated FastAPI endpoints delivering job history pagination, monthly billing summary, user settings CRUD, and Stripe payment method retrieval for the Job History and Settings pages.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-04-07T00:00:00Z
- **Completed:** 2026-04-07T00:12:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Replaced the naive 50-row `list_jobs` endpoint with a keyset-paginated version supporting status filter (running/complete/failed per D-17), created_at cursor, configurable limit (1-100), and `has_more` flag
- Created `backend/user/` module with GET/PUT `/user/settings` (display_name + notification_preferences) and GET `/user/usage` (monthly job count + total spend + recent_charges list)
- Added GET `/billing/payment-method` to existing billing router, retrieving Stripe card brand/last4/expiry via `stripe.Customer.retrieve` with expand

## Task Commits

1. **Task 1: Jobs list endpoint with keyset pagination and status filter** - `29d49b9` (feat)
2. **Task 2: User usage, notification settings, and payment method endpoints** - `deb012f` (feat)

## Files Created/Modified

- `backend/jobs/router.py` - Replaced simple list with paginated GET /jobs; added ALLOWED_STATUS_FILTERS, cursor param, has_more
- `backend/user/__init__.py` - New module init (empty)
- `backend/user/router.py` - New: GET /user/usage, GET /user/settings, PUT /user/settings
- `backend/billing/router.py` - Added GET /billing/payment-method; removed unused `settings` import; added `stripe` import
- `backend/main.py` - Registered user_router

## Decisions Made

- Rate limiting decorators (`@limiter.limit`) omitted — `slowapi` is not in requirements.txt and no existing endpoint in the project uses rate limiting; adding it would require architectural changes (Rule 4 boundary). Noted as future hardening work.
- Handled NULL `display_name` and `notification_preferences` defensively since these columns are added by Plan 01's migration running in a parallel worktree.
- Used `stripe.Customer.retrieve` directly in `billing/router.py` rather than adding a helper to `stripe_client.py` — the retrieval is endpoint-specific and does not warrant a shared utility function.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed unused `settings` import from billing/router.py**
- **Found during:** Task 2 (adding get_payment_method)
- **Issue:** IDE flagged `settings` as unused import (pre-existing but confirmed unused)
- **Fix:** Removed `from config import settings`; added `import stripe` needed by get_payment_method
- **Files modified:** backend/billing/router.py
- **Committed in:** deb012f (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug/cleanup)
**Impact on plan:** Minimal — import cleanup only. Rate limiting omission is documented; no scope creep.

## Issues Encountered

- Rate limiting: Plan specified `@limiter.limit("30/minute")` but the project has no rate-limiting library installed and no existing endpoints use it. Implementing it would require adding `slowapi` to requirements.txt and wiring it as middleware — an architectural addition beyond this plan's scope. Deferred.

## Next Phase Readiness

- GET /jobs, GET /user/usage, GET /user/settings, PUT /user/settings, GET /billing/payment-method are all live and registered
- Frontend Job History page (D-16 through D-20) can consume GET /jobs immediately
- Frontend Settings page (D-10 through D-15) can consume user/settings endpoints
- Payment method display on Settings page can consume GET /billing/payment-method
- Note: display_name and notification_preferences columns must exist in the DB (added by Plan 01 migration) before settings endpoints return real data

---
*Phase: 06-ui-improvements*
*Completed: 2026-04-07*
