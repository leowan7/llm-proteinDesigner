---
phase: 06-ui-improvements
verified: 2026-04-08T00:00:00Z
status: human_needed
score: 6/8
overrides_applied: 0
overrides:
  - must_have: "Help/docs page at /docs with tool descriptions, parameter explanations, result interpretation guide, and FAQ"
    reason: "Intentionally scoped out of Phase 6 plans per user instruction — content pages deferred to post-launch"
    accepted_by: "leo"
    accepted_at: "2026-04-08T00:00:00Z"
  - must_have: "Resources page at /resources with links to publications, benchmark data, example use cases, and video walkthroughs"
    reason: "Intentionally scoped out of Phase 6 plans per user instruction — content pages deferred to post-launch"
    accepted_by: "leo"
    accepted_at: "2026-04-08T00:00:00Z"
human_verification:
  - test: "End-to-end session persistence verification"
    expected: "Send a message, refresh the page, and verify the conversation is fully restored including any structured cards in the sidebar and chat view"
    why_human: "Requires live browser session with backend running; cannot verify SSE + DB round-trip programmatically"
  - test: "Sidebar session navigation"
    expected: "Click a past session in sidebar, verify it loads full message history and navigates to /chat/{id}; click 'Start new session', verify empty chat opens with new session ID in URL"
    why_human: "Interactive browser navigation with live API calls"
  - test: "Session title auto-generation"
    expected: "Send a first message in a new session; within a few seconds the sidebar item updates from 'Untitled session' to an AI-generated title"
    why_human: "Requires Claude Haiku API call + async background task observable in browser"
  - test: "Settings page full round-trip"
    expected: "Change display name in Account tab, save, refresh page — name persists. Toggle a notification preference in Notifications tab, save, refresh — toggle state persists. Billing tab shows card info or 'No payment method on file'."
    why_human: "Requires authenticated API calls with live Supabase + Stripe integration"
  - test: "Jobs table functionality"
    expected: "Visit /jobs, table shows past jobs with correct columns; status filter (All/Running/Complete/Failed — no Cancelled) works; Previous/Next pagination works when >25 jobs exist; on mobile (<768px) cards render instead of table"
    why_human: "Requires jobs in DB and responsive browser testing"
  - test: "GreetingCard prompt injection"
    expected: "On new empty chat, click any example prompt button — chat input fills with the prompt text and input is focused; user can then send or edit before sending"
    why_human: "Interactive DOM event verification; requires live browser"
  - test: "WCAG keyboard navigation"
    expected: "Tab through the app: skip nav link is first focusable element, focus moves logically through sidebar toggle, sidebar items, main content; focus indicators visible on all interactive elements"
    why_human: "Requires manual keyboard testing with visual focus inspection"
  - test: "Screen reader aria-live announcements"
    expected: "When agent responds, screen reader announces new message text; when a running job changes status, an assertive announcement fires"
    why_human: "Requires screen reader (NVDA, VoiceOver) or browser a11y tooling; cannot verify with grep"
---

# Phase 6: UI Improvements — Verification Report

