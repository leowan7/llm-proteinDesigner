/**
 * OrganizationSwitcher smoke tests.
 *
 * Asserts:
 *   - Hidden when orgs.length <= 1 (solo user / single-tenant fallback).
 *   - Rendered when orgs.length >= 2.
 *   - Clicking a non-active item writes localStorage and triggers reload.
 *
 * Strategy: mock fetchMyOrgs() at the @/lib/organizations boundary so the
 * provider resolves synchronously and the switcher sees the controlled
 * orgs list.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { OrganizationSwitcher } from "./OrganizationSwitcher";
import { OrgProvider } from "./OrganizationContext";

vi.mock("@/lib/organizations", () => ({
  fetchMyOrgs: vi.fn(),
}));

import { fetchMyOrgs } from "@/lib/organizations";

const STORAGE_KEY = "kendrew.activeOrgId";

function renderSwitcher() {
  return render(
    <MemoryRouter>
      <OrgProvider>
        <OrganizationSwitcher />
      </OrgProvider>
    </MemoryRouter>,
  );
}

describe("OrganizationSwitcher", () => {
  let originalLocation: Location;
  let reloadMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    originalLocation = window.location;
    reloadMock = vi.fn();
    // Replace window.location with a stub whose reload() we can spy on.
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { ...originalLocation, reload: reloadMock },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
    vi.mocked(fetchMyOrgs).mockReset();
  });

  it("renders nothing when the user has only one org", async () => {
    vi.mocked(fetchMyOrgs).mockResolvedValueOnce([
      { id: "p1", name: "Personal", role: "owner", is_personal: true },
    ]);

    const { container } = renderSwitcher();
    await waitFor(() => {
      expect(vi.mocked(fetchMyOrgs)).toHaveBeenCalled();
    });
    // No trigger button rendered.
    expect(container.textContent).toBe("");
  });

  it("renders a trigger when the user has multiple orgs", async () => {
    vi.mocked(fetchMyOrgs).mockResolvedValueOnce([
      { id: "p1", name: "Personal", role: "owner", is_personal: true },
      { id: "a1", name: "Acme", role: "scientist", is_personal: false },
    ]);

    renderSwitcher();
    // The personal org is selected by default; its name appears on the trigger.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /switch organization/i })).toBeInTheDocument();
    });
  });

  it("clicking an org writes localStorage and triggers a reload", async () => {
    vi.mocked(fetchMyOrgs).mockResolvedValueOnce([
      { id: "p1", name: "Personal", role: "owner", is_personal: true },
      { id: "a1", name: "Acme", role: "scientist", is_personal: false },
    ]);

    renderSwitcher();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /switch organization/i })).toBeInTheDocument();
    });

    // Open the dropdown.
    fireEvent.click(screen.getByRole("button", { name: /switch organization/i }));

    // Locate the Acme item by its test id and click.
    await waitFor(() => {
      expect(screen.getByTestId("org-switcher-item-a1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("org-switcher-item-a1"));

    expect(localStorage.getItem(STORAGE_KEY)).toBe("a1");
    expect(reloadMock).toHaveBeenCalled();
  });
});
