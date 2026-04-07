# Phase 6: UI Improvements — Research

**Researched:** 2026-04-07
**Domain:** React/TypeScript SaaS frontend — session persistence, sidebar navigation, settings page, job history, onboarding, WCAG 2.2 AA
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Sidebar & Navigation**
- D-01: Collapsible left sidebar, always visible on desktop by default. shadcn Sidebar handles responsive behavior (overlay on tablet, sheet on mobile).
- D-02: Sidebar contents top-to-bottom: New Session button, session history list grouped by date, separator, Jobs and Settings nav links, user section at bottom (avatar/initials, name, logout).
- D-03: UserMenu moves from header to sidebar footer. Header becomes: logo + sidebar toggle (left), optional session title (center).
- D-04: No Help/Docs link in sidebar at launch.

**Session Persistence**
- D-05: Sessions stored in PostgreSQL (`sessions` + `session_messages` tables). Replace Redis-based ephemeral sessions.
- D-06: Session titles auto-generated via Claude Haiku from first user message. Titles are editable.
- D-07: Full session resume: clicking a past session restores complete message history including structured cards; user can continue the conversation.
- D-08: Sessions grouped by date: Today, This Week, Earlier. No folders at v1.
- D-09: Session-to-job linking via FK on jobs table (`session_id`).

**Settings & Billing UI**
- D-10: Settings page at `/settings` with four tabs: Account, Billing, Usage, Notifications (shadcn Tabs).
- D-11: Account tab: name, email (read-only), password change. No profile photo.
- D-12: Billing tab: card last-4 and expiry, "Manage payment method" link to Stripe Customer Portal only.
- D-13: Usage tab: billing period spend, job count, recent charges table, link to Stripe invoice history.
- D-14: Notifications tab: email notification toggles for job completion and failure. Default both on.
- D-15: Stripe Customer Portal for all billing management. Zero custom payment UI.

**Job History Page**
- D-16: `/jobs` table. Columns: title, tool, status badge, date (relative + hover full), designs count, GPU cost, actions.
- D-17: Status filter dropdown: All, Running, Complete, Failed. Tool filter deferred.
- D-18: Server-side pagination, 25 jobs per page, Previous/Next.
- D-19: Mobile: table degrades to card-based list.
- D-20: Empty state: "No jobs yet. Start a design conversation to launch your first job." with link to chat.

**Onboarding**
- D-21: Enhanced GreetingCard with 3-4 clickable example prompts that auto-fill chat input.
- D-22: Subtle capability indicators below prompts.
- D-23: One-time post-first-job completion message teaching navigation.

**Accessibility**
- D-24: WCAG 2.2 AA baseline: skip nav, aria-live for chat and SSE, heading hierarchy, contrast, keyboard audit.
- D-25: Status badges must not rely on color alone — visually hidden text.
- D-26: Score tables use semantic HTML `<table>` with `<th>` headers.
- D-27: Reduced motion support; eslint-plugin-jsx-a11y added to CI.

### Claude's Discretion
- Session list loading skeleton while sessions fetch
- Exact responsive breakpoints for sidebar collapse behavior
- Animation/transition details for sidebar open/close
- Pagination component design for job history
- Notification preferences storage mechanism (user metadata vs separate table)

### Deferred Ideas (OUT OF SCOPE)
- Help/Docs page (`/docs`) — content, not just placeholder link
- Resources page (`/resources`)
- Session search
- Theme toggle (light/dark)
- In-app toast notifications (Sonner)

</user_constraints>

---

## Summary

Phase 6 is a pure frontend phase with backend support work. The core deliverables are: (1) replace ephemeral Redis sessions with PostgreSQL-persisted sessions surfaced in a collapsible left sidebar, (2) build a `/jobs` history page with server-side paginated table, (3) build a `/settings` page with four tabbed sections, (4) enhance the GreetingCard with clickable example prompts, and (5) achieve WCAG 2.2 AA compliance.