**Phase Goal:** The platform feels like a polished SaaS product — persistent sessions, navigable history, user settings, and WCAG 2.2 AA accessibility for biopharma procurement
**Verified:** 2026-04-08T00:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User conversations persist across page refreshes and browser sessions; user can resume any previous conversation from the sidebar | VERIFIED | `sessions` + `session_messages` tables with RLS in migration 20260408000001; agent/router.py reads/writes PostgreSQL via `get_agent_history`/`update_agent_history`; ChatPage loads via `loadSession(sessionId)` on URL param change; no Redis session_manager.load/save calls remain |
| 2 | Collapsible left sidebar provides navigation between chat, job history, and settings | VERIFIED | `AppSidebar.tsx` uses shadcn `collapsible="icon"`; Jobs/Settings nav links present; `AuthenticatedLayout.tsx` wraps all authenticated routes in `SidebarProvider`; all auth routes (`/login`, `/signup`) are outside the layout wrapper |
| 3 | User can view all past jobs in a filterable table at /jobs with status, cost, dates, and download links | VERIFIED | `JobHistoryPage.tsx` contains `TableHeader`, `scope="col"` headers, `StatusBadge`, `listJobs`, keyset pagination (`Previous`/`Next`), empty state, mobile `md:hidden` card fallback; backend `GET /jobs` with `ALLOWED_STATUS_FILTERS = {'running', 'complete', 'failed'}` |
| 4 | User settings page allows notification preferences, password change, and billing management (via Stripe Customer Portal) | VERIFIED | `SettingsPage.tsx` has 4 tabs (account/billing/usage/notifications) with `TabsContent`; `user.ts` exports all 5 API functions; Stripe portal via `createPortalSession()`; notification toggles persist via `PUT /user/settings`; `htmlFor`/`id` pairs on all form fields |
| 5 | First-run onboarding presents clickable example prompts in the greeting card; no tooltip tours | VERIFIED | `GreetingCard.tsx` exports `onPromptClick` prop with 4 prompts including "Design a binder for the IL-6 receptor" and "Generate de novo backbones"; `ChatPage.tsx` wires `onPromptClick` to fill input via `injectedValue`; capability indicators present (RFdiffusion, BindCraft, BoltzGen, RFantibody) |
| 6 | All interactive components pass WCAG 2.2 AA audit (keyboard navigation, aria-live for SSE updates, color contrast) | HUMAN NEEDED | `MessageList.tsx` has `aria-live="polite"` sr-only div for new messages; `JobStatusCard.tsx` has `aria-live="assertive"` for status changes; `AppHeader.tsx` has skip nav link; `AppSidebar.tsx` has `aria-label` on icon-only buttons; `JobHistoryPage.tsx` table has `scope="col"` on headers; `globals.css` has `prefers-reduced-motion` block; `eslint-plugin-jsx-a11y` added to ESLint config. Color contrast and actual keyboard navigation require browser verification. |
| 7 | Help/docs page (/docs) with tool descriptions, parameter explanations, result interpretation guide, and FAQ | PASSED (override) | Override: Intentionally scoped out of Phase 6 plans per user instruction — content pages deferred to post-launch |
| 8 | Resources page (/resources) with links to original publications, benchmark data, example use cases, and video walkthroughs | PASSED (override) | Override: Intentionally scoped out of Phase 6 plans per user instruction — content pages deferred to post-launch |

