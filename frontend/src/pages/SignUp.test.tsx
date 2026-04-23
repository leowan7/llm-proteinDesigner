import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mock the api helper so no real HTTP call leaves the test.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: vi.fn().mockResolvedValue({ message: "ok" }),
  };
});

import { api } from "@/lib/api";
import { TOS_VERSION } from "@/pages/legal/versions";
import { SignUp } from "./SignUp";

function renderSignUp() {
  return render(
    <MemoryRouter>
      <SignUp />
    </MemoryRouter>,
  );
}

describe("SignUp — ToS acceptance gate (Plan 10-02)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the ToS checkbox unchecked by default", () => {
    renderSignUp();
    const checkbox = screen.getByRole("checkbox", { name: /agree to the terms of service/i });
    expect(checkbox).toBeInTheDocument();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
  });

  it("shows a validation error when the form is submitted without ticking the checkbox", async () => {
    renderSignUp();
    fireEvent.change(screen.getByLabelText(/^email$/i), {
      target: { value: "new@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: "Passw0rd!12" },
    });
    fireEvent.change(screen.getByLabelText(/confirm password/i), {
      target: { value: "Passw0rd!12" },
    });

    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/accept the terms of service and privacy policy/i),
      ).toBeInTheDocument();
    });
    expect(api).not.toHaveBeenCalled();
  });

  it("posts email, password and tos_version when the checkbox is ticked", async () => {
    renderSignUp();
    fireEvent.change(screen.getByLabelText(/^email$/i), {
      target: { value: "new@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: "Passw0rd!12" },
    });
    fireEvent.change(screen.getByLabelText(/confirm password/i), {
      target: { value: "Passw0rd!12" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /agree to the terms of service/i }));

    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith("/auth/signup", {
        method: "POST",
        body: {
          email: "new@example.com",
          password: "Passw0rd!12",
          tos_version: TOS_VERSION,
        },
      });
    });
  });

  it("renders links to /legal/terms and /legal/privacy in the checkbox label", () => {
    renderSignUp();
    const termsLink = screen.getByRole("link", { name: /terms of service/i });
    const privacyLink = screen.getByRole("link", { name: /privacy policy/i });
    expect(termsLink.getAttribute("href")).toBe("/legal/terms");
    expect(privacyLink.getAttribute("href")).toBe("/legal/privacy");
    expect(termsLink.getAttribute("target")).toBe("_blank");
    expect(privacyLink.getAttribute("target")).toBe("_blank");
  });
});
