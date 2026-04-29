# UI Feature Research: Kendrew Platform

**Domain:** SaaS AI protein design platform (chat-first scientific tool)
**Researched:** 2026-03-20
**Overall confidence:** MEDIUM-HIGH
**Context:** Platform has auth, chat wizard, job execution, PDB upload, and Stripe billing built. This research covers the user-facing UI features needed to make the product feel complete for launch and beyond.

---

## 1. User Settings Page

**Priority:** Must-have for launch
**Complexity:** MEDIUM
**Confidence:** HIGH

### What to Include

| Setting | Priority | Rationale |
|---------|----------|-----------|
| Account details (name, email) | Launch | Basic identity management. Read from Supabase Auth, editable via Supabase client SDK. |
| Password change | Launch | Supabase Auth provides `updateUser({ password })` — minimal implementation cost. |
| Billing / payment method | Launch | Link to Stripe Customer Portal. Already required for BILL-03 (payment gate before job launch). Surface current payment method and link to manage. |
| Usage dashboard | Launch | Show current billing period GPU spend, job count, total compute hours. Data already exists in the jobs table and Stripe meters. Scientists at biopharma companies expense compute — they need receipts and visibility. |
| Notification preferences | Post-launch | Email on job complete/fail is the only notification type at v1. A toggle adds complexity for one boolean. Ship with email-on by default; add preferences when notification types expand. |
| API keys | v2 | Only relevant when PLAT-V2-01 (REST API access) ships. Do not build API key management before the API exists. |
| Team management | v2 | Explicitly deferred per PLAT-V2-02. Single-user accounts at v1. |
| Theme toggle (light/dark) | Post-launch | Currently hardcoded dark mode. A toggle is low-cost but not launch-blocking. Scientists working in bright labs may prefer light mode — add when user feedback requests it. |
| Default tool preferences | v2 | Saving preferred tool, default parameter sets. Only valuable once repeat usage patterns emerge. |

### Implementation Approach

Route: `/settings` with tabbed sub-sections (Account, Billing, Usage).

Use shadcn Tabs component for sub-navigation within the settings page. Each tab is a self-contained section that loads its own data.

**Billing tab** should embed a "Manage payment method" link that opens the Stripe Customer Portal (already standard Stripe integration pattern — `stripe.billingPortal.sessions.create()`). Display the current card last-4 and expiry fetched from the Stripe customer object.

**Usage tab** should show:
- Current billing period total spend (from Stripe meter events or local job cost aggregation)
- Job count this period
- A simple table of recent charges (job name, date, GPU cost)
- Link to full Stripe-hosted invoice history

**Account tab**: name, email (read-only if Supabase-managed), password change form. Keep it minimal. No profile photo, no bio — this is a scientific tool, not a social platform.

### Libraries

No additional libraries needed. shadcn form components (already installed) + Supabase client SDK + existing Stripe integration cover all cases.

---

## 2. Session History / Previous Conversations

**Priority:** Must-have for launch
**Complexity:** HIGH
**Confidence:** MEDIUM

### Current State

Sessions are ephemeral. The ChatPage creates a new session on mount and clears everything on "New Session." No persistence between page refreshes. This is the single largest UX gap for launch — a scientist who refreshes the page or closes the browser loses their entire design conversation.

### How Comparable Products Handle This

**ChatGPT (2025-2026):**
- Left sidebar lists conversation threads, sorted by recency
- Conversations are auto-titled from the first message
- Floating sidebar mode (hovers over content rather than pushing it)
- Recent addition: full conversation search via "PersonalContextAgentTool" (Plus/Pro only)
- Pinned conversations supported

**Cursor (AI code editor):**
- Left sidebar lists chat sessions
- Sessions tied to workspace context (files open, project)
- Sessions persist across app restarts
- No explicit "save" — all sessions are automatically persisted

**Claude.ai:**
- Left sidebar with conversation list, grouped by date (Today, Yesterday, Previous 7 days)
- Auto-generated titles from first message
- Conversations persist indefinitely
- Project-level organization (folders)