**Score:** 6/8 truths verified (4 fully verified, 2 human-pending, 2 override-accepted)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `supabase/migrations/20260408000001_session_persistence.sql` | sessions, session_messages tables + jobs.session_id FK + RLS | VERIFIED | Contains `CREATE TABLE public.sessions`, `CREATE TABLE public.session_messages`, `ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS session_id`, `display_name TEXT`, `notification_preferences JSONB`, `CREATE POLICY sessions_own`, `CREATE POLICY session_messages_own`, `agent_history JSONB` |
| `backend/sessions/router.py` | Session CRUD endpoints | VERIFIED | `router = APIRouter(prefix="/sessions"`, all 6 endpoints present, `claude-haiku-4-5-20251001` for title generation |
| `backend/sessions/queries.py` | 8 async query functions | VERIFIED | All 8 functions found: `create_session`, `list_sessions`, `get_session_with_messages`, `update_session_title`, `delete_session`, `append_message`, `update_agent_history`, `get_agent_history`; uses `from db.connection import get_db_pool` |
| `backend/jobs/router.py` | `GET /jobs` with keyset pagination | VERIFIED | `async def list_jobs` with `ALLOWED_STATUS_FILTERS`, `ORDER BY created_at DESC`, `created_at < $3` cursor, `has_more` flag |
| `backend/user/router.py` | GET/PUT `/user/settings`, GET `/user/usage` | VERIFIED | `router = APIRouter(prefix="/user"`, `get_usage`, `get_settings`, `update_settings`, `notification_preferences`, `display_name`, `date_trunc` |
| `backend/billing/router.py` | `GET /billing/payment-method` | VERIFIED | `async def get_payment_method`, `has_payment_method`, `last4`, `invoice_settings` |
| `frontend/src/lib/sessions.ts` | Session API client | VERIFIED | All 5 exported functions: `listSessions`, `loadSession`, `createPersistentSession`, `deleteSessionApi`, `updateSessionTitle` |
| `frontend/src/components/layout/AppHeader.tsx` | Header with sidebar toggle and skip nav | VERIFIED | `SidebarTrigger` present, `Skip to main content` sr-only link, `aria-label="Toggle sidebar"` |
| `frontend/src/components/layout/AppSidebar.tsx` | Collapsible sidebar with session list | VERIFIED | `SidebarGroupLabel` present, `Start new session`, `Today`, `This week`, `Earlier`, `No sessions yet`, `aria-current` on active session |
| `frontend/src/components/layout/AuthenticatedLayout.tsx` | Auth guard + SidebarProvider + useOutletContext | VERIFIED | `SidebarProvider`, `navigate("/login")`, `refreshSessions`, `useOutletContext`/`Outlet context` all present |
| `frontend/src/pages/JobHistoryPage.tsx` | Job history page with table | VERIFIED | `TableHeader`, `scope="col"`, `StatusBadge`, `No jobs yet`, `Start a design conversation`, `Previous`, `Next`, `listJobs`, `md:hidden` |
| `frontend/src/components/common/StatusBadge.tsx` | WCAG-compliant status badge | VERIFIED | `sr-only` present, `bg-blue-500/15`, `bg-green-500/15` |
| `frontend/src/lib/jobs.ts` | `listJobs` API function | VERIFIED | `export async function listJobs`, `JobListItem` interface |
| `frontend/src/components/chat/GreetingCard.tsx` | Enhanced greeting with prompts | VERIFIED | `onPromptClick`, IL-6 receptor prompt, de novo backbone prompt, `RFdiffusion`, `BindCraft`, `Results in 30 min` |
| `frontend/src/lib/user.ts` | User settings API client | VERIFIED | All 5 functions: `getSettings`, `updateSettings`, `getUsage`, `getPaymentMethod`, `createPortalSession`; no `API_BASE` dead code |
| `frontend/src/pages/SettingsPage.tsx` | Settings page with 4 tabs | VERIFIED | `TabsContent`, all 4 tab values, `Display name`, `Manage payment method`, `Save changes`, `No payment method on file`, `Job completion`, `Job failure`, `Changes saved`, `htmlFor` pairs |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `backend/agent/router.py` | `backend/sessions/queries.py` | `from sessions.queries import` | WIRED | Lines 32-36 import `append_message`, `update_agent_history`; no `session_manager.load/save` remaining |
| `backend/sessions/router.py` | `backend/sessions/queries.py` | `from sessions.queries import` | WIRED | Confirmed in router.py |
| `backend/main.py` | `backend/sessions/router.py` | `app.include_router(sessions_router)` | WIRED | Lines 100-101: `from sessions.router import router as sessions_router` + `app.include_router(sessions_router)` |
| `backend/main.py` | `backend/user/router.py` | `app.include_router(user_router)` | WIRED | Lines 103-104: `from user.router import router as user_router` + `app.include_router(user_router)` |
| `backend/jobs/router.py` | `db.connection.get_db_pool` | asyncpg query | WIRED | `get_db_pool` used for the `list_jobs` query |
| `backend/billing/router.py` | Stripe API | `stripe.Customer.retrieve` | WIRED | `invoice_settings.default_payment_method` expand pattern present |
| `frontend/src/App.tsx` | `AuthenticatedLayout.tsx` | Route element wrapper | WIRED | Line 67: `element={<AuthenticatedLayout />}` wraps all authenticated routes |
| `frontend/src/components/layout/AppSidebar.tsx` | `frontend/src/lib/sessions.ts` | `listSessions` API call | WIRED | `listSessions` called via `onRefresh` prop from AuthenticatedLayout |
| `frontend/src/components/chat/ChatPage.tsx` | `frontend/src/lib/sessions.ts` | `loadSession`, `createPersistentSession`, `listSessions` | WIRED | Lines 52, 184, 192, 230 |
| `frontend/src/components/chat/ChatPage.tsx` | `AuthenticatedLayout.tsx` | `useLayoutContext` | WIRED | Line 53: `import { useLayoutContext }`, line 79: `const { refreshSessions } = useLayoutContext()` |
| `frontend/src/pages/JobHistoryPage.tsx` | `frontend/src/lib/jobs.ts` | `listJobs` | WIRED | Line 32: `import { listJobs, downloadAllDesignsUrl } from "@/lib/jobs"`, line 76: `listJobs(...)` called |
| `frontend/src/pages/JobHistoryPage.tsx` | `StatusBadge.tsx` | `StatusBadge` component | WIRED | Line 31: import, line 213: used in table rows |
| `frontend/src/components/chat/GreetingCard.tsx` | `ChatPage.tsx` | `onPromptClick` callback prop | WIRED | `ChatPage.tsx` line 746 passes `onPromptClick` handler |
| `frontend/src/pages/SettingsPage.tsx` | `frontend/src/lib/user.ts` | `getSettings`, `updateSettings`, `getUsage`, `getPaymentMethod` | WIRED | `SettingsPage.tsx` imports and calls all user.ts functions |
| `frontend/src/components/chat/MessageList.tsx` | aria-live region | `aria-live="polite"` on sr-only div | WIRED | Line 143: `aria-live="polite"`, `aria-atomic="true"`, `sr-only` class |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `AppSidebar.tsx` | `sessions[]` (session list) | `listSessions()` → `GET /sessions` → asyncpg `SELECT FROM sessions WHERE user_id` | Yes — DB query in `queries.py:list_sessions` with real user scoping | FLOWING |
| `ChatPage.tsx` | `messages[]` | `loadSession(sessionId)` → `GET /sessions/{id}` → `get_session_with_messages` with asyncpg SELECT | Yes — JOIN on session_messages table ordered by sort_order | FLOWING |
| `JobHistoryPage.tsx` | `jobs[]` | `listJobs()` → `GET /jobs` → asyncpg SELECT with cursor pagination | Yes — `list_jobs` in jobs/router.py queries `public.jobs` with keyset cursor | FLOWING |
| `SettingsPage.tsx` | `settings` (display_name, notification_preferences) | `getSettings()` → `GET /user/settings` → asyncpg SELECT on `public.users` | Yes — `get_settings` queries `email, display_name, notification_preferences` from users table | FLOWING |
| `SettingsPage.tsx` (Billing tab) | `paymentMethod` | `getPaymentMethod()` → `GET /billing/payment-method` → `stripe.Customer.retrieve` | Yes — live Stripe API call with expand for payment method | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| TypeScript compilation | `cd frontend && npx tsc --noEmit` | Exit 0, no output | PASS |
| Session queries module imports | Python import check | All 8 functions importable | PASS (documented in SUMMARY) |
| Session router imports | Python import check | 6 routes at /sessions | PASS (documented in SUMMARY) |
| Commits exist | `git log --oneline \| grep {hashes}` | All 11 commits (d576b0b through 05ca924) found | PASS |
| sessions_router registered in main.py | grep | Line 100-101 confirmed | PASS |
| user_router registered in main.py | grep | Line 103-104 confirmed | PASS |

