/**
 * Agent API client for chat session management and SSE streaming.
 *
 * Handles:
 * - Session creation and deletion
 * - Message sending with SSE streaming response
 * - PDB file upload alongside messages
 *
 * Note: Uses fetch + ReadableStream (not EventSource) for SSE because we
 * need to POST a JSON body. EventSource only supports GET requests.
 */

import { api } from "./api";

const API_BASE = "http://localhost:8000";

/**
 * Reads the csrftoken cookie value from document.cookie.
 * Returns empty string if no CSRF token cookie is present.
 */
function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/** SSE event types from the agent endpoint */
export type AgentEvent =
  | { type: "status"; text: string }
  | { type: "text"; text: string }
  | { type: "tool_result"; tool_name: string; result: Record<string, unknown> }
  | { type: "done" }
  | { type: "error"; text: string };

/** Per-chain metadata from RCSB */
export interface ChainInfo {
  id: string;
  name: string;
  residue_count: number;
  organism?: string;
}

/** Structure summary returned by resolve_structure tool */
export interface StructureSummary {
  pdb_id: string;
  protein_name: string;
  resolution: number | null;
  method: string;
  chain_count: number;
  selected_chain: string;
  residue_count: number;
  chains: ChainInfo[];
  normalization_changes: string[];
  organism?: string;
}

/** Validation check result from validate_preflight tool */
export interface ValidationCheck {
  check_name: string;
  status: "pass" | "warn" | "fail";
  message: string;
}

/** Review card data assembled from tool results */
export interface ReviewData {
  design_goal: string;
  tool: string;
  rationale: string;
  target_pdb_id: string;
  target_chain: string;
  hotspot_residues: number[];
  parameters: Record<string, unknown>;
  parameter_descriptions: Record<string, { label: string; description: string; default: unknown }>;
  estimated_cost_usd: number;
  validation_results: ValidationCheck[];
  can_proceed: boolean;
  has_warnings: boolean;
}

/** Chat message in the local state */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cards?: ChatCard[];
  /** Optional inline action buttons (half-built feature; widened for build). */
  actions?: unknown[];
}

export type ChatCard =
  | { type: "structure_preview"; data: StructureSummary }
  | { type: "review"; data: ReviewData }
  | {
      type: "validation";
      data: {
        validation_results: ValidationCheck[];
        can_proceed: boolean;
        has_warnings: boolean;
        summary: string;
      };
    };

/**
 * Create a new agent session.
 *
 * @returns The session_id string for use in subsequent messages.
 */
export async function createSession(): Promise<string> {
  const data = await api<{ session_id: string }>("/agent/session", {
    method: "POST",
  });
  return data.session_id;
}

/**
 * Delete an existing agent session.
 *
 * @param sessionId - The session to delete.
 */
export async function deleteSession(sessionId: string): Promise<void> {
  await api(`/agent/session/${sessionId}`, { method: "DELETE" });
}

/**
 * Upload a PDB or mmCIF file to the backend for normalization.
 *
 * @param file - The file to upload (must be .pdb or .cif).
 * @returns normalized_path and list of normalization changes applied.
 */
export async function uploadPdbFile(
  file: File,
): Promise<{ normalized_path: string; changes: string[] }> {
  const formData = new FormData();
  formData.append("file", file);
  const csrf = getCsrfToken();
  const headers: Record<string, string> = {};
  if (csrf) headers["x-csrftoken"] = csrf;
  const response = await fetch(`${API_BASE}/pdb/upload`, {
    method: "POST",
    body: formData,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error((err as { detail: string }).detail);
  }
  return response.json() as Promise<{ normalized_path: string; changes: string[] }>;
}

/**
 * Send a message to the agent and process the SSE stream.
 *
 * Uses fetch + ReadableStream (not EventSource) because we need to POST a body.
 * EventSource only supports GET requests.
 *
 * @param sessionId - Active session to send the message to.
 * @param message - The user's message text.
 * @param onEvent - Callback invoked for each parsed SSE event.
 */
export async function sendMessage(
  sessionId: string,
  message: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const csrf = getCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["x-csrftoken"] = csrf;
  const response = await fetch(`${API_BASE}/agent/message`, {
    method: "POST",
    headers,
    body: JSON.stringify({ session_id: sessionId, message }),
    credentials: "include",
    signal,
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Message failed" }));
    throw new Error((err as { detail: string }).detail);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE protocol: events are separated by double newlines, each line prefixed "data: "
    const lines = buffer.split("\n");
    // Keep the last potentially incomplete line in the buffer
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event = JSON.parse(line.slice(6)) as AgentEvent;
          onEvent(event);
        } catch {
          // Ignore malformed or non-JSON SSE lines (e.g. keep-alive comments)
        }
      }
    }
  }
}
