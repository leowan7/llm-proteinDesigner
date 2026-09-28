import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn() };
});

import { api, ApiError } from "@/lib/api";
import { ResetPasswordConfirm } from "./ResetPasswordConfirm";

async function submitNewPassword(updatePasswordError: unknown) {
  vi.mocked(api)
    .mockResolvedValueOnce({ message: "Token exchanged" })
    .mockRejectedValueOnce(updatePasswordError);
  render(
    <MemoryRouter>
      <ResetPasswordConfirm />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText(/^new password$/i), {
    target: { value: "Passw0rd!12" },
  });
  fireEvent.change(screen.getByLabelText(/^confirm password$/i), {
    target: { value: "Passw0rd!12" },
  });
  fireEvent.click(screen.getByRole("button", { name: /set new password/i }));
}

describe("ResetPasswordConfirm errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.location.hash = "#access_token=a&refresh_token=b&type=recovery";
  });

  it("shows the server's reason for a 400 instead of a connection error", async () => {
    await submitNewPassword(new ApiError(400, "Password should be at least 10 characters."));

    expect(await screen.findByText("Password should be at least 10 characters.")).toBeInTheDocument();
    expect(screen.queryByText(/unable to connect/i)).not.toBeInTheDocument();
    expect(api).toHaveBeenLastCalledWith("/auth/update-password", {
      method: "POST",
      body: { password: "Passw0rd!12" },
      skipRefreshRetry: true,
    });
  });

  it("says Unable to connect when no response arrived", async () => {
    await submitNewPassword(new TypeError("Failed to fetch"));

    expect(await screen.findByText(/unable to connect/i)).toBeInTheDocument();
  });
});