### Recommended Approach for Kendrew

**Collapsible left sidebar** listing past design sessions, sorted by recency. This is the established pattern for chat-first apps and users will expect it.

**Key design decisions:**

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Storage backend | PostgreSQL `sessions` table | Sessions contain structured data (tool selection, parameters, job references) that benefits from relational queries. Redis TTL (current approach) is wrong for persistent history. |
| Auto-title generation | LLM-generated from first user message | "Binder design for IL-6R" is more useful than "Session 2026-03-20 14:32". Use a cheap Claude Haiku call or simple extraction. |
| Session-to-job linking | Foreign key from jobs table to sessions table | A session may produce multiple jobs (user iterates). Job history and session history are linked but distinct views. |
| Resume behavior | Restore full message history + context panel state | User clicks a past session and sees the conversation as they left it, including any structured cards. |
| Sidebar position | Left side, collapsible | Matches ChatGPT/Claude/Cursor convention. Collapsible to maximize chat width on smaller screens. |
| Session grouping | By date (Today, This week, Earlier) | Simple, no need for folders or projects at v1. |
| Search | Post-launch | Full-text search across sessions is valuable but adds complexity. Ship without it; add when session count per user justifies it. |

**Session persistence model:**

```
sessions table:
  id (uuid, PK)
  user_id (uuid, FK -> users)
  title (text, nullable — auto-generated)
  created_at (timestamptz)
  updated_at (timestamptz)
  metadata (jsonb — tool, target PDB ID, status summary)

session_messages table:
  id (uuid, PK)
  session_id (uuid, FK -> sessions)
  role (text — 'user' | 'assistant')
  content (text)
  cards (jsonb, nullable — structured card data)
  actions (jsonb, nullable — action button data)
  created_at (timestamptz)
  sort_order (integer)
```

**What NOT to do:**
- Do not store full conversation in Redis with TTL. Sessions are user data, not cache.
- Do not build folder/project organization at v1. Scientists will have 10-50 sessions, not thousands.
- Do not build real-time sync across tabs/devices. Single-tab usage is the norm for desktop scientific tools.

### Sidebar Component

shadcn/ui has an official Sidebar component. Use it. It handles collapsible state, mobile responsiveness (sheet overlay on mobile), and keyboard navigation out of the box.

---

## 3. User Onboarding

**Priority:** Must-have for launch (minimal version)
**Complexity:** LOW-MEDIUM
**Confidence:** MEDIUM

### Why It Matters for This Product

Kendrew's core value proposition requires the user to understand one thing: "describe what you want in plain language and the agent handles the rest." If users don't grasp this on first visit, they'll look for a form-based interface, not find one, and leave.

Activation benchmark for AI/ML SaaS tools: 54.8% (2025 industry data). Target: get the user to submit their first message within 2 minutes of first login.

### Recommended First-Run Experience

**Approach: Contextual greeting + sample prompts. No guided tour, no modal overlays.**

Rationale: Scientists are technically sophisticated. They do not want a patronizing walkthrough. They want to understand what the tool does and start using it. The existing `GreetingCard` component already serves as the entry point — enhance it rather than adding a separate onboarding layer.

**Enhanced GreetingCard should include:**

1. **One-sentence value statement:** "Describe your protein design goal and Kendrew handles tool selection, parameters, and GPU execution."

2. **3-4 clickable example prompts** (these double as the fastest path to first value):
   - "Design a binder for the IL-6 receptor extracellular domain"
   - "Generate de novo backbones for a 100-residue protein"
   - "I have a PDB file for my target — help me design a binder"
   - "What tools are available for antibody design?"

3. **Subtle capability indicators** (not a feature list — just enough to set expectations):
   - "Supports RFdiffusion, BindCraft, BoltzGen, and RFantibody"
   - "Upload PDB files or provide accession IDs"
   - "GPU jobs run on cloud infrastructure — results in 30 min to 2 hours"

