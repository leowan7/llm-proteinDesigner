import { describe, it, expect, vi, beforeEach } from "vitest";
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

describe("Login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function submitLogin(error: unknown) {
    vi.mocked(api).mockRejectedValueOnce(error);
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "Passw0rd!12" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
  }

  it("shows a 401's detail as the server sent it", async () => {
    await submitLogin(new ApiError(401, "Verify your email before signing in."));

    expect(await screen.findByText("Verify your email before signing in.")).toBeInTheDocument();
    expect(screen.queryByText("Incorrect email or password.")).not.toBeInTheDocument();
  });

  it("shows a non-credential failure distinctly", async () => {
    const detail = "We could not sign you in right now. Try again in a moment.";
    await submitLogin(new ApiError(400, detail));

    expect(await screen.findByText(detail)).toBeInTheDocument();
  });

  it("does not try a token refresh after a failed sign-in", async () => {
    await submitLogin(new ApiError(401, "Incorrect email or password."));

    await screen.findByText("Incorrect email or password.");
    expect(api).toHaveBeenCalledWith("/auth/login", {
      method: "POST",
      body: { email: "a@example.com", password: "Passw0rd!12" },
      skipRefreshRetry: true,
    });
  });

  it("shows the notice it was sent to", () => {
    const notice = "Your password has been updated. Sign in with your new password.";
    render(
      <MemoryRouter initialEntries={[{ pathname: "/login", state: { notice } }]}>
        <Login />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent(notice);
  });

  it("marks the inputs for password managers", () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText(/email/i)).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("autocomplete", "current-password");
  });
});