The existing codebase is React + Vite + TypeScript, shadcn 4.x (base-nova style, neutral, dark-only), Radix UI, Tailwind v4, React Router, with a FastAPI backend backed by Supabase (PostgreSQL) and Redis. The current session system is entirely Redis-based and ephemeral — this is the largest backend change in the phase. The frontend refactor touches `App.tsx` (add sidebar wrapper to all authenticated routes), `ChatPage.tsx` (remove internal header+session management, consume persistent session API), and `GreetingCard.tsx` (add prompts). Three new pages are created from scratch: sidebar component, job history page, settings page.

**Primary recommendation:** Implement in five sequential waves: (1) DB migrations + backend session CRUD API, (2) sidebar + app shell restructure, (3) GreetingCard onboarding enhancement, (4) job history page, (5) settings page + accessibility audit.

---

## Standard Stack

### Core (already installed — confirmed from components.json and codebase)

| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| React | 18.x | UI framework | Already installed |
| TypeScript | 5.x | Type safety | Already installed |
| Vite | 5.x | Build/dev server | Already installed |
| React Router | 6.x | Client-side routing | Already in use |
| shadcn/ui | 4.x | Component system | base-nova / neutral preset |
| Radix UI | via shadcn | Accessible primitives | All interactive components |
| Tailwind CSS | v4 | Styling | CSS @theme inline strategy |
| lucide-react | current | Icons | Confirmed in components.json |

### shadcn Components to Install (Phase 6 additions)

```bash
npx shadcn add sidebar
npx shadcn add tabs
npx shadcn add table
npx shadcn add dialog
npx shadcn add progress
```

**Do NOT install yet:** Sonner — deferred to post-launch per D-04/deferred list.

### Supporting (backend, already installed)

| Library | Purpose | Notes |
|---------|---------|-------|
| FastAPI | API framework | Already in use |
| asyncpg | PostgreSQL async client | Already in use via db/connection.py |
| Supabase PostgreSQL | Persistent storage | Migrations via supabase CLI |
| Redis | SSE connection counting only | session storage moves to PostgreSQL |
| Anthropic SDK | Claude Haiku for title generation | Already in use |

### New Backend Dependencies (none)

No new Python packages required. Session persistence uses existing asyncpg pool. Title generation uses the existing Anthropic SDK (same client, different model string for Haiku).

### Accessibility Tooling (new to CI)

```bash
npm install --save-dev eslint-plugin-jsx-a11y
```

---

## Architecture Patterns

### App Shell Restructure

The current routing in `App.tsx` has no authenticated layout wrapper. All authenticated routes need to be wrapped in a sidebar shell. Pattern:

```typescript
// New: AuthenticatedLayout component wraps all non-auth routes
<SidebarProvider>
  <AppSidebar />      // new component
  <main>
    <AppHeader />     // slimmed header: logo + SidebarTrigger + session title
    <Outlet />        // ChatPage / JobHistoryPage / SettingsPage / JobPage
  </main>
</SidebarProvider>
```

`App.tsx` routing changes:
- Add `<Route element={<AuthenticatedLayout />}>` wrapper for `/`, `/chat`, `/jobs`, `/jobs/:id`, `/settings`
- Auth pages (`/login`, `/signup`, etc.) stay outside the wrapper

### Persistent Session Architecture

**Before (current):** Redis key `session:{user_id}:{session_id}` → `[]` with 1hr TTL. Frontend calls `createSession()` on mount, `deleteSession()` on "New Session". No persistence across refresh.

**After:** PostgreSQL `sessions` + `session_messages` tables. Backend session CRUD replaces `session.py` SessionManager for the persistent fields; Redis is kept only for the SSE connection counter (`sse_count:{user_id}` key).

**Session resume flow:**
1. User opens `/chat` — frontend calls `GET /sessions` to list sessions
2. Most recent session is auto-loaded (or new session created if none exist)
3. On resume: `GET /sessions/{id}` returns session with messages array including `cards` JSONB
4. Frontend reconstructs `messages` state and last `cards` from the response
5. Agent receives full prior messages array on next `/agent/message` call