4. **Link to documentation** (external, does not block flow): "Read the docs" link in the footer of the greeting card.

**What NOT to do:**
- No multi-step onboarding wizard. The product IS a wizard — the agent IS the onboarding.
- No tooltip tours pointing at UI elements. The UI has two primary elements: the chat input and the context panel.
- No "skip tutorial" button. Nothing should need skipping.
- No demo/sandbox mode with fake data. The agent conversation is inherently exploratory — users learn by asking.
- No email drip campaign at v1. Premature for a product without validated PMF.

### Post-First-Run

After the user completes their first job, show a one-time completion message: "Your first design is complete. You can find all past sessions in the sidebar and all job results under Jobs." This teaches navigation without front-loading it.

---

## 4. Navigation Patterns

**Priority:** Must-have for launch
**Complexity:** MEDIUM
**Confidence:** HIGH

### Current State

The app has a thin header bar with: logo, "New Session" button, mobile sheet trigger, and UserMenu dropdown. No sidebar. No persistent navigation to Jobs, Settings, or other pages. The only way to reach `/jobs/:id` is through the in-chat JobCompletionCard link.

### Recommended Pattern: Collapsible Left Sidebar + Thin Header

**Why sidebar, not header-only:**
- Chat-first apps universally use left sidebars (ChatGPT, Claude, Cursor, Slack). Users expect it.
- The app has 3-5 top-level destinations (Chat, Jobs, Settings) — manageable in a header, but the session history list requires vertical space that only a sidebar provides.
- The sidebar serves double duty: navigation AND session history.
- Header-only navigation would require a separate "History" page, fragmenting the UX.

**Sidebar contents (top to bottom):**

1. **New Session button** (prominent, top of sidebar)
2. **Session history list** (scrollable, grouped by date)
3. **Separator**
4. **Navigation links:**
   - Jobs (icon: `Briefcase` from lucide-react)
   - Settings (icon: `Settings`)
5. **User section at bottom** (avatar/initials, name, logout)

**Header contents (simplified from current):**
- Logo + product name (left)
- Sidebar toggle button (left, next to logo)
- Current session title (center, optional)
- No UserMenu in header — moved to sidebar bottom

**Responsive behavior:**
- Desktop (>= 1024px): Sidebar visible by default, collapsible via toggle
- Tablet (768-1023px): Sidebar collapsed by default, opens as overlay on toggle
- Mobile (< 768px): Sidebar hidden, opens as full-width sheet on toggle

**shadcn Sidebar component** handles all of this. It provides `SidebarProvider`, `SidebarTrigger`, `SidebarContent`, `SidebarGroup`, `SidebarFooter` with built-in responsive behavior and keyboard shortcut (Cmd+B / Ctrl+B to toggle).

### Migration Path

This is a structural change to `App.tsx` and `ChatPage.tsx`. The current header needs to be refactored into a sidebar + slimmer header. Plan this as a dedicated implementation task, not a bolt-on.

---

## 5. Job History Page

**Priority:** Must-have for launch
**Complexity:** LOW-MEDIUM
**Confidence:** HIGH

### Current State

The Phase 3 UI spec mentions a "Previous jobs" list at the bottom of JobPage, but there is no dedicated `/jobs` route listing all jobs. v2 requirement JOB-V2-01 calls for a "Job history page listing all past jobs with status, parameters, and results" but this should be promoted to v1 — scientists need to find past work.

### Recommended Design

**Route:** `/jobs`

**Layout:** Full-width table/list within the app shell (sidebar + header).

**Columns:**

| Column | Content | Sort |
|--------|---------|------|
| Title/Description | Auto-generated from session context (e.g., "IL-6R binder design") or tool + target | - |
| Tool | RFdiffusion / BindCraft / BoltzGen / RFantibody | Filterable |
| Status | Badge (queued/running/complete/failed/cancelled) | Filterable |
| Date | Relative time ("2 hours ago") with full date on hover | Default sort (newest first) |
| Designs | Count of output candidates (e.g., "8 designs") | - |
| GPU Cost | Dollar amount in monospace (e.g., "$4.32") | Sortable |
| Actions | "View" link to `/jobs/:id`, "Download all" for complete jobs | - |