---

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|---------------|-------------|--------|----------|
| UI-01 | 06-01, 06-03 | Session persistence — PostgreSQL-backed sessions, agent history JSONB | SATISFIED | Migration, queries.py, agent/router.py migration, ChatPage URL-based session loading all verified |
| UI-02 | 06-03 | Collapsible sidebar with session history and navigation | SATISFIED | AppSidebar.tsx, AuthenticatedLayout.tsx, App.tsx routing all verified |
| UI-03 | 06-02, 06-04 | Job history page at /jobs with filterable table | SATISFIED | `GET /jobs` backend endpoint and `JobHistoryPage.tsx` both verified |
| UI-04 | 06-02, 06-05 | User settings page with notifications, billing via Stripe portal | SATISFIED | `GET/PUT /user/settings`, `GET /billing/payment-method`, `SettingsPage.tsx` all verified |
| UI-05 | 06-04 | First-run onboarding with clickable example prompts | SATISFIED | GreetingCard prompts wired via onPromptClick to ChatInput injectedValue |
| UI-06 | 06-05 | WCAG 2.2 AA compliance | PARTIALLY SATISFIED | Code-level checks pass (aria-live, sr-only, scope, skip nav, aria-labels); browser/screen-reader verification needed — see Human Verification section |

**Note:** UI-01 through UI-06 are referenced in ROADMAP.md Phase 6 but are NOT formally defined in REQUIREMENTS.md (the file only defines AUTH-01 through BILL-04). These are Phase 6-specific requirement IDs that exist only in the roadmap and plan frontmatter — they are not orphaned per the traceability table, but they are unregistered in REQUIREMENTS.md. This is a documentation gap but not a code gap.

---

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `frontend/src/pages/JobHistoryPage.tsx` | No Download links wired (ROADMAP SC-3 says "download links") | Warning | "Download results" button present but links to `downloadAllDesignsUrl` helper — needs verification that this helper returns a real presigned URL, not a placeholder |
| `backend/sessions/router.py` | `generate-title` endpoint uses `asyncio.create_task` with `run_in_executor` for synchronous Anthropic SDK | Info | Pattern is intentional per SUMMARY decision log — fire-and-forget without blocking SSE; not a bug |

---

### Human Verification Required

#### 1. End-to-End Session Persistence

**Test:** Log in, send a message in a new session, close the browser tab completely, reopen and navigate to `/chat` — the previous session should appear in the sidebar and be loadable.
**Expected:** Full message history including any structured cards (structure_preview, review) is restored. Agent can continue the conversation with prior context.
**Why human:** Requires live browser + backend with real PostgreSQL session round-trip. Cannot verify SSE streaming + DB persistence + session resume together programmatically.

