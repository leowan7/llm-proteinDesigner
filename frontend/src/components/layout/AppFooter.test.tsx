import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mock the cookieConsent module so we can assert the footer's re-open hook.
vi.mock("@/lib/cookieConsent", () => ({
  requestOpenConsent: vi.fn(),
}));

import { requestOpenConsent } from "@/lib/cookieConsent";
import { AppFooter } from "./AppFooter";

function renderFooter() {
  return render(
    <MemoryRouter>
      <AppFooter />
    </MemoryRouter>,
  );
}

describe("AppFooter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the four /legal/* links with correct hrefs", () => {
    renderFooter();

    const terms = screen.getByRole("link", { name: /^terms$/i });
    const privacy = screen.getByRole("link", { name: /^privacy$/i });
    const subprocessors = screen.getByRole("link", { name: /^subprocessors$/i });
    const cookies = screen.getByRole("link", { name: /^cookies$/i });

    expect(terms.getAttribute("href")).toBe("/legal/terms");
    expect(privacy.getAttribute("href")).toBe("/legal/privacy");
    expect(subprocessors.getAttribute("href")).toBe("/legal/subprocessors");
    expect(cookies.getAttribute("href")).toBe("/legal/cookies");
  });

  it("clicking 'Cookie preferences' calls requestOpenConsent", () => {
    renderFooter();

    const button = screen.getByRole("button", { name: /cookie preferences/i });
    fireEvent.click(button);

    expect(requestOpenConsent).toHaveBeenCalledTimes(1);
  });

  it("renders a copyright line for the current year", () => {
    renderFooter();
    const year = new Date().getFullYear();
    // The copyright line uses a © glyph plus the current year plus "Ranomics Inc."
    expect(
      screen.getByText(new RegExp(`©\\s*${year}\\s*Ranomics Inc\\.`, "i")),
    ).toBeInTheDocument();
  });

  it("renders inside a <footer> element with role=contentinfo", () => {
    renderFooter();
    const footer = screen.getByRole("contentinfo");
    expect(footer).toBeInTheDocument();
    expect(footer.tagName).toBe("FOOTER");
  });
});
