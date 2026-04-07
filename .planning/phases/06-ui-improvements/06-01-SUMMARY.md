---
phase: 06-ui-improvements
plan: 01
subsystem: database
tags: [postgres, asyncpg, fastapi, sessions, anthropic, redis-removal]

# Dependency graph
requires:
  - phase: 02-agent-and-structure-input
    provides: agent/router.py with Redis session_manager, agent/session.py SessionManager
  - phase: 01-foundation
    provides: db/connection.py asyncpg pool, users/jobs schema, auth dependencies

provides:
  - sessions table with agent_history JSONB and RLS
  - session_messages table for user-visible message history with RLS
  - session_id FK on jobs table
  - display_name and notification_preferences columns on users table
  - sessions/queries.py with 8 async CRUD functions
  - sessions/router.py with 6 REST endpoints at /sessions
  - agent router migrated from Redis to PostgreSQL
  - async title generation via claude-haiku-4-5-20251001 after first message

affects: [06-02, 06-03, 06-04, 06-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - asyncpg pool.fetchrow/fetch/execute for all DB operations (no ORM)
    - keyset pagination via updated_at cursor (not offset)
    - agent_history JSONB stores full Anthropic messages array; session_messages stores user-visible rows only
    - asyncio.create_task for fire-and-forget background AI calls
    - loop.run_in_executor wraps synchronous Anthropic SDK calls in async context

key-files:
  created:
    - supabase/migrations/20260408000001_session_persistence.sql
    - backend/sessions/__init__.py
    - backend/sessions/queries.py
    - backend/sessions/router.py
  modified:
    - backend/agent/router.py
    - backend/main.py

key-decisions:
  - "agent_history JSONB stores full Anthropic messages array (tool_use/tool_result blocks); session_messages stores user-visible messages only (D-09 pattern)"
  - "Title generation uses asyncio.create_task + loop.run_in_executor to avoid blocking SSE stream with synchronous Anthropic SDK call"
  - "Keyset pagination on sessions list uses updated_at cursor to prevent offset drift as sessions are updated"
  - "user_sort == 0 check triggers title generation only on first message (sort_order returned by append_message)"

patterns-established:
  - "Session queries: all DB calls use get_db_pool() directly, no ORM layer"
  - "Ownership enforcement: WHERE user_id = $N in all session queries (defense-in-depth alongside RLS)"
  - "Background AI tasks: asyncio.create_task + run_in_executor for sync Anthropic client in async context"

requirements-completed: [UI-01]

# Metrics
duration: 15min
completed: 2026-04-07
---

# Phase 06 Plan 01: Session Persistence Summary

**PostgreSQL-backed session storage replacing Redis ephemeral sessions — agent_history JSONB + session_messages table with RLS, 6-endpoint CRUD API, and async Haiku title generation on first message**

## Performance

- **Duration:** 15 min
- **Started:** 2026-04-07T15:00:00Z
- **Completed:** 2026-04-07T15:13:12Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Created sessions and session_messages Postgres tables with RLS policies, session_id FK on jobs, and display_name/notification_preferences columns on users
- Implemented 8 async query functions in sessions/queries.py covering full CRUD plus agent_history read/write and message append with sort_order
- Migrated agent/router.py from Redis session_manager to PostgreSQL — sessions now survive page refresh with no 1hr TTL expiry
- Added sessions/router.py with 6 REST endpoints including generate-title via claude-haiku-4-5-20251001 as fire-and-forget async task

## Task Commits

Each task was committed atomically:

1. **Task 1: Database migration and session query module** - `d576b0b` (feat)
2. **Task 2: Session CRUD router + agent router migration to PostgreSQL** - `0e6c0ae` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `supabase/migrations/20260408000001_session_persistence.sql` - sessions, session_messages tables + RLS, jobs.session_id FK, users.display_name + notification_preferences
- `backend/sessions/__init__.py` - package init (empty)
- `backend/sessions/queries.py` - 8 async asyncpg query functions for session CRUD and agent history
- `backend/sessions/router.py` - FastAPI router with 6 endpoints at /sessions prefix
- `backend/agent/router.py` - Migrated from Redis session_manager to PostgreSQL; removed create/delete session endpoints; added background title generation
- `backend/main.py` - Registered sessions_router

## Decisions Made

- **agent_history vs session_messages split (D-09):** agent_history JSONB on sessions table stores the full Anthropic messages array including tool_use/tool_result blocks needed for context reconstruction. session_messages table stores one row per user message and one row per assistant text response for sidebar display — these are the only rows the UI needs to render history.
- **Title generation timing:** user_sort == 0 (returned by append_message) signals the first message in a session. asyncio.create_task fires the background Haiku call without blocking the SSE stream. loop.run_in_executor wraps the synchronous Anthropic SDK call to avoid event loop blocking.
- **Keyset pagination:** list_sessions uses updated_at cursor rather than OFFSET to avoid drift as active sessions are bumped to the top by append_message.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. The migration file `supabase/migrations/20260408000001_session_persistence.sql` will be applied by `supabase db reset` or `supabase migration up` as part of normal local dev setup.

## Next Phase Readiness

- PostgreSQL session persistence foundation is complete; Plan 06-02 can implement sidebar session history using GET /sessions and GET /sessions/{id}
- sessions.agent_history enables full conversation reconstruction on resume (D-07)
- jobs.session_id FK is ready for linking new jobs to their originating session
- users.display_name column is ready for the settings page (Plan 06-05)

---
*Phase: 06-ui-improvements*
*Completed: 2026-04-07*