**Empty state:** "No jobs yet. Start a design conversation to launch your first job." with a link back to the chat.

**Pagination:** Server-side, 25 jobs per page. Simple "Previous / Next" pagination, not infinite scroll. Scientists want deterministic navigation, not feed-style UX.

**Filtering (v1 minimal):** Status filter only (dropdown: All, Running, Complete, Failed). Tool filter and date range are post-launch additions.

### Data Source

Query the existing `jobs` table. All required fields (status, tool, created_at, gpu_cost_usd) already exist in the schema from Phase 3. The only new field needed is a human-readable title/description, which can be derived from the session's auto-generated title or stored as a denormalized field on the job record.

### Libraries

shadcn Table component for the data display. No additional data table library needed at v1 — the job count per user will be low (tens to low hundreds). TanStack Table is overkill until sorting/filtering requirements grow.

---

## 6. Notification System

**Priority:** Email notifications are must-have (already in JOB-02). In-app notifications are post-launch.
**Complexity:** Email: LOW (already scoped). In-app: MEDIUM.
**Confidence:** HIGH

### Email Notifications (v1 — already planned)

| Event | Template | Priority |
|-------|----------|----------|
| Job complete | "Your [tool] job is complete. [N] designs generated. View results: [link]" | Launch |
| Job failed | "Your [tool] job failed. Reason: [category]. View details: [link]" | Launch |
| Job cancelled | "Your [tool] job was cancelled. GPU cost: $X.XX. View details: [link]" | Launch |
| Payment method expiring | "Your payment method ending in [last4] expires on [date]. Update it to continue launching jobs." | Post-launch |
| Billing period summary | "This month: [N] jobs, [X] GPU hours, $[Y] total." | Post-launch |

**Transactional email provider:** Resend is the correct choice for this stack. It has a Python SDK, handles transactional email well, and costs $0 for the first 3,000 emails/month. SendGrid is an alternative but heavier. Do not use SES directly — the DX is poor for transactional templates.

### In-App Notifications (post-launch)

**Toast notifications** for real-time events when the user is active in the app:
- Job status transitions (queued -> running, running -> complete)
- Billing events (payment processed, payment failed)

Use **Sonner** (the shadcn/ui-endorsed toast library). It is already part of the shadcn ecosystem, supports all needed variants (success, error, info), and handles positioning, stacking, and dismissal out of the box. Install via `npx shadcn add sonner`.

**Notification center (bell icon with dropdown):** Defer to v2. At v1, the combination of email + toast covers all cases. A persistent notification inbox is only valuable when notification volume and diversity justify it.

### What NOT to build

- No push notifications (web push API). Desktop browser notifications are annoying and most users block them.
- No SMS notifications. Unnecessary for this product.
- No Slack/Teams integration at v1. This is a v2+ enterprise feature.

---

## 7. Responsive Design Concerns

**Priority:** Functional on tablet, informational on mobile. Not launch-blocking but should not be broken.
**Complexity:** LOW-MEDIUM (with shadcn handling most responsive behavior)
**Confidence:** HIGH

### Usage Context

Scientists use this tool at their desk on a laptop or monitor. The primary interaction (typing design goals, reviewing parameters, inspecting 3D structures) is fundamentally a desktop workflow. Mobile usage is limited to checking job status — "did my job finish?" — not running the full wizard.

### Breakpoint Strategy

| Breakpoint | Width | Primary Use | Design Priority |
|------------|-------|-------------|-----------------|
| Desktop | >= 1280px | Full workflow: chat + context panel side-by-side | Primary target |
| Laptop | 1024-1279px | Full workflow: narrower context panel | Must work well |
| Tablet | 768-1023px | Job status checking, session browsing, settings | Functional |
| Mobile | < 768px | Job status checking only | Informational (read-only acceptable) |