**Session title generation:**
- Triggered async after the first user message in a session is saved
- `POST /sessions/{id}/generate-title` — backend calls Claude Haiku with the first user message
- Haiku prompt: "Generate a short title (max 8 words) for a protein design conversation that starts with: {message}"
- Title is written back to `sessions.title`; frontend polls for it or receives it on next session list fetch

### Database Schema (new migrations)

```sql
-- Migration: 20260408000001_session_persistence.sql
CREATE TABLE public.sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  title       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata    JSONB DEFAULT '{}'
);

CREATE TABLE public.session_messages (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id  UUID NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content     TEXT NOT NULL DEFAULT '',
  cards       JSONB,
  sort_order  INTEGER NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.jobs ADD COLUMN session_id UUID REFERENCES public.sessions(id);

-- RLS
ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.session_messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY sessions_own ON public.sessions
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY session_messages_own ON public.session_messages
  FOR ALL USING (
    session_id IN (SELECT id FROM public.sessions WHERE user_id = auth.uid())
  );
```

### Backend Endpoint Map (new endpoints required)

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/sessions` | GET | required | List sessions (sorted by updated_at desc, with pagination) |
| `/sessions` | POST | required | Create new persistent session |
| `/sessions/{id}` | GET | required | Get session + messages (for resume) |
| `/sessions/{id}` | PUT | required | Update session title (user edit) |
| `/sessions/{id}` | DELETE | required | Delete session + cascade messages |
| `/sessions/{id}/generate-title` | POST | required | Trigger Haiku title generation |
| `/jobs` | GET | required | List jobs (paginated, filterable by status) |
| `/user/usage` | GET | required | Billing period summary (job count + GPU spend) |
| `/billing/portal` | POST | required | Already exists — create Stripe Customer Portal session |
| `/billing/payment-method` | GET | required | Get current card last-4 and expiry from Stripe |

**Critical:** The existing `POST /agent/session` and `DELETE /agent/session/{id}` endpoints in `backend/agent/router.py` must be updated or replaced. The agent message handler currently calls `session_manager.load()` and `session_manager.save()` from the Redis-backed `SessionManager`. These calls must be migrated to read/write from PostgreSQL. The Redis `sse_count` logic stays as-is.

### ChatPage Refactor Strategy

`ChatPage.tsx` is the most complex file affected. Changes required:
1. Remove internal header markup (moves to `AppHeader` component)
2. Remove `createSession`/`deleteSession` imports from `lib/agent.ts`
3. Receive `sessionId` and initial `messages` as props (or from React Router state) rather than creating ephemeral sessions on mount
4. On "Start new session" (now in sidebar): navigate to `/chat` with no session ID, then create new persistent session
5. On session resume from sidebar: navigate to `/chat?session={id}` or `/chat/{id}`, load messages from API
6. The `buildCard` function is pure logic — no changes needed
7. The SSE streaming logic is unchanged

### Notification Preferences Storage

Recommended: store in `users` table as JSONB column `notification_preferences JSONB DEFAULT '{"job_complete": true, "job_failure": true}'`. This avoids a separate table for two boolean fields. The settings page reads/writes this column via a new `PUT /user/settings` endpoint.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Collapsible sidebar with responsive overlay | Custom CSS + state | shadcn Sidebar (Radix-based) | Handles collapse state, mobile Sheet overlay, keyboard shortcut (Cmd+B), aria attributes out of the box |
| Tab navigation in settings | Custom tab components | shadcn Tabs | Radix Tabs manages focus, keyboard navigation (arrow keys), aria-selected, aria-controls |
| Confirmation dialogs | Custom modal state | shadcn Dialog | Radix Dialog handles focus trapping, Escape close, aria-modal, portal rendering |
| Data table | CSS Grid masquerading as table | shadcn Table (semantic HTML) | Screen readers require `<table>` semantics; shadcn Table is a thin wrapper over semantic HTML5 elements |
| Progress bar in usage tab | CSS width animation | shadcn Progress | Radix Progress handles aria-valuenow/valuemin/valuemax |
| Session title editing | Contenteditable div | Input with onBlur save | Contenteditable is inaccessible; controlled Input with save-on-blur is simpler and accessible |
| Stripe payment management UI | Custom card form | Stripe Customer Portal | D-15 is explicit: zero custom payment UI; portals handle PCI scope |

**Key insight:** Every interactive component in this phase has a shadcn/Radix equivalent. The accessibility guarantees come from Radix primitives — hand-rolling any of these would require implementing WAI-ARIA patterns from scratch.

---

## Common Pitfalls

### Pitfall 1: Session Context Loss on ChatPage Refactor
**What goes wrong:** `ChatPage.tsx` currently assembles `ReviewData` by accumulating refs across multiple SSE tool_result events (`intentResultRef`, `structureResultRef`, `parametersResultRef`). If these refs are not persisted into `session_messages.cards` correctly, session resume shows correct message text but broken ReviewCard/ValidationCard.
**Why it happens:** Cards are assembled client-side from tool_result events, not from a single server response. The cards JSONB in `session_messages` must capture the fully assembled card state, not the raw tool_result payloads.
**How to avoid:** When saving a message that contains cards, serialize the complete card data (post-assembly) to `cards` JSONB. On resume, deserialize `cards` directly into the `ChatMessage.cards` array without re-running `buildCard`.
**Warning signs:** Session history shows messages but no structured cards (StructurePreviewCard/ReviewCard missing) after resume.

### Pitfall 2: Redis SessionManager Still Called After Migration
**What goes wrong:** `backend/agent/router.py` calls `session_manager.load()` and `session_manager.save()` via the Redis-backed `SessionManager`. If only the HTTP session CRUD endpoints are migrated to PostgreSQL but the agent message handler is not updated, the system silently runs two parallel session stores.
**Why it happens:** The session manager is used both for HTTP session CRUD and within the SSE streaming event_generator. It is easy to migrate the endpoints and miss the internal usage.
**How to avoid:** Replace `session_manager.load/save` inside `event_generator()` in `router.py` with direct asyncpg queries to `session_messages`. Alternatively, create a `PgSessionManager` class that satisfies the same interface as `SessionManager` and swap it.
**Warning signs:** New sessions appear in PostgreSQL but message history still resets after 1hr (Redis TTL expiry).

### Pitfall 3: App Shell Route Guard Missing
**What goes wrong:** `/settings` and `/jobs` are protected routes but if `AuthenticatedLayout` does not include a redirect for unauthenticated users, accessing these URLs directly returns a blank page or crashes.
**Why it happens:** Current `App.tsx` has no route-level auth guard. Existing pages (ChatPage, JobPage) likely handle this via API 401 responses, not route-level guards.
**How to avoid:** `AuthenticatedLayout` must check Supabase auth state and redirect to `/login` if no session. Use the same pattern as existing auth pages.
**Warning signs:** Navigating to `/settings` while logged out shows a blank screen instead of redirecting.

### Pitfall 4: shadcn Sidebar CSS Variable Conflict
**What goes wrong:** The shadcn Sidebar component uses its own CSS variables (`--sidebar`, `--sidebar-foreground`, `--sidebar-primary`, etc.) that may not be defined in the existing `globals.css` dark theme block. Sidebar renders with broken colors (white on white, or system defaults).
**Why it happens:** Installing `npx shadcn add sidebar` adds a new CSS variable set that must be manually added to the `.dark {}` block in `globals.css`. The shadcn installer may add them to `:root` only (light theme), missing the dark theme override.
**How to avoid:** After running `npx shadcn add sidebar`, inspect `globals.css` — verify sidebar CSS variables exist inside `.dark {}`. If they appear only in `:root`, copy them to `.dark {}` with dark-appropriate values.
**Warning signs:** Sidebar background is white or default-browser gray in dark mode.

### Pitfall 5: Notification Preferences Race Condition
**What goes wrong:** Settings page loads notification toggles, user toggles a preference, auto-save fires — but the backend endpoint receives a stale value because two rapid toggles fire two concurrent PUT requests.
**Why it happens:** Debounce not applied, or optimistic UI update not matching server response.
**How to avoid:** Debounce toggle change handlers by 500ms, or use explicit "Save changes" button (already in the spec). The UI-SPEC includes a "Save changes" button for the settings page — use it for all tabs, not just account.
**Warning signs:** Toggling quickly results in the persisted value being the opposite of the last UI state.

### Pitfall 6: Pagination Cursor vs Offset on Jobs Table
**What goes wrong:** Using `OFFSET`-based pagination with `LIMIT 25 OFFSET n*25` causes performance degradation as job count grows and inconsistent results when new jobs are inserted between page navigations.
**Why it happens:** Offset pagination is simple to implement but incorrect for mutable data sets.
**How to avoid:** Use keyset (cursor) pagination: `WHERE created_at < :cursor ORDER BY created_at DESC LIMIT 25`. The "Previous" button is acceptable as a client-side page stack (store visited cursors in state).
**Warning signs:** Page 2 shows a job that was already on page 1 (row shifted by a new insert on page 1).

### Pitfall 7: aria-live Region Pollution
**What goes wrong:** If `aria-live="polite"` is placed on a container that already has many children (the full MessageList), screen readers announce every existing message when the component mounts, not just new messages.
**Why it happens:** `aria-live` regions announce their entire content on first render plus all subsequent DOM mutations.
**How to avoid:** Place `aria-live` on a visually hidden "announcement" div that receives only the most recent new message text, not on the full message container. Alternatively, use `aria-live="polite"` with `aria-atomic="false"` and `aria-relevant="additions"` on a container whose only children are new messages.
**Warning signs:** Screen reader announces all message history on page load.

---

## Code Examples

### shadcn Sidebar Installation and Basic Structure

```tsx
// Source: shadcn official docs — https://ui.shadcn.com/docs/components/sidebar
// Install: npx shadcn add sidebar

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarSeparator,
  SidebarTrigger,
} from "@/components/ui/sidebar";

