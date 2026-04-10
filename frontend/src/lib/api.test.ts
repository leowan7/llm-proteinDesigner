import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiError, api } from "./api";

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
