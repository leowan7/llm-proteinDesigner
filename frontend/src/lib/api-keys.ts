/**
 * API-key management client (Plan 13-06).
 *
 * Wraps the WEB-flow endpoints under /user/api-keys — NOT the SDK-facing
 * /api/v1/api-keys routes (RESEARCH §5.5). Because these paths live under
 * /user/*, the shared api() helper auto-attaches the X-Org-Id header (the
 * opt-out list in api.ts does NOT include /user/*), so the backend can scope
 * keys to the caller's active organization.
 *
 * All requests go through the shared api() helper which handles base URL,
 * cookie auth, CSRF tokens, and 401 refresh retry.
 */

import { api } from "./api";

/**
 * An API key as returned by GET /user/api-keys — never includes the plaintext.
 * `last_used_at` is null for keys that have never authenticated a request.
 */
export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  role: string;
  created_at: string;
  last_used_at: string | null;
}

/**
 * The response shape from POST /user/api-keys. Extends ApiKey with the
 * `plaintext` secret, which the backend returns EXACTLY ONCE at creation
 * time (API-01) and never again. The list endpoint omits it entirely.
 */
export interface CreatedApiKey extends ApiKey {
  plaintext: string;
}

/**
 * Lists the caller's active-org API keys (non-revoked). Plaintext is never
 * included — rows carry id, name, prefix, role, created_at, last_used_at.
 */
export async function listApiKeys(): Promise<ApiKey[]> {
  return api<ApiKey[]>("/user/api-keys", { method: "GET" });
}

/**
 * Creates a new API key with the given display name. The returned object
 * carries the one-time `plaintext` secret — surface it to the user once,
 * then discard it. It is not recoverable after the response is dropped.
 */
export async function createApiKey(name: string): Promise<CreatedApiKey> {
  return api<CreatedApiKey>("/user/api-keys", {
    method: "POST",
    body: { name },
  });
}

/**
 * Revokes an API key by id. After success the key no longer appears in
 * listApiKeys() and any request bearing it receives 401 immediately.
 * Requires owner/admin role on the backend (member → 403).
 */
export async function revokeApiKey(keyId: string): Promise<void> {
  await api(`/user/api-keys/${keyId}/revoke`, { method: "POST" });
}