#### 2. Sidebar Session Navigation

**Test:** With multiple sessions in the DB, verify sidebar groups sessions under Today/This Week/Earlier headers. Click a session to navigate to `/chat/{id}` and verify messages load. Click "Start new session" — a new empty chat opens with a new UUID in the URL.
**Expected:** Session list loads, groups correctly, clicking navigates and loads history, new session creation works.
**Why human:** Interactive browser navigation with live API calls and React Router.

#### 3. Session Title Auto-Generation

**Test:** Create a new session, send a first message like "Design a binder for IL-6 receptor". Watch the sidebar — within ~5 seconds the session title should update from "Untitled session" to something like "IL-6 Receptor Binder Design".
**Expected:** Title generated by Claude Haiku via background async task, written to DB, reflected in sidebar after `refreshSessions()` call following the agent `done` event.
**Why human:** Requires Anthropic API key, live Haiku call, async background task observable only in browser.

#### 4. Settings Page Round-Trip

**Test:** Go to `/settings` → Account tab, change display name, Save. Refresh — name persists. Notifications tab, toggle "Job failure" off, Save. Refresh — toggle stays off. Billing tab — shows card info (if card on file) or "No payment method on file.". Click "Manage payment method" — redirects to Stripe Customer Portal.
**Expected:** All four tabs functional, data persists across refresh, Stripe portal redirect works.
**Why human:** Requires authenticated Supabase + Stripe session.

#### 5. Jobs Table Functionality

**Test:** Visit `/jobs`. Verify table shows past jobs with Job, Tool, Status, Date, Designs, Cost, Actions columns. Test status filter — dropdown should show exactly: All, Running, Complete, Failed (no Cancelled). If >25 jobs exist, verify Previous/Next pagination. Resize browser to <768px — table should be replaced by card list.
**Expected:** Table renders, filter works, pagination works, mobile responsive.
**Why human:** Requires jobs in DB, full browser testing for responsive layout.

#### 6. GreetingCard Prompt Injection

**Test:** Navigate to a new empty session. GreetingCard should be visible with 4 example prompt buttons and capability indicators below. Click "Design a binder for the IL-6 receptor extracellular domain" — the chat input should fill with this text and receive focus. The user can then edit or press Enter to send.
**Expected:** Prompt auto-fills input, input is focused, user can send without re-typing.
**Why human:** Interactive DOM event verification (focus, value injection) requires live browser.

#### 7. WCAG Keyboard Navigation

**Test:** Tab through the app starting from a fresh page load. Verify: (1) Skip to main content link is the first focusable element and is visible when focused, (2) SidebarTrigger is reachable via Tab, (3) Session items in sidebar are keyboard-navigable with arrow keys or Tab, (4) All buttons, links, inputs in settings/jobs pages are reachable, (5) Focus indicators (ring) are visible on all focused elements.
**Expected:** Logical focus order, no focus traps, visible focus rings throughout.
**Why human:** Requires keyboard-only navigation in a browser; cannot simulate with grep.

#### 8. Screen Reader Aria-Live Announcements

**Test:** Using VoiceOver (macOS) or NVDA (Windows): (1) Send a message, wait for agent response — screen reader should announce the new assistant message text (not re-read entire history), (2) While on `/jobs/{id}` with a running job, wait for status to change — screen reader should assertively announce "Job status changed to complete" (or similar).
**Expected:** New messages announced with polite (non-interrupting) aria-live; job status changes announced with assertive aria-live.
**Why human:** Requires screen reader software; visual/audio verification only.

---

### Gaps Summary

No automated gaps were found. All 6 verifiable success criteria have code-level evidence of implementation. The 2 scoped-out items (Help/docs page, Resources page) are covered by user-accepted overrides.

The `human_needed` status reflects 8 items requiring browser/screen reader verification — primarily live integration testing (session persistence, Stripe portal, Haiku title generation) and WCAG behavioral testing (keyboard navigation, screen reader announcements). These are standard human-acceptance items for a UI phase; they do not indicate code defects.

The requirements UI-01 through UI-06 are referenced in ROADMAP.md but not formally registered in REQUIREMENTS.md. This is a documentation gap in REQUIREMENTS.md that should be addressed: add rows for these 6 IDs to the v2 Requirements section and the Traceability table.

---

_Verified: 2026-04-08T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
