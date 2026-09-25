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

const env = (name: string, fallback: string): string =>
  process.env[name] ?? fallback;

/** Frontend talking to the flag-off backend. Default for every spec. */
export const FLAG_OFF_URL = env("PHASE12_BASE_URL", "http://localhost:5173");

/** Frontend talking to the flag-on backend. organizations.spec.ts only. */
export const FLAG_ON_URL = env("PHASE12_ORGS_BASE_URL", "http://localhost:5174");

/** Backend with ORGANIZATIONS_ENABLED=true. */
export const FLAG_ON_API = env("PHASE12_ORGS_API_BASE", "http://localhost:8001");

/**
 * Pre-dismissed cookie consent for one origin, so the banner's Dialog overlay
 * does not intercept pointer events on clicks the tests issue against the app.
 * Schema must match `CookieConsentRecord` in src/lib/cookieConsent.ts, and
 * localStorage is origin-keyed, so each frontend origin needs its own entry.
 */
export function consentState(origin: string) {
  return {
    cookies: [],
    origins: [
      {
        origin,
        localStorage: [
          {
            name: "kendrew.cookie_consent.v1",
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
