/**
 * RevokeConfirmModal tests (Plan 13-06, API-03).
 *
 * Covers the type-name-to-confirm gate: Revoke stays disabled until the
 * typed name exactly matches the key name, then the click calls revokeApiKey
 * and fires onRevoked.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("@/lib/api-keys", () => ({
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

import { RevokeConfirmModal } from "../RevokeConfirmModal";
import { revokeApiKey } from "@/lib/api-keys";

const KEY = {
  id: "k1",
  name: "Prod key",
  prefix: "bw_live_abcd",
  role: "owner",
  created_at: "2026-06-01T00:00:00Z",
  last_used_at: null,
};

describe("RevokeConfirmModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("Revoke is disabled when the name input is empty", () => {
    render(
      <RevokeConfirmModal
        apiKey={KEY}
        onOpenChange={vi.fn()}
        onRevoked={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /revoke key/i })).toBeDisabled();
  });

  it("Revoke is disabled when the typed name is wrong", () => {
    render(
      <RevokeConfirmModal
        apiKey={KEY}
        onOpenChange={vi.fn()}
        onRevoked={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/key name/i), {
      target: { value: "Wrong name" },
    });
    expect(screen.getByRole("button", { name: /revoke key/i })).toBeDisabled();
  });

  it("Revoke is enabled when the typed name matches exactly", () => {
    render(
      <RevokeConfirmModal
        apiKey={KEY}
        onOpenChange={vi.fn()}
        onRevoked={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/key name/i), {
      target: { value: "Prod key" },
    });
    expect(
      screen.getByRole("button", { name: /revoke key/i }),
    ).not.toBeDisabled();
  });

  it("clicking Revoke calls revokeApiKey with the key id", async () => {
    vi.mocked(revokeApiKey).mockResolvedValue(undefined);
    render(
      <RevokeConfirmModal
        apiKey={KEY}
        onOpenChange={vi.fn()}
        onRevoked={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/key name/i), {
      target: { value: "Prod key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /revoke key/i }));
    await waitFor(() => {
      expect(revokeApiKey).toHaveBeenCalledWith("k1");
    });
  });

  it("calls onRevoked after a successful revoke", async () => {
    vi.mocked(revokeApiKey).mockResolvedValue(undefined);
    const onRevoked = vi.fn();
    render(
      <RevokeConfirmModal
        apiKey={KEY}
        onOpenChange={vi.fn()}
        onRevoked={onRevoked}
      />,
    );
    fireEvent.change(screen.getByLabelText(/key name/i), {
      target: { value: "Prod key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /revoke key/i }));
    await waitFor(() => {
      expect(onRevoked).toHaveBeenCalledTimes(1);
    });
  });
});