function AppSidebar() {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        {/* Logo */}
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Today</SidebarGroupLabel>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton aria-current="page">
                Session title
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarSeparator />
      <SidebarFooter>
        {/* User section */}
      </SidebarFooter>
    </Sidebar>
  );
}
```

### Session Resume — Message Reconstruction from PostgreSQL

```typescript
// Pattern for loading a session and reconstructing ChatMessage[] state
// Source: derived from existing agent.ts patterns + D-07 decision

interface PersistedMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cards: ChatCard[] | null;   // deserialized from session_messages.cards JSONB
  sort_order: number;
}

async function loadSession(sessionId: string): Promise<{
  title: string | null;
  messages: ChatMessage[];
}> {
  const data = await api<{
    title: string | null;
    messages: PersistedMessage[];
  }>(`/sessions/${sessionId}`, { method: "GET" });

  const messages: ChatMessage[] = data.messages
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      cards: m.cards ?? undefined,
    }));

  return { title: data.title, messages };
}
```

### WCAG Compliant aria-live Announcement Pattern

```tsx
// Source: WCAG 2.2 AA — Live Regions pattern
// Place once in AppShell, outside of MessageList

function LiveAnnouncer({ message }: { message: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="sr-only"
    >
      {message}
    </div>
  );
}

// For assertive (job status SSE updates):
function AssertiveLiveRegion({ text }: { text: string }) {
  return (
    <div aria-live="assertive" aria-atomic="true" className="sr-only">
      {text}
    </div>
  );
}
```

### Skip Navigation Link

```tsx
// Source: WCAG 2.2 SC 2.4.1 — must be first focusable element in <body>
// Add as first child of App root div

