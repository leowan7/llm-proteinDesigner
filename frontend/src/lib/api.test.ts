import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Phase 12: api() consults the /health feature probe before attaching
// X-Org-Id. Default null = probe unresolved, which is the pre-Phase-12
// behaviour every other test in this file expects.
const featureFlag = vi.hoisted(() => ({ orgs: null as boolean | null }));
vi.mock("./features", () => ({
  organizationsEnabled: async () => featureFlag.orgs === true,
  organizationsEnabledSync: () => featureFlag.orgs,
  useOrganizationsEnabled: () => featureFlag.orgs,
}));

import { ApiError, api, apiErrorMessage } from "./api";

// ---------------------------------------------------------------------------
// ApiError class
// ---------------------------------------------------------------------------

describe("ApiError", () => {
  it("creates an error with status and detail", () => {
    const error = new ApiError(401, "Not authenticated");
    expect(error.status).toBe(401);
    expect(error.detail).toBe("Not authenticated");
    expect(error.name).toBe("ApiError");
    expect(error.message).toBe("Not authenticated");
  });

  it("is an instance of Error", () => {
    const error = new ApiError(500, "Server error");
    expect(error).toBeInstanceOf(Error);
  });
});

describe("apiErrorMessage", () => {
  it.each([
    [new ApiError(400, "Password should be at least 10 characters."), "Password should be at least 10 characters."],
    [new ApiError(429, "Rate limit exceeded: 5 per 1 minute"), "Too many attempts. Wait a minute and try again."],
    [new ApiError(422, [{ msg: "field required" }] as unknown as string), "Something went wrong. Try again in a moment."],
    [new ApiError(500, "Internal Server Error"), "Something went wrong. Try again in a moment."],
    [new TypeError("Failed to fetch"), "Unable to connect. Check your connection and try again."],
  ])("%s -> %s", (error, message) => {
    expect(apiErrorMessage(error)).toBe(message);
  });
});

// ---------------------------------------------------------------------------
// api() client function
// ---------------------------------------------------------------------------

describe("api()", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch);
    // Stub document.cookie so getCsrfToken() returns null by default
    Object.defineProperty(document, "cookie", {
      value: "",
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    mockFetch.mockReset();
  });

  it("returns parsed JSON on a 200 response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ user_id: "abc123" }),
    });

    const result = await api<{ user_id: string }>("/auth/me");
    expect(result).toEqual({ user_id: "abc123" });
    expect(mockFetch).toHaveBeenCalledOnce();
  });

  it("throws ApiError with status and detail on non-2xx response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Not found" }),
    });

    await expect(api("/missing-resource")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      detail: "Not found",
    });
  });

  it("throws ApiError with fallback message when response body has no detail field", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(api("/error-endpoint")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      detail: "Request failed",
    });
  });

  it("attempts a token refresh on 401, then retries successfully", async () => {
    // First call: 401 response
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });
    // Refresh call: success
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    // Retry call: success
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ user_id: "refreshed" }),
    });

    const result = await api<{ user_id: string }>("/auth/me");
    expect(result).toEqual({ user_id: "refreshed" });
    // fetch called 3 times: original + refresh + retry
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it("throws ApiError on 401 when refresh also fails", async () => {
    // Original call: 401
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });
    // Refresh call: also fails
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });
    // Retry call: 401 again (skipRefreshRetry=true, so no further retry)
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });

    await expect(api("/auth/me")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });
  });

  it("propagates network errors (fetch throws)", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(api("/any")).rejects.toThrow("Failed to fetch");
  });

  it("sends Content-Type header when body is provided", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });

    await api("/auth/login", { method: "POST", body: { email: "a@b.com" } });

    const calledHeaders = mockFetch.mock.calls[0][1].headers;
    expect(calledHeaders["Content-Type"]).toBe("application/json");
  });

  it("does NOT send Content-Type header on GET requests without body", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });

    await api("/some/endpoint");

    const calledHeaders = mockFetch.mock.calls[0][1].headers;
    expect(calledHeaders["Content-Type"]).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// X-Org-Id header — Phase 12 flag gate
// ---------------------------------------------------------------------------

describe("api() X-Org-Id header", () => {
  const mockFetch = vi.fn();
  const STORAGE_KEY = "kendrew.activeOrgId";

  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch);
    Object.defineProperty(document, "cookie", {
      value: "",
      writable: true,
      configurable: true,
    });
    localStorage.setItem(STORAGE_KEY, "org-from-an-earlier-flag-on-session");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    mockFetch.mockReset();
    localStorage.removeItem(STORAGE_KEY);
    featureFlag.orgs = null;
  });

  async function headersFor(path: string): Promise<Record<string, string>> {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    await api(path);
    return mockFetch.mock.calls[0][1].headers;
  }

  it("sends the stored org id when the flag is on", async () => {
    featureFlag.orgs = true;
    const headers = await headersFor("/jobs");
    expect(headers["X-Org-Id"]).toBe("org-from-an-earlier-flag-on-session");
  });

  it("sends nothing when the flag is off, even with a stored org id", async () => {
    // Guards the flag-off/rollback path: a stale id must not scope requests
    // once the backend has stopped mounting the orgs routes. Without the
    // gate in api.ts this test fails with the stored id in the header.
    featureFlag.orgs = false;
    const headers = await headersFor("/jobs");
    expect(headers["X-Org-Id"]).toBeUndefined();
  });

  it("never sends the header on opt-out routes", async () => {
    featureFlag.orgs = true;
    const headers = await headersFor("/auth/me");
    expect(headers["X-Org-Id"]).toBeUndefined();
  });
});
