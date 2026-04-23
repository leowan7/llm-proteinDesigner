import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  readConsent,
  writeConsent,
  COOKIE_CONSENT_KEY,
  COOKIE_CONSENT_EVENT,
} from "@/lib/cookieConsent";
import { CookieConsentProvider } from "./CookieConsentProvider";
import { COOKIES_VERSION } from "@/pages/legal/versions";

function renderProvider() {
  return render(
    <MemoryRouter>
      <CookieConsentProvider>
        <div data-testid="app-content">app content</div>
      </CookieConsentProvider>
    </MemoryRouter>,
  );
}

describe("cookieConsent helpers", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("readConsent returns null when localStorage is empty", () => {
    expect(readConsent()).toBeNull();
  });

  it("writeConsent stores a v1 record under the canonical key", () => {
    const record = writeConsent();
    expect(record.version).toBe("v1");
    expect(record.cookies_version).toBe(COOKIES_VERSION);
    expect(record.accepted_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    const raw = localStorage.getItem(COOKIE_CONSENT_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.version).toBe("v1");
    expect(parsed.cookies_version).toBe(COOKIES_VERSION);
  });

  it("readConsent returns null when the stored record has a non-v1 version", () => {
    localStorage.setItem(
      COOKIE_CONSENT_KEY,
      JSON.stringify({ version: "v2", accepted_at: "x", cookies_version: "x" }),
    );
    expect(readConsent()).toBeNull();
  });

  it("readConsent returns null when the stored blob is invalid JSON", () => {
    localStorage.setItem(COOKIE_CONSENT_KEY, "{not-json");
    expect(readConsent()).toBeNull();
  });
});

describe("CookieConsentBanner via CookieConsentProvider", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the banner when no prior consent is stored", () => {
    renderProvider();
    const region = screen.getByRole("region", { name: /cookie/i });
    expect(region).toBeInTheDocument();
  });

  it("does not render the banner when a valid consent record is already present", () => {
    writeConsent();
    renderProvider();
    expect(screen.queryByRole("region", { name: /cookie/i })).toBeNull();
  });

  it("discloses the three strictly-necessary cookies by name", () => {
    renderProvider();
    const region = screen.getByRole("region", { name: /cookie/i });
    expect(region.textContent).toContain("access_token");
    expect(region.textContent).toContain("refresh_token");
    expect(region.textContent).toContain("csrftoken");
  });

  it("contains a Learn more link to /legal/cookies", () => {
    renderProvider();
    const link = screen.getByRole("link", { name: /learn more/i });
    expect(link.getAttribute("href")).toBe("/legal/cookies");
  });

  it("persists consent and hides the banner when 'Got it' is clicked", () => {
    renderProvider();
    const button = screen.getByRole("button", { name: /got it/i });
    fireEvent.click(button);

    expect(screen.queryByRole("region", { name: /cookie/i })).toBeNull();
    const stored = readConsent();
    expect(stored).not.toBeNull();
    expect(stored!.version).toBe("v1");
    expect(stored!.cookies_version).toBe(COOKIES_VERSION);
  });

  it("re-renders the banner when the open-consent event is dispatched, even after prior acceptance", () => {
    writeConsent();
    renderProvider();
    expect(screen.queryByRole("region", { name: /cookie/i })).toBeNull();

    act(() => {
      window.dispatchEvent(new CustomEvent(COOKIE_CONSENT_EVENT));
    });

    expect(screen.getByRole("region", { name: /cookie/i })).toBeInTheDocument();
  });
});