<a
  href="#main-content"
  className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:bg-background focus:text-foreground focus:px-4 focus:py-2 focus:rounded-md focus:ring-2 focus:ring-ring"
>
  Skip to main content
</a>
```

### Status Badge with sr-only Text (D-25)

```tsx
// Source: WCAG 2.2 AA SC 1.4.1 — color not as only visual means

function StatusBadge({ status }: { status: "running" | "complete" | "failed" | "queued" | "cancelled" }) {
  const styles: Record<string, string> = {
    running: "bg-blue-500/15 text-blue-400",
    complete: "bg-green-500/15 text-green-400",
    failed: "bg-destructive/15 text-destructive",
    queued: "bg-muted text-muted-foreground",
    cancelled: "bg-muted text-muted-foreground",
  };

  return (
    <Badge className={styles[status]}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
      <span className="sr-only"> status</span>
    </Badge>
  );
}
```

### Reduced Motion CSS (D-27)

```css
/* Add to globals.css */
@media (prefers-reduced-motion: reduce) {
  .animate-fade-in-up,
  .animate-fade-in,
  [data-sidebar],
  [data-state="open"],
  [data-state="closed"] {
    animation: none !important;
    transition: none !important;
  }
}
```

### Keyset Pagination for Jobs Endpoint (backend)

```python
# Source: derived from asyncpg patterns used throughout the codebase
# GET /jobs?limit=25&before=<created_at ISO string>

