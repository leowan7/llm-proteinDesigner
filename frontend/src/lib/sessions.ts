/**
 * Session API client — wraps the persistent session CRUD endpoints.
 *
 * Endpoints from Plan 06-01:
 * - GET /sessions → list of session summaries
 * - POST /sessions → create new session
 * - GET /sessions/{id} → full session detail including messages
 * - PUT /sessions/{id} → update session title
 * - DELETE /sessions/{id} → delete session
 * - POST /sessions/{id}/generate-title → auto-generate title from messages
 */

import { api } from "./api";

/** Summary returned by GET /sessions list */
export interface SessionSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

/** Full session detail returned by GET /sessions/{id} */
export interface SessionDetail {
  id: string;
  title: string | null;
  agent_history: unknown[];
  messages: Array<{
    id: string;
    role: "user" | "assistant";
    content: string;
    cards: unknown[] | null;
    sort_order: number;
  }>;
}

/**
 * Fetch a paginated list of the current user's sessions.
 * @param limit - maximum results to return (default 50)
 * @param before - cursor (session updated_at ISO string) for pagination
 */
export async function listSessions(
  limit = 50,
  before?: string,
): Promise<{ sessions: SessionSummary[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return api<{ sessions: SessionSummary[] }>(`/sessions?${params}`, {
    method: "GET",
  });
}

/**
 * Fetch the full detail of a single session including its message history.
 * @param sessionId - UUID of the session
 */
export async function loadSession(sessionId: string): Promise<SessionDetail> {
  return api<SessionDetail>(`/sessions/${sessionId}`, { method: "GET" });
}

/**
 * Create a new persistent session in PostgreSQL.
 * Returns the new session summary (id, title, created_at, updated_at).
 */
export async function createPersistentSession(): Promise<SessionSummary> {
  return api<SessionSummary>("/sessions", { method: "POST" });
}

/**
 * Delete a session permanently.
 * @param sessionId - UUID of the session to delete
 */
export async function deleteSessionApi(sessionId: string): Promise<void> {
  await api(`/sessions/${sessionId}`, { method: "DELETE" });
}

/**
 * Update the title of a session.
 * @param sessionId - UUID of the session
 * @param title - new title string
 */
export async function updateSessionTitle(
  sessionId: string,
  title: string,
): Promise<void> {
  await api(`/sessions/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify({ title }),
  });
}
