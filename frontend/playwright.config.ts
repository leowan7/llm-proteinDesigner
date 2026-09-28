import { defineConfig, devices } from "@playwright/test";

import {
  FLAG_OFF_URL,
  FLAG_ON_API,
  FLAG_ON_URL,
  consentState,
} from "./e2e/stacks";

/**
 * Two projects, two stacks (see e2e/stacks.ts):
 *
 *   chromium       every spec except organizations.spec.ts, against the
 *                  flag-OFF stack — this is the coverage that proves the
 *                  Phase 12 landing leaves single-tenant behaviour alone.
 *   chromium-orgs  organizations.spec.ts only, against the flag-ON stack.
 *
 * The org spec is selected by testMatch and excluded from the default project
 * by testIgnore, so it runs exactly once, with the flag on, and is never
 * skipped.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 30000,
  use: {
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /organizations\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FLAG_OFF_URL,
        storageState: consentState(FLAG_OFF_URL),
      },
    },
    {
      name: "chromium-orgs",
      testMatch: /organizations\.spec\.ts/,
      // The teams flow chains sign-in, invite, accept and two full-page
      // reloads per step, so it needs more than the 30s default.
      timeout: 120000,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FLAG_ON_URL,
        storageState: consentState(FLAG_ON_URL),
      },
    },
  ],
  webServer: [
    {
      command: "npm run dev",
      url: FLAG_OFF_URL,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev -- --port 5174 --strictPort",
      url: FLAG_ON_URL,
      reuseExistingServer: !process.env.CI,
      // Vite exposes VITE_*-prefixed process env through import.meta.env, so
      // this is what points the second frontend at the flag-on backend.
      env: { VITE_API_BASE: FLAG_ON_API },
    },
  ],
});
