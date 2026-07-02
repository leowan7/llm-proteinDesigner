/**
 * CreateApiKeyModal tests (Plan 13-06, API-01).
 *
 * The load-bearing assertions are the cannot-dismiss invariant: in stage 2,
 * before the confirmation checkbox is checked, neither Escape nor a backdrop
 * click may close the modal (onOpenChange must NOT be called with false).
 * A small controlled harness observes onOpenChange.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { useState } from "react";

vi.mock("@/lib/api-keys", () => ({
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

import { CreateApiKeyModal } from "../CreateApiKeyModal";
import { createApiKey, type CreatedApiKey } from "@/lib/api-keys";

const CREATED: CreatedApiKey = {
  id: "k1",
  name: "Local dev",
  prefix: "bw_live_abcd",
  role: "owner",
  created_at: "2026-06-01T00:00:00Z",
  last_used_at: null,
  plaintext: "bw_live_abcdefghijklmnopqrstuvwxyz012345",
};

/** Controlled harness so onOpenChange / onCreated are observable. */
function Harness({
  onOpenChange,
  onCreated,
}: {
  onOpenChange: (open: boolean) => void;
  onCreated: (key: CreatedApiKey) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <CreateApiKeyModal
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        onOpenChange(next);
      }}
      onCreated={onCreated}
    />
  );
}

async function advanceToStage2() {
  vi.mocked(createApiKey).mockResolvedValue(CREATED);
  const onOpenChange = vi.fn();
  const onCreated = vi.fn();
  render(<Harness onOpenChange={onOpenChange} onCreated={onCreated} />);

  fireEvent.change(screen.getByLabelText(/key name/i), {
    target: { value: "Local dev" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => {
    expect(screen.getByDisplayValue(CREATED.plaintext)).toBeInTheDocument();
  });
  return { onOpenChange, onCreated };
}

describe("CreateApiKeyModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom lacks clipboard by default; provide a spy-able stub.
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("stage 1 shows the name input and a Create button", () => {
    render(
      <CreateApiKeyModal open onOpenChange={vi.fn()} onCreated={vi.fn()} />,
    );
    expect(screen.getByLabelText(/key name/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^create$/i })).toBeInTheDocument();
  });

  it("clicking Create with a non-empty name calls createApiKey", async () => {
    vi.mocked(createApiKey).mockResolvedValue(CREATED);
    render(
      <CreateApiKeyModal open onOpenChange={vi.fn()} onCreated={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText(/key name/i), {
      target: { value: "Local dev" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() => {
      expect(createApiKey).toHaveBeenCalledWith("Local dev");
    });
  });

  it("after create, stage 2 shows the plaintext key and a Copy button", async () => {
    await advanceToStage2();
    expect(screen.getByDisplayValue(CREATED.plaintext)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("Close is disabled while the confirmation checkbox is unchecked", async () => {
    await advanceToStage2();
    expect(screen.getByRole("button", { name: /close/i })).toBeDisabled();
  });

  it("Close becomes enabled once the checkbox is checked", async () => {
    await advanceToStage2();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: /close/i })).not.toBeDisabled();
  });

  it("checking the box + Close calls onCreated and onOpenChange(false)", async () => {
    const { onOpenChange, onCreated } = await advanceToStage2();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith(CREATED);
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("pressing Escape in stage 2 without confirming does NOT close", async () => {
    const { onOpenChange } = await advanceToStage2();
    fireEvent.keyDown(document.body, { key: "Escape", code: "Escape" });
    // The dialog stays: plaintext still visible, and onOpenChange was never
    // called with false.
    expect(screen.getByDisplayValue(CREATED.plaintext)).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("clicking the backdrop in stage 2 without confirming does NOT close", async () => {
    const { onOpenChange } = await advanceToStage2();
    const overlay = document.querySelector('[data-slot="dialog-overlay"]');
    if (overlay) {
      fireEvent.pointerDown(overlay);
      fireEvent.click(overlay);
    }
    expect(screen.getByDisplayValue(CREATED.plaintext)).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("Copy calls navigator.clipboard.writeText with the plaintext", async () => {
    await advanceToStage2();
    fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        CREATED.plaintext,
      );
    });
  });
});
