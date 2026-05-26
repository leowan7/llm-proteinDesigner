import { defineConfig, devices } from "@playwright/test";

// Pre-dismiss the cookie consent banner so its Dialog overlay doesn't
// intercept pointer events on clicks the tests issue against the app.
// Schema must match `CookieConsentRecord` in src/lib/cookieConsent.ts.
const COOKIE_CONSENT = JSON.stringify({
  version: "v1",
  accepted_at: "2026-01-01T00:00:00.000Z",
  cookies_version: "2026-04-23",
});

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 30000,
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    storageState: {
      cookies: [],
      origins: [
        {
          origin: "http://localhost:5173",
          localStorage: [{ name: "kendrew.cookie_consent.v1", value: COOKIE_CONSENT }],
        },
      ],
    },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
});
