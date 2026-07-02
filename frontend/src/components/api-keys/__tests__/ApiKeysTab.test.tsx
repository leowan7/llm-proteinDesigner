/**
 * ApiKeysTab tests (Plan 13-06).
 *
 * Covers list rendering (empty + populated), the idle-key badge (D-04),
 * opening the create modal, and graceful error handling on list failure.
 * The api-keys lib is mocked so no real HTTP is made.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("@/lib/api-keys", () => ({
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

import { ApiKeysTab } from "../ApiKeysTab";
import { listApiKeys } from "@/lib/api-keys";

const NOW = Date.now();
const FRESH_ISO = new Date(NOW - 2 * 24 * 3600 * 1000).toISOString(); // 2d ago
const STALE_ISO = new Date(NOW - 31 * 24 * 3600 * 1000).toISOString(); // 31d ago

const twoKeys = [
  {
    id: "k1",
    name: "Fresh key",
    prefix: "bw_live_aaaa",
    role: "owner",
    created_at: "2026-06-01T00:00:00Z",
    last_used_at: FRESH_ISO,
  },
  {
    id: "k2",
    name: "Stale key",
    prefix: "bw_live_bbbb",
    role: "owner",
    created_at: "2026-05-01T00:00:00Z",
    last_used_at: STALE_ISO,
  },
];

describe("ApiKeysTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders an empty state when listApiKeys returns []", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([]);
    render(<ApiKeysTab />);
    await waitFor(() => {
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /create new key/i }),
    ).toBeInTheDocument();
  });

  it("renders a row per key when listApiKeys returns 2 keys", async () => {
    vi.mocked(listApiKeys).mockResolvedValue(twoKeys);
    render(<ApiKeysTab />);
    await waitFor(() => {
      expect(screen.getByText("Fresh key")).toBeInTheDocument();
    });
    expect(screen.getByText("Stale key")).toBeInTheDocument();
    // Both revoke buttons present (one per row).
    expect(
      screen.getAllByRole("button", { name: /revoke/i }).length,
    ).toBe(2);
  });

  it("renders an 'Unused' badge for a key last used > 30d ago", async () => {
    vi.mocked(listApiKeys).mockResolvedValue(twoKeys);
    render(<ApiKeysTab />);
    await waitFor(() => {
      expect(screen.getByText("Stale key")).toBeInTheDocument();
    });
    // The stale key (31d) gets the badge; the fresh one (2d) does not.
    expect(screen.getByText(/unused 3\dd/i)).toBeInTheDocument();
  });

  it("clicking 'Create new key' opens the CreateApiKeyModal", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([]);
    render(<ApiKeysTab />);
    await waitFor(() => {
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /create new key/i }));
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /create api key/i }),
      ).toBeInTheDocument();
    });
  });

  it("handles a list fetch error gracefully", async () => {
    vi.mocked(listApiKeys).mockRejectedValue(new Error("boom"));
    render(<ApiKeysTab />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/boom/i);
    });
    // Still shows the empty state + Create button — the tab stays usable.
    expect(
      screen.getByRole("button", { name: /create new key/i }),
    ).toBeInTheDocument();
  });
});