### What Changes Per Breakpoint

**Chat page:**
- Desktop/Laptop: Two-column layout (already implemented with resizable panels)
- Tablet: Single column, context panel accessible via sheet (already implemented)
- Mobile: Single column, context panel via sheet, simplified chat input (already implemented)

**Job history page:**
- Desktop/Laptop: Full table with all columns
- Tablet: Hide GPU Cost column, compress date to relative time only
- Mobile: Card-based list instead of table (each job as a compact card with status badge, tool, date)

**Settings page:**
- Desktop: Tabbed layout with generous spacing
- Mobile: Stacked sections (tabs become an accordion or vertical nav)

**Mol* 3D viewer (v2):**
- Desktop: Full interactive viewer with controls
- Mobile: Static thumbnail with "Open on desktop for interactive view" message. Mol* on mobile is technically functional but the interaction model (rotate, zoom, select residues) is poor on touch.

### What NOT to optimize for mobile

- The chat wizard parameter collection flow. Typing complex protein design specifications on a phone keyboard is not a realistic use case.
- PDB file upload. Scientists do not have PDB files on their phones.
- The context panel's structured cards (StructurePreviewCard, ReviewCard). These contain dense data that does not compress well to mobile width.

### Implementation

The existing responsive patterns in ChatPage are adequate. The main new work is making the sidebar responsive (handled by shadcn Sidebar component) and ensuring the Job History page degrades gracefully to card layout on mobile.

---

## 8. Accessibility

**Priority:** Must-have for launch (WCAG 2.2 AA baseline)
**Complexity:** LOW if using shadcn correctly (components are built on Radix UI which handles most a11y)
**Confidence:** HIGH

### Why This Matters

Biopharma companies increasingly require vendor tools to meet WCAG 2.1 AA (now WCAG 2.2 AA as of ISO/IEC 40500:2025). This is not optional for enterprise sales. Accessibility failures will surface during procurement reviews.

### Baseline Requirements (WCAG 2.2 AA)

| Requirement | Status in Current Codebase | Action Needed |
|-------------|---------------------------|---------------|
| Color contrast (4.5:1 text, 3:1 large text) | LIKELY OK — dark theme with light text on dark bg | Audit all text-muted-foreground usages. oklch(0.708 0 0) on oklch(0.205 0 0) needs verification. |
| Keyboard navigation | PARTIAL — Radix UI components (buttons, dropdowns, sheets) handle focus. Custom components may not. | Audit ChatInput, MessageList, CandidateCard for keyboard operability. Ensure all interactive elements are reachable via Tab. |
| Focus indicators | PARTIAL — shadcn uses `ring` for focus-visible states. | Verify focus ring is visible on all interactive elements, especially in the chat thread. |
| Screen reader support | UNKNOWN — no ARIA audit done. | Add aria-label to icon-only buttons, aria-live regions for SSE status updates, proper heading hierarchy. |
| Form labels | OK — shadcn Form component handles label association. | Verify settings page forms have proper labels. |
| Error identification | PARTIAL — form errors shown visually. | Ensure form errors are associated with inputs via aria-describedby. |
| Skip navigation | MISSING | Add a "Skip to main content" link. Low effort, high impact. |
| Reduced motion | MISSING | Add `prefers-reduced-motion` media query to disable animations for users who request it. |

### Chat-Specific Accessibility Concerns

| Concern | Recommendation |
|---------|---------------|
| New messages announced to screen readers | Add `aria-live="polite"` to the MessageList container. New messages will be announced as they arrive. |
| SSE status updates | The status text ("Queued", "Running diffusion") must be in an `aria-live="assertive"` region so screen readers announce state changes. |
| Structured cards (StructurePreviewCard, ReviewCard) | Each card needs a descriptive `aria-label` summarizing its content (e.g., "Structure preview: 1ABC chain A, 245 residues"). |
| Action buttons in chat | Already keyboard-accessible via shadcn Button. Ensure they have descriptive text (not icon-only without aria-label). |
| Code/monospace content | Screen readers handle monospace text fine. No special treatment needed for PDB IDs or scores. |

