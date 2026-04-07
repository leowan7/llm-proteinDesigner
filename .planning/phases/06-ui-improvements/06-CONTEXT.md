# Phase 6: UI Improvements - Context

**Gathered:** 2026-04-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Make Kendrew feel like a polished SaaS product: persistent sessions with sidebar navigation, job history, user settings with billing visibility, enhanced onboarding, and WCAG 2.2 AA accessibility baseline. No new backend capabilities — this phase improves the frontend experience around existing functionality.

</domain>

<decisions>
## Implementation Decisions

### Sidebar & Navigation
- **D-01:** Collapsible left sidebar, always visible on desktop by default. User can collapse/expand. shadcn Sidebar component handles responsive behavior (overlay on tablet, sheet on mobile).
- **D-02:** Sidebar contents (top to bottom): New Session button, session history list (grouped by date), separator, Jobs and Settings nav links, user section at bottom (avatar/initials, name, logout).
- **D-03:** UserMenu moves from header to sidebar footer. Header becomes: logo + sidebar toggle (left), optional session title (center).
- **D-04:** No Help/Docs link in sidebar at launch — defer to when docs page exists.

### Session Persistence
- **D-05:** Sessions stored in PostgreSQL (`sessions` + `session_messages` tables). Replace current Redis-based ephemeral sessions with persistent storage.
- **D-06:** Session titles auto-generated via LLM (Claude Haiku) from the first user message. Titles are editable by the user.
- **D-07:** Full session resume: clicking a past session restores the complete message history including structured cards, and the user can continue the conversation. Agent receives prior context on resume.
- **D-08:** Sessions grouped in sidebar by date: Today, This Week, Earlier. No folders or project organization at v1.
- **D-09:** Session-to-job linking via foreign key on jobs table (`session_id`). A session may produce multiple jobs.

### Settings & Billing UI
- **D-10:** Settings page at `/settings` with four tabs: Account, Billing, Usage, Notifications. Use shadcn Tabs component.
- **D-11:** Account tab: name, email (read-only if Supabase-managed), password change form. Minimal — no profile photo, no bio.
- **D-12:** Billing tab: show current card last-4 and expiry, with "Manage payment method" link to Stripe Customer Portal. No inline card management UI.
- **D-13:** Usage tab: current billing period total spend, job count, recent charges table (job name, date, GPU cost), link to Stripe-hosted invoice history.
- **D-14:** Notifications tab: email notification toggles for job completion and job failure. Default both on. Simple boolean toggles.
- **D-15:** Stripe Customer Portal for all billing management (payment methods, invoices, etc.). Zero custom payment UI to maintain.

### Job History Page
- **D-16:** Job history page at `/jobs` using table layout (shadcn Table). Columns: title/description, tool, status badge, date (relative with full date on hover), designs count, GPU cost, actions (View, Download all).
- **D-17:** Status filter dropdown at launch: All, Running, Complete, Failed. Tool filter deferred to post-launch.
- **D-18:** Server-side pagination, 25 jobs per page, Previous/Next controls. No infinite scroll.
- **D-19:** Mobile: table degrades to card-based list (each job as a compact card with status badge, tool, date).
- **D-20:** Empty state: "No jobs yet. Start a design conversation to launch your first job." with link back to chat.

### Onboarding
- **D-21:** Enhanced GreetingCard with clickable example prompts (3-4 prompts that auto-fill the chat input). No guided tour, no modals, no tooltip walkthrough.
- **D-22:** Subtle capability indicators below prompts (supported tools, input methods, typical runtime).
- **D-23:** One-time post-first-job completion message teaching navigation (sidebar, jobs page).

### Accessibility
- **D-24:** WCAG 2.2 AA baseline: skip navigation link, aria-live regions for chat messages and job status SSE updates, heading hierarchy audit, color contrast verification, keyboard navigation audit.
- **D-25:** Status badges must not rely on color alone — include visually hidden text.
- **D-26:** Score tables use semantic HTML `<table>` with proper `<th>` headers.
- **D-27:** Reduced motion support (`prefers-reduced-motion` media query) and eslint-plugin-jsx-a11y added to CI.

### Claude's Discretion
- Session list loading skeleton while sessions fetch
- Exact responsive breakpoints for sidebar collapse behavior
- Animation/transition details for sidebar open/close
- Pagination component design for job history
- Notification preferences storage mechanism (user metadata vs separate table)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### UI Research
- `.planning/research/UI-FEATURES.md` — Comprehensive UI feature research covering session persistence schema, sidebar patterns, settings page design, job history layout, onboarding approach, accessibility requirements, and shadcn component recommendations

### Phase 3 Context (prior UI decisions)
- `.planning/phases/03-job-execution-frontend-and-billing/03-CONTEXT.md` — Prior frontend decisions: card-based results layout, tool-native scoring, pre-ranked display, no 3D viewer in v1

### Existing Frontend Code
- `frontend/src/App.tsx` — Current routing structure (needs sidebar wrapper added)
- `frontend/src/components/chat/ChatPage.tsx` — Current chat layout with two-column resizable panels, ephemeral session lifecycle
- `frontend/src/components/chat/GreetingCard.tsx` — Current greeting card (target for onboarding enhancement)
- `frontend/src/components/UserMenu.tsx` — Current user menu dropdown (moves to sidebar footer)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **shadcn UI components installed:** Card, Button, Sheet, ScrollArea, Separator, DropdownMenu, Badge, Form, Input, Label, Textarea, Tooltip, Alert
- **shadcn components to install:** Sidebar, Tabs, Table, Sonner (toast, post-launch), Dialog, Progress
- **UserMenu component:** Already handles logout, can be adapted for sidebar footer
- **GreetingCard component:** Entry point for onboarding enhancement — already renders on first visit
- **ChatPage two-column layout:** Uses ResizablePanel; sidebar adds a third column to the left

### Established Patterns
- **Dark theme only:** `className="dark"` on root div in App.tsx. All components use dark theme tokens.
- **Auth flow:** Supabase Auth with HTTP-only cookies. `get_current_user` dependency on backend.
- **SSE for real-time:** Agent events and job status both use SSE. Existing pattern for aria-live regions.
- **API client:** `frontend/src/lib/api.ts` handles fetch with auth cookies and CSRF tokens.

### Integration Points
- **App.tsx routing:** New routes needed: `/jobs` (history), `/settings` (settings page). Sidebar wraps all authenticated routes.
- **Agent session API:** Current `createSession`/`deleteSession` in `lib/agent.ts` must be replaced with persistent session CRUD.
- **Jobs table:** Already has all fields needed for job history (status, tool, created_at, gpu_cost_usd). Needs `session_id` FK added.
- **Backend endpoints needed:** Session CRUD (`/sessions`), job list (`/jobs` GET with pagination/filter), user usage summary (`/user/usage`).

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Research in UI-FEATURES.md provides detailed implementation guidance for each feature area.

</specifics>

<deferred>
## Deferred Ideas

- **Help/Docs page** (`/docs`) — Tool descriptions, parameter explanations, result interpretation guide. Phase 6 scope includes a docs link placeholder but not the docs content itself.
- **Resources page** (`/resources`) — Links to publications, benchmarks, video walkthroughs. Separate phase or post-launch.
- **Session search** — Full-text search across sessions. Add when users accumulate 20+ sessions.
- **Theme toggle** (light/dark) — Currently hardcoded dark. Add when user feedback requests light mode.
- **In-app toast notifications** (Sonner) — Post-launch addition when users report missing job completion events while in-app.

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 06-ui-improvements*
*Context gathered: 2026-04-06*
