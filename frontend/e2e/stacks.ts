/**
 * E2E stack topology — one source of truth for playwright.config.ts and the
 * specs that build their own browser contexts.
 *
 * Two stacks run side by side in CI:
 *
 *   flag OFF  frontend :5173 -> backend :8000   every spec except organizations
 *   flag ON   frontend :5174 -> backend :8001   organizations.spec.ts only
 *
 * The frontend reads its API base from VITE_API_BASE at serve time, so a
 * second backend needs a second frontend. Keeping the flag-off pair as the
 * default means the existing specs keep proving that the Phase 12 landing does
 * not change single-tenant behaviour.
 */

import { COOKIE_CONSENT_KEY } from "@/lib/cookieConsent";

const env = (name: string, fallback: string): string =>
  process.env[name] ?? fallback;

/** Frontend talking to the flag-off backend. Default for every spec. */
export const FLAG_OFF_URL = env("PHASE12_BASE_URL", "http://localhost:5173");

/** Frontend talking to the flag-on backend. organizations.spec.ts only. */
export const FLAG_ON_URL = env("PHASE12_ORGS_BASE_URL", "http://localhost:5174");

/** Backend with ORGANIZATIONS_ENABLED=true. */
export const FLAG_ON_API = env("PHASE12_ORGS_API_BASE", "http://localhost:8001");

/**
 * Pre-dismissed cookie consent for one origin. The banner is a fixed bar
 * pinned to the bottom of the viewport (CookieConsentBanner.tsx), so while it
 * is up it covers whatever sits in that strip; accepting up front keeps it out
 * of the way of the long click chains these specs run.
 *
 * The key comes from the app rather than a literal: it was renamed
 * kendrew.* -> bindwave.* by the rebrand, and playwright.config.ts went on
 * seeding the old one, which made this pre-dismissal a no-op. Schema must match
 * `CookieConsentRecord`, and localStorage is origin-keyed, so each frontend
 * origin needs its own entry.
 */
export function consentState(origin: string) {
  return {
    cookies: [],
    origins: [
      {
        origin,
        localStorage: [
          {
            name: COOKIE_CONSENT_KEY,
            value: JSON.stringify({
              version: "v1",
              accepted_at: "2026-01-01T00:00:00.000Z",
              cookies_version: "2026-04-23",
            }),
          },
        ],
      },
    ],
  };
}
