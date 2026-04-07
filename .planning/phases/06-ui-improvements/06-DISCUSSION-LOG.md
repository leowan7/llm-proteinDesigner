# Phase 6: UI Improvements - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-06
**Phase:** 06-ui-improvements
**Areas discussed:** Sidebar & navigation, Session persistence, Settings & billing UI, Job history page

---

## Sidebar & Navigation

### Sidebar behavior on desktop

| Option | Description | Selected |
|--------|-------------|----------|
| Always visible (Recommended) | Sidebar open by default, user can collapse it. Matches ChatGPT/Claude pattern. | ✓ |
| Collapsed by default | Starts as thin icon rail, expands on hover or click. | |
| Floating overlay | Overlays content when toggled, doesn't push layout. | |

**User's choice:** Always visible
**Notes:** None

### Sidebar contents beyond session history

| Option | Description | Selected |
|--------|-------------|----------|
| New Session button (top) | Prominent button to start fresh conversation. | ✓ |
| Nav links (Jobs, Settings) | Direct links to /jobs and /settings pages. | ✓ |
| User section (bottom) | Avatar/initials, name, logout at sidebar footer. | ✓ |
| Help/Docs link | Link to docs in sidebar footer. | |

**User's choice:** New Session, Nav links, User section (Help/Docs excluded)
**Notes:** None

---

## Session Persistence

### Session titling

| Option | Description | Selected |
|--------|-------------|----------|
| LLM auto-title (Recommended) | Generate title from first message via Haiku. Editable. | ✓ |
| First message truncation | First ~50 chars of first message. No LLM call. | |
| User names it | Prompt user to name session manually. | |

**User's choice:** LLM auto-title
**Notes:** None

### Resume behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Full resume (Recommended) | Full message history restored, can continue conversation. | ✓ |
| Read-only replay | Viewable but not continuable. | |
| Resume with warning | Allow resume with stale context note. | |

**User's choice:** Full resume
**Notes:** None

### Session grouping

| Option | Description | Selected |
|--------|-------------|----------|
| By date (Recommended) | Today, This Week, Earlier. Simple chronological. | ✓ |
| Flat list | No grouping, sorted by recency. | |
| By target/tool | Group by design tool or target protein. | |

**User's choice:** By date
**Notes:** None

---

## Settings & Billing UI

### Settings tabs at launch

| Option | Description | Selected |
|--------|-------------|----------|
| Account (name, email, password) | Basic identity management. | ✓ |
| Billing (payment method) | Card on file + Stripe Portal link. | ✓ |
| Usage (spend, job count) | GPU spend, job count, recent charges. | ✓ |
| Notifications | Email notification toggles. | ✓ |

**User's choice:** All four tabs
**Notes:** None

### Billing management approach

| Option | Description | Selected |
|--------|-------------|----------|
| Stripe Customer Portal (Recommended) | Link to Stripe-hosted portal. Zero custom UI. | ✓ |
| Inline card management | Build payment forms with Stripe Elements. | |
| Hybrid | Inline summary, Portal for changes. | |

**User's choice:** Stripe Customer Portal
**Notes:** None

---

## Job History Page

### Layout

| Option | Description | Selected |
|--------|-------------|----------|
| Table (Recommended) | Full table with columns. Degrades to cards on mobile. | ✓ |
| Card grid | Each job as a visual card. | |
| Compact list | Minimal rows, click to expand. | |

**User's choice:** Table
**Notes:** None

### Filtering at launch

| Option | Description | Selected |
|--------|-------------|----------|
| Status filter only (Recommended) | Dropdown: All, Running, Complete, Failed. | ✓ |
| Status + tool filter | Both status and tool dropdowns. | |
| No filtering | Just a sorted list. | |

**User's choice:** Status filter only
**Notes:** None

---

## Claude's Discretion

- Session list loading skeleton design
- Exact responsive breakpoints for sidebar collapse
- Animation/transition details for sidebar
- Pagination component design
- Notification preferences storage mechanism

## Deferred Ideas

- Help/Docs page (/docs) — separate content effort
- Resources page (/resources) — post-launch
- Session search — when users accumulate 20+ sessions
- Theme toggle (light/dark) — when user feedback requests it
- In-app toast notifications (Sonner) — post-launch
