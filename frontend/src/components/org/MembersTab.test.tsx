/**
 * MembersTab role-gating tests.
 *
 * Asserts that:
 *   - viewer sees the members list with no editing controls
 *   - scientist sees the members list with no editing controls
 *   - owner sees the invite form, per-row role select, Remove buttons
 *
 * Strategy: mock OrgContext to control the current user's role, and mock
 * fetchMembers() to return a fixed list. We don't exercise the mutation
 * paths here — those are covered by integration tests against the live
 * backend in Plan 12-06's Playwright run.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { MembersTab } from "./MembersTab";

vi.mock("./OrganizationContext", async () => {
  const actual = await vi.importActual<typeof import("./OrganizationContext")>(
    "./OrganizationContext",
  );
  return {
    ...actual,
    useOrgContext: vi.fn(),
  };
});

vi.mock("@/lib/organizations", () => ({
  fetchMembers: vi.fn(),
  inviteMember: vi.fn(),
  removeMember: vi.fn(),
  transferOwnership: vi.fn(),
  updateMemberRole: vi.fn(),
}));

import { useOrgContext } from "./OrganizationContext";
import { fetchMembers } from "@/lib/organizations";

const baseCtx = {
  orgs: [
    { id: "org-1", name: "Acme", role: "owner" as const, is_personal: false },
  ],
  activeOrgId: "org-1",
  activeOrg: { id: "org-1", name: "Acme", role: "owner" as const, is_personal: false },
  loading: false,
  refresh: vi.fn(),
  setActiveOrg: vi.fn(),
};

const sampleMembers = [
  {
    user_id: "u1",
    email: "owner@example.com",
    role: "owner" as const,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    user_id: "u2",
    email: "scientist@example.com",
    role: "scientist" as const,
    created_at: "2026-01-02T00:00:00Z",
  },
];

describe("MembersTab", () => {
  beforeEach(() => {
    vi.mocked(fetchMembers).mockResolvedValue(sampleMembers);
  });

  afterEach(() => {
    vi.mocked(useOrgContext).mockReset();
    vi.mocked(fetchMembers).mockReset();
  });

  it("renders members list with NO owner controls when current user is a viewer", async () => {
    vi.mocked(useOrgContext).mockReturnValue({
      ...baseCtx,
      role: "viewer",
      activeOrg: { ...baseCtx.activeOrg, role: "viewer" },
    });

    render(<MembersTab orgId="org-1" />);
    await waitFor(() => {
      expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    });

    // No invite form
    expect(screen.queryByLabelText(/invite member/i)).not.toBeInTheDocument();
    // No remove buttons
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
  });

  it("renders members list with NO owner controls when current user is a scientist", async () => {
    vi.mocked(useOrgContext).mockReturnValue({
      ...baseCtx,
      role: "scientist",
      activeOrg: { ...baseCtx.activeOrg, role: "scientist" },
    });

    render(<MembersTab orgId="org-1" />);
    await waitFor(() => {
      expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    });

    expect(screen.queryByLabelText(/invite member/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
  });

  it("renders invite form + role selects + remove buttons when current user is an owner", async () => {
    vi.mocked(useOrgContext).mockReturnValue({
      ...baseCtx,
      role: "owner",
    });

    render(<MembersTab orgId="org-1" />);
    await waitFor(() => {
      expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    });

    // Invite form is present
    expect(screen.getByRole("form", { name: /invite member/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/^email$/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send invitation/i })).toBeInTheDocument();

    // Per-row role select for each member
    expect(screen.getByLabelText(/role for scientist@example.com/i)).toBeInTheDocument();

    // Remove button per row (one for each of 2 sample members)
    expect(screen.getAllByRole("button", { name: /remove/i }).length).toBeGreaterThanOrEqual(2);
  });
});
