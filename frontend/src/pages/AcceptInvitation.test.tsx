/**
 * AcceptInvitation §6.2 branch tests.
 *
 * Verifies the four documented branches render the expected UI:
 *   1. signed in + email matches → "Join {Org} as {role}" with Accept button
 *   2. signed in + email mismatch → "Wrong account" + "Sign out and sign in" CTA
 *   3. signed out + valid token → "Sign in" + "Create account" CTAs with token
 *   4. invalid token → reason-specific message (expired/revoked/etc.)
 *
 * Plus the flag-off landing: with organizations_enabled=false the page
 * renders nothing and calls neither /invitations/preview nor /auth/me.
 *
 * Strategy: mock previewInvitation() to return the desired preview shape,
 * stub api() so the /auth/me probe resolves to either the signed-in user
 * (and we control the email) or rejects as 401, and mock @/lib/features so
 * the /health flag probe is controlled rather than fetched.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AcceptInvitation } from "./AcceptInvitation";
import { ApiError } from "@/lib/api";

vi.mock("@/lib/organizations", () => ({
  previewInvitation: vi.fn(),
  acceptInvitation: vi.fn(),
}));

const featureFlag = vi.hoisted(() => ({ orgs: true }));
vi.mock("@/lib/features", () => ({
  organizationsEnabled: async () => featureFlag.orgs,
  organizationsEnabledSync: () => featureFlag.orgs,
  useOrganizationsEnabled: () => featureFlag.orgs,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: vi.fn(),
  };
});

import { previewInvitation } from "@/lib/organizations";
import { api } from "@/lib/api";

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <AcceptInvitation />
    </MemoryRouter>,
  );
}

describe("AcceptInvitation", () => {
  beforeEach(() => {
    vi.mocked(previewInvitation).mockReset();
    vi.mocked(api).mockReset();
    featureFlag.orgs = true;
  });

  afterEach(() => {
    vi.mocked(previewInvitation).mockReset();
    vi.mocked(api).mockReset();
  });

  it("renders the Accept CTA when signed in and the email matches", async () => {
    vi.mocked(previewInvitation).mockResolvedValueOnce({
      valid: true,
      organization_name: "Acme",
      role: "scientist",
      email: "new@example.com",
    });
    vi.mocked(api).mockResolvedValueOnce({
      user_id: "u1",
      email: "new@example.com",
    });

    renderAt("/invitations/accept?token=abc");

    await waitFor(() => {
      expect(screen.getByText(/Join Acme/i)).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /accept invitation/i }),
    ).toBeInTheDocument();
  });

  it("renders the wrong-account branch when signed in with a different email", async () => {
    vi.mocked(previewInvitation).mockResolvedValueOnce({
      valid: true,
      organization_name: "Acme",
      role: "scientist",
      email: "invited@example.com",
    });
    vi.mocked(api).mockResolvedValueOnce({
      user_id: "u2",
      email: "someone-else@example.com",
    });

    renderAt("/invitations/accept?token=abc");

    await waitFor(() => {
      expect(screen.getByText(/wrong account/i)).toBeInTheDocument();
    });
    expect(
      screen.getByText(/this invitation is for/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /sign out and sign in as invited@example.com/i }),
    ).toBeInTheDocument();
  });

  it("renders sign-in + create-account CTAs when signed out", async () => {
    vi.mocked(previewInvitation).mockResolvedValueOnce({
      valid: true,
      organization_name: "Acme",
      role: "scientist",
      email: "new@example.com",
    });
    vi.mocked(api).mockRejectedValueOnce(new ApiError(401, "Not authenticated"));

    renderAt("/invitations/accept?token=abc");

    await waitFor(() => {
      expect(screen.getByText(/you've been invited/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /^sign in$/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create account/i }),
    ).toBeInTheDocument();
  });

  it("renders the invalid branch with a reason-specific message", async () => {
    vi.mocked(previewInvitation).mockResolvedValueOnce({
      valid: false,
      reason: "expired",
    });
    vi.mocked(api).mockRejectedValueOnce(new ApiError(401, "Not authenticated"));

    renderAt("/invitations/accept?token=abc");

    await waitFor(() => {
      expect(screen.getByText(/invitation unavailable/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/expired/i)).toBeInTheDocument();
  });

  it("renders revoked, already_accepted, and not_found reasons distinctly", async () => {
    const reasons = [
      { reason: "revoked", expected: /revoked/i },
      { reason: "already_accepted", expected: /already been accepted/i },
      { reason: "not_found", expected: /not found/i },
    ] as const;

    for (const { reason, expected } of reasons) {
      vi.mocked(previewInvitation).mockResolvedValueOnce({
        valid: false,
        reason,
      });
      vi.mocked(api).mockRejectedValueOnce(new ApiError(401, "Not authenticated"));

      const { unmount } = renderAt("/invitations/accept?token=abc");
      await waitFor(() => {
        expect(screen.getByText(expected)).toBeInTheDocument();
      });
      unmount();
      vi.mocked(previewInvitation).mockReset();
      vi.mocked(api).mockReset();
    }
  });

  it("renders nothing and calls no endpoint when the flag is off", async () => {
    featureFlag.orgs = false;

    const { container } = renderAt("/invitations/accept?token=abc");

    // Nothing to await: the page short-circuits before any request.
    expect(container.textContent).toBe("");
    expect(vi.mocked(previewInvitation)).not.toHaveBeenCalled();
    expect(vi.mocked(api)).not.toHaveBeenCalled();
  });
});