### Data-Dense UI Considerations

| Pattern | Recommendation |
|---------|---------------|
| Score tables (CandidateCard) | Use semantic HTML `<table>` with `<th>` headers, not CSS grid. Screen readers rely on table semantics to navigate tabular data. |
| Status badges | Include visually hidden text for color-coded badges: `<Badge>Complete<span className="sr-only"> status</span></Badge>`. Do not rely on color alone to convey status. |
| Job history table | Use `<table>` with proper `<thead>`, `<th scope="col">` headers. shadcn Table component handles this correctly. |
| Mol* viewer (v2) | Provide a text summary of the structure alongside the 3D viewer. Mol* itself is not screen-reader accessible (WebGL canvas). |

### Implementation Priority

1. **Launch:** Skip navigation link, aria-live regions for chat and job status, heading hierarchy audit, contrast verification, keyboard navigation audit
2. **Post-launch:** Reduced motion support, comprehensive screen reader testing with NVDA/VoiceOver, VPAT (Voluntary Product Accessibility Template) for enterprise sales

### Libraries/Tools

- **axe-core** or **eslint-plugin-jsx-a11y**: Static analysis for React a11y issues. Add to CI.
- **@axe-core/react**: Runtime a11y auditing in development mode (renders violations in the console).
- No separate a11y component library needed — Radix UI (underlying shadcn) is built with WAI-ARIA patterns.

---

## Feature Priority Summary

### Must-Have for Launch

| Feature | Complexity | Rationale |
|---------|------------|-----------|
| Session history (sidebar + persistence) | HIGH | Without this, users lose work on page refresh. Unacceptable for a tool that runs 30-min to 2-hr jobs. |
| Navigation sidebar | MEDIUM | Required to house session history, link to Jobs and Settings. Current header-only nav does not scale. |
| Settings page (Account + Billing tabs) | MEDIUM | Users need to manage payment methods and see usage. Billing visibility is table stakes for pay-per-job. |
| Job history page | LOW-MEDIUM | Scientists need to find past work. Current app has no way to list all jobs. |
| Enhanced onboarding (GreetingCard upgrade) | LOW | Clickable example prompts accelerate time-to-first-value. Minimal implementation cost. |
| Accessibility baseline | LOW | WCAG 2.2 AA compliance is a procurement requirement for biopharma. Most work is auditing, not building. |

### Post-Launch (add within 1-2 months)

| Feature | Complexity | Trigger to Build |
|---------|------------|-----------------|
| In-app toast notifications (Sonner) | LOW | When users report missing job completion events while in-app |
| Session search | MEDIUM | When users accumulate 20+ sessions |
| Notification preferences | LOW | When notification types expand beyond job completion |
| Theme toggle (light/dark) | LOW | When users request light mode |
| Usage dashboard in settings | MEDIUM | When billing disputes arise or users request spend visibility |

### v2 (defer until PMF validated)

| Feature | Complexity | Rationale |
|---------|------------|-----------|
| API key management | MEDIUM | Depends on REST API (PLAT-V2-01) |
| Team management | HIGH | Depends on shared workspaces (PLAT-V2-02) |
| Notification center (bell icon) | MEDIUM | Only needed when notification volume justifies persistent inbox |
| Full-text session search | HIGH | Only needed at scale (hundreds of sessions per user) |

---

## Architectural Implications

### Database Changes Required

```sql
-- Session persistence (new table)
CREATE TABLE sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  title TEXT,  -- auto-generated from first message
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB DEFAULT '{}'  -- tool, target, status summary
);

-- Session messages (new table)
CREATE TABLE session_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL DEFAULT '',
  cards JSONB,     -- structured card data for replay
  actions JSONB,   -- action button data for replay
  sort_order INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Add session reference to jobs table
ALTER TABLE jobs ADD COLUMN session_id UUID REFERENCES sessions(id);
```

