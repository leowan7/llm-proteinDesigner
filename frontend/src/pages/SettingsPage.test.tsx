import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SettingsPage } from "./SettingsPage";

// Mock the user API module so no real HTTP calls are made.
// Plan 10-04 adds requestDataExport / getExportStatus / requestAccountDeletion /
// cancelAccountDeletion — all mocked here so PrivacyTab renders without network.
vi.mock("@/lib/user", () => ({
  getSettings: vi.fn().mockResolvedValue({
    email: "test@example.com",
    display_name: "Test User",
    notification_preferences: { job_complete: true, job_failure: true },
    deletion_requested_at: null,
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
  // Plan 10-04 GDPR helpers
  requestDataExport: vi.fn().mockResolvedValue({
    status: "pending",
    message: "Export is being prepared; you will receive an email when it is ready.",
  }),
  getExportStatus: vi.fn().mockResolvedValue({ status: "none" }),
  requestAccountDeletion: vi.fn().mockResolvedValue({
    deletion_scheduled_for: "2026-05-23T12:00:00Z",
  }),
  cancelAccountDeletion: vi.fn().mockResolvedValue({ cancelled: true }),
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

/**
 * Plan 10-04 Task 5 — Privacy tab content.
 *
 * Verifies the Privacy tab renders Export Data + Delete Account controls,
 * that the delete-confirmation dialog gates on the literal phrase, and that
 * the pending-deletion banner appears when deletion_requested_at is set.
 */
describe("SettingsPage Privacy tab (Plan 10-04)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function renderPrivacy() {
    const { container } = render(
      <MemoryRouter initialEntries={["/settings?tab=privacy"]}>
        <SettingsPage />
      </MemoryRouter>,
    );
    // Wait for initial getSettings() to resolve and the tab body to appear.
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /your data/i })).toBeInTheDocument();
    });
    return container;
  }

  it("renders Export my data and Delete my account sections", async () => {
    await renderPrivacy();
    expect(screen.getByRole("button", { name: /export my data/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^delete my account$/i })).toBeInTheDocument();
  });

  it("clicking 'Export my data' calls requestDataExport and shows pending message", async () => {
    const userLib = await import("@/lib/user");
    await renderPrivacy();
    fireEvent.click(screen.getByRole("button", { name: /export my data/i }));
    await waitFor(() => {
      expect(userLib.requestDataExport).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByText(/export is being prepared/i),
    ).toBeInTheDocument();
  });

  it("clicking 'Delete my account' opens the confirmation dialog", async () => {
    await renderPrivacy();
    fireEvent.click(screen.getByRole("button", { name: /^delete my account$/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /delete account\?/i })).toBeInTheDocument();
    });
  });

  it("submit is disabled until the exact phrase is typed", async () => {
    await renderPrivacy();
    fireEvent.click(screen.getByRole("button", { name: /^delete my account$/i }));
    const confirmInput = await screen.findByLabelText(/confirmation/i);

    // Wrong casing — submit stays disabled.
    fireEvent.change(confirmInput, { target: { value: "delete my account" } });
    const submitButton = screen.getByRole("button", { name: /schedule deletion/i });
    expect(submitButton).toBeDisabled();

    // Exact phrase — submit enabled.
    fireEvent.change(confirmInput, { target: { value: "DELETE MY ACCOUNT" } });
    expect(submitButton).not.toBeDisabled();
  });

  it("submitting the exact phrase calls requestAccountDeletion", async () => {
    const userLib = await import("@/lib/user");
    await renderPrivacy();
    fireEvent.click(screen.getByRole("button", { name: /^delete my account$/i }));
    const confirmInput = await screen.findByLabelText(/confirmation/i);
    fireEvent.change(confirmInput, { target: { value: "DELETE MY ACCOUNT" } });
    fireEvent.click(screen.getByRole("button", { name: /schedule deletion/i }));
    await waitFor(() => {
      expect(userLib.requestAccountDeletion).toHaveBeenCalledWith("DELETE MY ACCOUNT");
    });
  });

  it("renders pending-deletion banner + Cancel button when deletion_requested_at is set", async () => {
    const userLib = await import("@/lib/user");
    (userLib.getSettings as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      email: "test@example.com",
      display_name: "Test User",
      notification_preferences: { job_complete: true, job_failure: true },
      deletion_requested_at: "2026-04-20T12:00:00Z",
    });

    render(
      <MemoryRouter initialEntries={["/settings?tab=privacy"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /account deletion scheduled/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /cancel deletion/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /cancel deletion/i }));
    await waitFor(() => {
      expect(userLib.cancelAccountDeletion).toHaveBeenCalledTimes(1);
    });
  });
});
