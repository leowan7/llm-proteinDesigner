/**
 * API client for FastAPI backend.
 *
 * All requests include credentials (cookies) for HTTP-only cookie auth.
 * CSRF: reads csrftoken_v2 cookie and sends it in x-csrftoken header on mutating requests.
 * On 401 response, attempts one token refresh before failing.
 */

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

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
 * Core API client function.
 *
 * Sends an authenticated fetch request to the FastAPI backend.
 * - Attaches credentials: include on all requests (HTTP-only cookie auth)
 * - Attaches x-csrftoken header on non-GET/HEAD requests
 * - On 401, attempts one silent token refresh then retries
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

  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new ApiError(response.status, data.detail || "Request failed");
  }

  return response.json() as Promise<T>;
}