### New shadcn Components to Install

```bash
npx shadcn add sidebar   # Navigation sidebar
npx shadcn add sonner     # Toast notifications (post-launch)
npx shadcn add tabs       # Settings page sub-navigation
npx shadcn add table      # Job history page
npx shadcn add dialog     # Confirmation dialogs (cancel job, etc.)
npx shadcn add progress   # Usage dashboard progress bars
```

### New Routes

```
/            -> ChatPage (with sidebar)
/jobs        -> JobHistoryPage (new)
/jobs/:id    -> JobPage (existing)
/settings    -> SettingsPage (new, tabbed: Account, Billing)
```

### Backend Endpoints Required

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/sessions` | GET | List user's sessions (paginated, sorted by updated_at desc) |
| `/api/sessions` | POST | Create new session (returns session ID) |
| `/api/sessions/:id` | GET | Get session with messages (for resume) |
| `/api/sessions/:id` | PUT | Update session title |
| `/api/sessions/:id` | DELETE | Delete session and cascade messages |
| `/api/sessions/:id/messages` | POST | Append message to session |
| `/api/jobs` | GET | List user's jobs (paginated, filterable by status) |
| `/api/user/usage` | GET | Current billing period usage summary |

Note: Some of these may already exist or overlap with existing endpoints. The session creation endpoint likely needs to replace the current ephemeral session logic in the agent module.

---

## Sources

- [ChatGPT sidebar redesign patterns](https://www.ai-toolbox.co/chatgpt-management-and-productivity/chatgpt-sidebar-redesign-guide) (MEDIUM confidence)
- [ChatGPT conversation history search — April 2025](https://community.openai.com/t/chatgpt-can-now-reference-all-past-conversations-april-10-2025/1229453?page=4) (MEDIUM confidence)
- [Conversational AI UI comparison 2025](https://intuitionlabs.ai/articles/conversational-ai-ui-comparison-2025) (MEDIUM confidence)
- [SaaS onboarding best practices 2025](https://www.flowjam.com/blog/saas-onboarding-best-practices-2025-guide-checklist) (MEDIUM confidence)
- [User activation rate benchmarks 2025 — 54.8% for AI/ML](https://www.agilegrowthlabs.com/blog/user-activation-rate-benchmarks-2025/) (MEDIUM confidence)
- [Header vs sidebar navigation guide](https://saltnbold.com/blog/post/header-vs-sidebar-a-simple-guide-to-better-navigation-design) (MEDIUM confidence)
- [Best UX practices for sidebar design 2025](https://uiuxdesigntrends.com/best-ux-practices-for-sidebar-menu-in-2025/) (MEDIUM confidence)
- [React toast libraries comparison 2025 — Sonner recommended](https://blog.logrocket.com/react-toast-libraries-compared-2025/) (HIGH confidence, technical comparison)
- [shadcn/ui Sonner component](https://ui.shadcn.com/docs/components/radix/sonner) (HIGH confidence, official docs)
- [shadcn/ui Sidebar component](https://ui.shadcn.com/docs/components) (HIGH confidence, official docs)
- [WCAG 2.2 approved as ISO/IEC 40500:2025](https://www.w3.org/press-releases/2025/wcag22-iso-pas/) (HIGH confidence, W3C official)
- [WCAG 2.2 AA requirements](https://www.accessibility.works/blog/wcag-ada-website-compliance-standards-requirements) (HIGH confidence)
- [Benchling tenant admin settings](https://help.benchling.com/hc/en-us/articles/39939352973453-Configure-and-administer-tenant-basics) (HIGH confidence, official docs)
- [SaaS UX design best practices 2025](https://sapient.pro/blog/designing-for-saas-best-practices) (MEDIUM confidence)
- [Top React notification libraries 2026](https://knock.app/blog/the-top-notification-libraries-for-react) (MEDIUM confidence)

---

*UI feature research for: Kendrew — AI protein design SaaS platform*
*Researched: 2026-03-20*
