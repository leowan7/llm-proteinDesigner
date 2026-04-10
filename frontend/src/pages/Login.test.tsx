import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
});
