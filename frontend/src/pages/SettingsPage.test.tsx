import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SettingsPage } from "./SettingsPage";

// Mock the user API module so no real HTTP calls are made.
vi.mock("@/lib/user", () => ({
  getSettings: vi.fn().mockResolvedValue({
    email: "test@example.com",
    display_name: "Test User",
    notification_preferences: { job_complete: true, job_failure: true },
  }),
  updateSettings: vi.fn().mockResolvedValue(undefined),
  getUsage: vi.fn().mockResolvedValue({
    period_start: "2026-04-01T00:00:00Z",
    job_count: 0,
    total_spend_usd: 0,
    recent_charges: [],
  }),
  getPaymentMethod: vi.fn().mockResolvedValue({
    has_payment_method: false,
  }),
  createPortalSession: vi.fn().mockResolvedValue("https://billing.stripe.com/portal/test"),
}));

describe("SettingsPage (smoke test)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderSettings() {
    return render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );
  }

  it("renders without crashing", () => {
    const { container } = renderSettings();
    expect(container).toBeTruthy();
  });

  it("renders the Settings heading", () => {
    renderSettings();
    const heading = screen.getByRole("heading", { name: /settings/i });
    expect(heading).toBeInTheDocument();
  });

  it("renders the Account tab trigger", () => {
    renderSettings();
    const accountTab = screen.getByRole("tab", { name: /account/i });
    expect(accountTab).toBeInTheDocument();
  });

  it("renders the Billing tab trigger", () => {
    renderSettings();
    const billingTab = screen.getByRole("tab", { name: /billing/i });
    expect(billingTab).toBeInTheDocument();
  });

  it("renders the Usage tab trigger", () => {
    renderSettings();
    const usageTab = screen.getByRole("tab", { name: /usage/i });
    expect(usageTab).toBeInTheDocument();
  });
});

/**
 * Plan 10-06 Task 3 — deep-link coverage.
 *
 * Hardens the `/settings?tab=privacy` URL embedded in the cancel-deletion
 * email sent by Plan 10-04 Task 3. If this deep-link ever regresses (tab
 * param ignored, Privacy tab removed, or the validation whitelist drops
 * "privacy"), the email CTA silently breaks. These tests catch that at CI.
 */
describe("SettingsPage deep-link ?tab=", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderAt(url: string) {
    return render(
      <MemoryRouter initialEntries={[url]}>
        <SettingsPage />
      </MemoryRouter>,
    );
  }

  it("activates the Privacy tab when rendered at /settings?tab=privacy", () => {
    renderAt("/settings?tab=privacy");
    const privacyTab = screen.getByRole("tab", { name: /^privacy$/i });
    expect(privacyTab).toHaveAttribute("aria-selected", "true");
  });

  it("activates the Account tab (default) when rendered at /settings with no query param", () => {
    renderAt("/settings");
    const accountTab = screen.getByRole("tab", { name: /^account$/i });
    expect(accountTab).toHaveAttribute("aria-selected", "true");
  });

  it("falls back to the Account tab when ?tab=<invalid> is supplied", () => {
    renderAt("/settings?tab=bogus");
    const accountTab = screen.getByRole("tab", { name: /^account$/i });
    expect(accountTab).toHaveAttribute("aria-selected", "true");
    // Privacy tab is present but not selected.
    const privacyTab = screen.getByRole("tab", { name: /^privacy$/i });
    expect(privacyTab).toHaveAttribute("aria-selected", "false");
  });
});
