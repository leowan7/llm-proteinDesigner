/**
 * organizations.ts unit tests.
 *
 * Mocks the global fetch + document.cookie so the api() helper can run under
 * jsdom and we can assert request shape (method, body, X-Org-Id header) plus
 * the parsed response envelope.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchMyOrgs,
  createOrg,
  acceptInvitation,
  previewInvitation,
  inviteMember,
} from "./organizations";

const ORG_STORAGE_KEY = "kendrew.activeOrgId";

describe("organizations API client", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch);
    Object.defineProperty(document, "cookie", {
      value: "",
      writable: true,
      configurable: true,
    });
    try {
      localStorage.removeItem(ORG_STORAGE_KEY);
    } catch {
      // ignore
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    mockFetch.mockReset();
  });

  it("fetchMyOrgs returns the orgs array from the response envelope", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        orgs: [
          { id: "a", name: "Acme", role: "owner", is_personal: false },
          { id: "b", name: "Personal", role: "owner", is_personal: true },
        ],
      }),
    });

    const orgs = await fetchMyOrgs();
    expect(orgs).toHaveLength(2);
    expect(orgs[0].name).toBe("Acme");
    expect(orgs[1].is_personal).toBe(true);
  });

  it("fetchMyOrgs request does NOT carry the X-Org-Id header", async () => {
    // /organizations/mine is on the opt-out list because it lists ALL orgs.
    localStorage.setItem(ORG_STORAGE_KEY, "should-not-leak");
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ orgs: [] }),
    });

    await fetchMyOrgs();
    const headers = mockFetch.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["X-Org-Id"]).toBeUndefined();
  });

  it("createOrg POSTs to /organizations with name body and NO X-Org-Id", async () => {
    // POST /organizations creates a NEW org — no active-org context to inject.
    localStorage.setItem(ORG_STORAGE_KEY, "should-not-leak");
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        id: "x",
        name: "Acme",
        role: "owner",
        is_personal: false,
      }),
    });

    const result = await createOrg("Acme");
    expect(result.id).toBe("x");

    const call = mockFetch.mock.calls[0];
    expect(call[1].method).toBe("POST");
    expect(JSON.parse(call[1].body)).toEqual({ name: "Acme" });
    const headers = call[1].headers as Record<string, string>;
    expect(headers["X-Org-Id"]).toBeUndefined();
  });

  it("inviteMember sends X-Org-Id since /organizations/{id}/invitations is org-scoped", async () => {
    localStorage.setItem(ORG_STORAGE_KEY, "org-abc");
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ id: "invite-1" }),
    });

    await inviteMember("org-abc", "new@example.com", "scientist");
    const headers = mockFetch.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["X-Org-Id"]).toBe("org-abc");
  });

  it("acceptInvitation POSTs to /invitations/accept without X-Org-Id", async () => {
    // /invitations/* is on the opt-out list.
    localStorage.setItem(ORG_STORAGE_KEY, "stale-org");
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        organization_id: "new-org",
        role: "scientist",
      }),
    });

    const result = await acceptInvitation("token-xyz");
    expect(result.organization_id).toBe("new-org");
    const call = mockFetch.mock.calls[0];
    expect(call[1].method).toBe("POST");
    expect(JSON.parse(call[1].body)).toEqual({ token: "token-xyz" });
    const headers = call[1].headers as Record<string, string>;
    expect(headers["X-Org-Id"]).toBeUndefined();
  });

  it("previewInvitation GETs /invitations/preview with the token in the query string", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        valid: true,
        organization_name: "Acme",
        role: "scientist",
        email: "new@example.com",
      }),
    });

    const preview = await previewInvitation("token with space");
    expect(preview.valid).toBe(true);
    const call = mockFetch.mock.calls[0];
    const url = call[0] as string;
    expect(url).toMatch(/\/invitations\/preview\?token=token%20with%20space/);
  });
});
