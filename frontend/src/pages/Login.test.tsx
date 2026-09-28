import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn() };
});

import { api, ApiError } from "@/lib/api";
import { Login } from "./Login";

describe("Login (smoke test)", () => {
  function renderLogin() {
    return render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
  }

  it("renders without crashing", () => {
    const { container } = renderLogin();
    expect(container).toBeTruthy();
  });

  it("renders an email input field", () => {
    renderLogin();
    // The form uses a label "Email" associated with the input
    const emailInput = screen.getByLabelText(/email/i);
    expect(emailInput).toBeInTheDocument();
  });

  it("renders a password input field", () => {
    renderLogin();
    const passwordInput = screen.getByLabelText(/password/i);
    expect(passwordInput).toBeInTheDocument();
  });

  it("renders the submit button", () => {
    renderLogin();
    const submitButton = screen.getByRole("button", { name: /sign in/i });
    expect(submitButton).toBeInTheDocument();
  });

  it("renders a link to the sign-up page", () => {
    renderLogin();
    const createLink = screen.getByRole("link", { name: /create one/i });
    expect(createLink).toBeInTheDocument();
  });

  it("shows a 403 that is not about email verification as the server sent it", async () => {
    const detail = "We could not verify this request. Refresh the page and try again.";
    vi.mocked(api).mockRejectedValueOnce(new ApiError(403, detail));
    renderLogin();
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "Passw0rd!12" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /go to verification page/i })).not.toBeInTheDocument();
  });
});