async def list_jobs(
    user_id: str,
    status_filter: str | None,
    before: str | None,   # ISO timestamp cursor
    limit: int = 25,
) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT id, tool, status, name, created_at, gpu_cost_usd,
                   results->>'candidate_count' AS candidate_count, session_id
            FROM public.jobs
            WHERE user_id = $1
              AND ($2::text IS NULL OR status = $2)
              AND ($3::timestamptz IS NULL OR created_at < $3)
            ORDER BY created_at DESC
            LIMIT $4
        """
        rows = await conn.fetch(query, user_id, status_filter, before, limit)
    return [dict(r) for r in rows]
```

---

## Integration Points and Migration Notes

### agent/router.py Changes (HIGH RISK)

The `event_generator()` function in `backend/agent/router.py` calls `session_manager.load()` and `session_manager.save()`. This is the most complex migration in the phase:

1. `session_manager.load(user_id, session_id)` returns `list[dict]` — the raw Anthropic messages array
2. After migration, `messages` array must be loaded from `session_messages` table, reconstructed as Anthropic-compatible `[{role, content}]` dicts
3. After each turn, new messages must be appended to `session_messages` with correct `sort_order`
4. Title generation is triggered async after the first user message is saved (check `len(messages) == 1`)

The Redis `sse_count` key (`_check_sse_limit` / `_release_sse_slot`) stays unchanged — it is a rate-limiting mechanism, not session storage.

### Frontend lib/agent.ts Changes

Replace `createSession()` and `deleteSession()` with session CRUD functions that target the new `/sessions` endpoint. `sendMessage()` is unchanged — it still posts to `/agent/message` with `session_id`.

New functions needed in `lib/agent.ts` (or new `lib/sessions.ts`):
- `listSessions()` → `GET /sessions`
- `loadSession(id)` → `GET /sessions/{id}` (returns messages)
- `createPersistentSession()` → `POST /sessions`
- `deleteSession(id)` → `DELETE /sessions/{id}`
- `updateSessionTitle(id, title)` → `PUT /sessions/{id}`

### App.tsx Route Changes

New routes to add:
- `/jobs` → `<JobHistoryPage />`
- `/settings` → `<SettingsPage />`

All authenticated routes wrapped in `<AuthenticatedLayout>` which contains `<SidebarProvider>` + `<AppSidebar>` + `<AppHeader>`.

Existing `/jobs/:id` stays as-is; it is already inside the authenticated zone.

### Stripe Integration (Settings Billing Tab)

The `billing/router.py` already has `create_portal_session`. The billing tab needs:
1. `GET /billing/payment-method` — new endpoint. Fetches the Stripe customer's default payment method (last-4, brand, expiry). Uses existing `get_or_create_customer` helper.
2. `POST /billing/portal` — already exists. The "Manage payment method" link triggers this.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js / npm | shadcn add, eslint | Yes (project runs) | — | — |
| Supabase CLI | DB migrations | Yes (prior phases used it) | — | — |
| PostgreSQL (Supabase local) | Session persistence | Yes | 15.x (Supabase standard) | — |
| Redis | SSE counter (stays) | Yes (Phase 5 active) | — | — |
| Anthropic API (Haiku model) | Session title generation | Yes | SDK already installed | Skip title gen; use "Untitled session" fallback |
| Stripe API | Billing tab payment method fetch | Yes (Phase 3 integrated) | — | — |

No missing dependencies. All tooling required for this phase is available.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Vitest (frontend) + pytest (backend) |
| Config file | `frontend/vite.config.ts` (vitest config embedded) or `frontend/vitest.config.ts` |
| Quick run command | `cd frontend && npx vitest run --reporter=verbose` |
| Full suite command | `cd frontend && npx vitest run && cd ../backend && pytest tests/ -x` |

### Phase Requirements — Test Map

| Behavior | Test Type | Automated Command | Notes |
|----------|-----------|-------------------|-------|
| Session CRUD endpoints (create, list, get, delete) | Integration | `pytest tests/test_sessions.py -x` | Wave 0 gap — file does not exist |
| Session resume reconstructs messages correctly | Unit | `pytest tests/test_session_resume.py -x` | Wave 0 gap |
| Job list endpoint (pagination, status filter) | Integration | `pytest tests/test_jobs_list.py -x` | Wave 0 gap |
| User usage endpoint returns correct aggregation | Integration | `pytest tests/test_user_usage.py -x` | Wave 0 gap |
| Payment method fetch endpoint | Integration | `pytest tests/test_billing_payment_method.py -x` | Wave 0 gap |
| GreetingCard renders example prompts | Unit | `npx vitest run src/components/chat/GreetingCard.test.tsx` | Wave 0 gap |
| StatusBadge includes sr-only text | Unit | `npx vitest run src/components/StatusBadge.test.tsx` | Wave 0 gap |
| Skip nav link is first focusable element | Manual (axe audit) | `npx axe http://localhost:5173` | Semi-automated |
| Sidebar renders session groups (Today/This Week/Earlier) | Unit | `npx vitest run src/components/AppSidebar.test.tsx` | Wave 0 gap |
| Job history page pagination (Previous/Next disabled at boundaries) | Unit | `npx vitest run src/pages/JobHistoryPage.test.tsx` | Wave 0 gap |

### Wave 0 Gaps

- [ ] `backend/tests/test_sessions.py` — session CRUD endpoint integration tests
- [ ] `backend/tests/test_session_resume.py` — message reconstruction tests
- [ ] `backend/tests/test_jobs_list.py` — paginated jobs list endpoint
- [ ] `backend/tests/test_user_usage.py` — usage summary endpoint
- [ ] `frontend/src/components/chat/GreetingCard.test.tsx` — example prompts render and click behavior
- [ ] `frontend/src/components/AppSidebar.test.tsx` — session grouping, active session indicator
- [ ] `frontend/src/pages/JobHistoryPage.test.tsx` — table render, pagination, empty state
- [ ] `frontend/src/pages/SettingsPage.test.tsx` — tab switching, form field presence

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Redis-only session storage | PostgreSQL-backed persistent sessions | Phase 6 | Sessions survive page refresh and browser close |
| Header-only navigation | Collapsible sidebar + slim header | Phase 6 | Session history accessible without leaving chat |
| No job history list view | `/jobs` paginated table | Phase 6 | Scientists can find and re-download past work |
| Minimal GreetingCard (heading + subtext only) | Clickable example prompts + capability indicators | Phase 6 | Reduces time-to-first-message for new users |
| No accessibility baseline | WCAG 2.2 AA | Phase 6 | Required for biopharma enterprise procurement |

---

## Open Questions

1. **Session title generation timing**
   - What we know: Title is generated via Claude Haiku from first user message (D-06). Titles are editable.
   - What's unclear: Whether title generation is synchronous (block session creation response) or async (best-effort, sidebar shows "Untitled session" briefly).
   - Recommendation: Async. Trigger `POST /sessions/{id}/generate-title` as a background task in FastAPI after the first message is saved. Sidebar uses "Untitled session" as fallback until title appears on next session list refresh.

2. **Session list update frequency**
   - What we know: Sidebar shows session list, grouped by date.
   - What's unclear: When a session title changes (after Haiku generation), does the sidebar update without a page refresh?
   - Recommendation: Re-fetch session list after each `done` SSE event from the agent, or after the message completes. This is a natural refresh point and avoids a polling mechanism.

3. **Message persistence for agent tool_use/tool_result blocks**
   - What we know: The agent saves full Anthropic message history including `tool_use` and `tool_result` blocks to Redis (currently). These are serialized as `{"type": "tool_use", "id": ..., "name": ..., "input": ...}` dicts.
   - What's unclear: Should `session_messages` store one row per Anthropic message turn (which may have multi-block content), or one row per user-visible message?
   - Recommendation: Store user-visible messages only in `session_messages` (one row per user message, one row per assistant text response). Store the raw Anthropic messages array (including tool_use/tool_result blocks) separately as a JSONB column `agent_history JSONB` on the `sessions` table. This separates the UI concern (message display) from the agent context concern (full conversation history for Claude).

4. **Notification preferences: user metadata vs separate table**
   - Left to Claude's discretion per CONTEXT.md.
   - Recommendation: Add `notification_preferences JSONB DEFAULT '{"job_complete": true, "job_failure": true}'` column to `public.users` table. Two boolean fields do not warrant a separate table.

---

## Project Constraints (from CLAUDE.md)

| Constraint | Applies To |
|------------|------------|
| Dark theme only — `className="dark"` on root | All new components; never add light-mode-only classes |
| shadcn 4.x + Tailwind v4 CSS @theme inline | All new components; no tailwind.config.ts changes |
| No additional font families | Typography uses DM Sans + Source Serif 4 already loaded |
| Descriptive variable/function names — no single-letter vars | All backend Python code |
| Google-style docstrings on all functions | All new Python functions |
| Explicit file I/O error handling, fail fast | Backend session CRUD, file operations |
| PEP 8 | All new Python code |
| Font: `font-display` class for Source Serif 4 (section headings only) | GreetingCard heading, Settings section titles |
| Auth: Supabase HTTP-only cookies + CSRF token header | All new POST/PUT/DELETE endpoints |
| API client: `frontend/src/lib/api.ts` for authenticated fetches | All new frontend API calls |
| `API_BASE = "http://localhost:8000"` in lib files | Match existing pattern in agent.ts and jobs.ts |
| Database: asyncpg pool via `db/connection.get_db_pool()` | All new backend DB queries |

---

## Sources

### Primary (HIGH confidence)

- shadcn/ui official docs — Sidebar, Tabs, Table, Dialog, Progress components; installation commands
- CONTEXT.md decisions D-01 through D-27 — locked implementation choices
- UI-SPEC.md (06-UI-SPEC.md) — visual/interaction contract, copywriting, ARIA annotations
- UI-FEATURES.md (.planning/research/UI-FEATURES.md) — prior research on session persistence schema, sidebar patterns, job history layout
- Existing codebase — `App.tsx`, `ChatPage.tsx`, `agent/router.py`, `agent/session.py`, `billing/router.py`, `supabase/migrations/20260318000000_init.sql`

### Secondary (MEDIUM confidence)

- WCAG 2.2 specification (W3C) — aria-live pattern requirements, skip navigation, color contrast
- Radix UI documentation — SidebarProvider keyboard shortcut (Cmd+B), Dialog focus trap behavior

### Tertiary (LOW confidence)

- None — all findings verified against codebase or official docs

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all components confirmed in existing codebase or shadcn official docs
- Architecture patterns: HIGH — based on direct codebase inspection of existing code that will be migrated
- Pitfalls: HIGH — derived from inspecting actual code paths (agent/router.py, session.py, ChatPage.tsx) that will be changed
- Accessibility: HIGH — WCAG 2.2 AA requirements are specification-level; implementation patterns from Radix UI docs

**Research date:** 2026-04-07
**Valid until:** 2026-05-07 (shadcn/Radix stable; Supabase schema is project-specific)
