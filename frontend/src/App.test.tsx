import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn() };
});

import { api } from "@/lib/api";
import App from "./App";

const RECOVERY_URL = "/reset-password/confirm#access_token=a&refresh_token=b&type=recovery";

// Counts history writes. Past the budget it stops calling through, so an
// unfixed redirect loop ends here instead of hanging the test.
function renderAppAt(url: string, budget = 20) {
  window.history.replaceState(null, "", url);
  let calls = 0;
  for (const method of ["pushState", "replaceState"] as const) {
    const original = window.history[method].bind(window.history);
    vi.spyOn(window.history, method).mockImplementation((data, unused, target) => {
      calls += 1;
      if (calls <= budget) original(data, unused, target);
    });
  }
  render(<App />);
  return () => calls;
}

describe("App hash redirects", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.mocked(api).mockReset();
  });

  it("does not loop when a recovery link lands on the reset page", async () => {
    vi.mocked(api).mockResolvedValueOnce({ message: "Token exchanged" });
    const historyWrites = renderAppAt(RECOVERY_URL);

    expect(await screen.findByLabelText(/^new password$/i)).toBeInTheDocument();
    expect(historyWrites()).toBeLessThanOrEqual(2);
    expect(window.location.hash).toBe("");
  });

  it("goes to sign in with a notice once the new password is saved", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ message: "Token exchanged" })
      .mockResolvedValueOnce({ message: "Password updated." });
    renderAppAt(RECOVERY_URL);

    fireEvent.change(await screen.findByLabelText(/^new password$/i), {
      target: { value: "Passw0rd!12" },
    });
    fireEvent.change(screen.getByLabelText(/^confirm password$/i), {
      target: { value: "Passw0rd!12" },
    });
    fireEvent.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByText(/password has been updated/i)).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  it("keeps an expired link on the reset page and says so", async () => {
    renderAppAt(
      "/reset-password/confirm#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    );

    expect(await screen.findByText(/expired or was already used/i)).toBeInTheDocument();
    expect(window.location.pathname).toBe("/reset-password/confirm");
    expect(api).not.toHaveBeenCalled();
  });
});
