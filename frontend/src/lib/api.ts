/**
 * API client for FastAPI backend.
 *
 * All requests include credentials (cookies) for HTTP-only cookie auth.
 * CSRF: reads csrftoken_v2 cookie and sends it in x-csrftoken header on mutating requests.
 * On 401 response, attempts one token refresh before failing.
 *
 * Phase 12 (Plan 12-05): every request also carries an `X-Org-Id` header
 * sourced from localStorage["kendrew.activeOrgId"] except for the opt-out
 * list of routes that legitimately have no active-org context.
 */

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

/** localStorage key used to remember the user's active organization. */
const ACTIVE_ORG_STORAGE_KEY = "kendrew.activeOrgId";

/**
 * Routes that must NOT receive the X-Org-Id header.
 *
 * - /auth/*            — auth has no active-org context
 * - /organizations/mine — lists all orgs, must not be filtered to one
 * - /invitations/*     — preview + accept are out-of-band (token-scoped)
 * - /health            — public probe
 *
 * Matching is by `startsWith()`. Bare POST /organizations (no id) is also
 * out — handled explicitly in shouldSendOrgHeader() below.
 */
const ORG_HEADER_OPT_OUT_PREFIXES = [
  "/auth/",
  "/organizations/mine",
  "/invitations/",
  "/health",
];

/**
 * Reads the csrftoken_v2 cookie value from document.cookie.
 * Returns null if no CSRF token cookie is present.
 *
 * Name bumped from "csrftoken" to "csrftoken_v2" to force browsers carrying
 * pre-domain-scoping cookies to drop the orphaned value and pick up the new
 * one issued with Domain=.bindwave.com (visible cross-subdomain).
 */
function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken_v2=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Decides whether the X-Org-Id header should be attached to a given path.
 *
 * Returns true for /jobs/*, /billing/*, /user/*, /sessions/*, etc.
 * Returns false for /auth/*, /organizations/mine, POST /organizations
 * (no id, no trailing slash), /invitations/*, and /health.
 */
function shouldSendOrgHeader(path: string, method: string): boolean {
  // Strip query string before prefix matching.
  const pathOnly = path.split("?")[0];

  if (ORG_HEADER_OPT_OUT_PREFIXES.some((prefix) => pathOnly.startsWith(prefix))) {
    return false;
  }
  // Bare `POST /organizations` (no id) creates a new org — no active org yet.
  // Sub-paths like /organizations/{id}/members still receive the header.
  if (
    (pathOnly === "/organizations" || pathOnly === "/organizations/") &&
    method.toUpperCase() === "POST"
  ) {
    return false;
  }
  return true;
}

interface ApiOptions {
  method?: string;
  body?: unknown;
  skipRefreshRetry?: boolean;
}

/** Typed error thrown by the api() client on non-2xx responses. */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Silently attempts to refresh the access token via the /auth/refresh endpoint.
 * Returns true if the refresh succeeded, false otherwise.
 */
async function refreshToken(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: {
        "x-csrftoken": getCsrfToken() || "",
      },
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * On a 403 with an org-related detail, clear the stale active-org id so the
 * next request can pick up a fresh default. We do NOT auto-reload here —
 * OrgProvider re-fetches /organizations/mine on next mount and picks the
 * personal org as a fallback.
 *
 * Triggers:
 *   "Not a member of this organization"  — user was removed mid-session
 *   "X-Org-Id header required"           — stale header on a now-required route
 */
async function maybeClearStaleOrgOn403(response: Response): Promise<void> {
  try {
    const data = await response.clone().json();
    const detail = String(data?.detail ?? "");
    if (
      detail.includes("Not a member of this organization") ||
      detail.includes("X-Org-Id header required")
    ) {
      try {
        localStorage.removeItem(ACTIVE_ORG_STORAGE_KEY);
      } catch {
        // localStorage unavailable (SSR/private mode) — nothing to do
      }
    }
  } catch {
    // Body not JSON — ignore.
  }
}

/**
 * Core API client function.
 *
 * Sends an authenticated fetch request to the FastAPI backend.
 * - Attaches credentials: include on all requests (HTTP-only cookie auth)
 * - Attaches x-csrftoken header on non-GET/HEAD requests
 * - Attaches X-Org-Id header on org-scoped routes (Plan 12-05)
 * - On 401, attempts one silent token refresh then retries
 * - On 403 with org-related detail, clears the stale active-org id
 * - Throws ApiError on non-2xx responses
 *
 * @param path - API path (e.g. "/auth/login")
 * @param options - Request options (method, body, skipRefreshRetry)
 * @returns Parsed JSON response body
 */
export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const { method = "GET", body, skipRefreshRetry = false } = options;

  const headers: Record<string, string> = {};

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  // Attach CSRF token on mutating requests
  if (method !== "GET" && method !== "HEAD") {
    const csrf = getCsrfToken();
    if (csrf) {
      headers["x-csrftoken"] = csrf;
    }
  }

  // Attach X-Org-Id header on org-scoped routes (Plan 12-05).
  if (shouldSendOrgHeader(path, method)) {
    try {
      const activeOrgId = localStorage.getItem(ACTIVE_ORG_STORAGE_KEY);
      if (activeOrgId) {
        headers["X-Org-Id"] = activeOrgId;
      }
    } catch {
      // localStorage unavailable — proceed without the header.
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "include",
  });

  // On 401, attempt one silent refresh then retry
  if (response.status === 401 && !skipRefreshRetry) {
    const refreshed = await refreshToken();
    if (refreshed) {
      return api<T>(path, { ...options, skipRefreshRetry: true });
    }
  }

  // On 403, clear stale active-org id when the backend says we're not a member
  // or the header is missing — OrgProvider will re-resolve on next mount.
  if (response.status === 403) {
    await maybeClearStaleOrgOn403(response);
  }

  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new ApiError(response.status, data.detail || "Request failed");
  }

  return response.json() as Promise<T>;
}
