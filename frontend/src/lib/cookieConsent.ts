/**
 * Cookie consent helpers (Plan 10-03).
 *
 * Kendrew only uses strictly-necessary cookies (access_token, refresh_token,
 * csrftoken). We do NOT use analytics, advertising, or tracking cookies, so
 * consent is a one-time disclosure rather than a granular opt-in. The record
 * below persists the user's acknowledgement so the banner is not re-shown on
 * every page load, and it is re-openable at any time via the
 * `kendrew:open-cookie-consent` custom event (dispatched from, e.g., the
 * footer "Cookie preferences" link).
 */
import { COOKIES_VERSION } from "@/pages/legal/versions";

export const COOKIE_CONSENT_KEY = "kendrew.cookie_consent.v1";
export const COOKIE_CONSENT_EVENT = "kendrew:open-cookie-consent";

export interface CookieConsentRecord {
  version: "v1";
  /** ISO 8601 timestamp of the moment the user dismissed the banner. */
  accepted_at: string;
  /** Value of COOKIES_VERSION at the time of acceptance. */
  cookies_version: string;
}

/**
 * Returns the persisted consent record, or `null` if no valid record exists.
 * Invalid JSON and unknown schema versions are treated as "no consent" so the
 * banner re-appears and users see the current disclosure.
 */
export function readConsent(): CookieConsentRecord | null {
  try {
    const raw = localStorage.getItem(COOKIE_CONSENT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CookieConsentRecord;
    if (parsed.version !== "v1") return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Persists a fresh consent record stamped with the current time and the active
 * COOKIES_VERSION. Returns the stored record for the caller's convenience.
 */
export function writeConsent(): CookieConsentRecord {
  const record: CookieConsentRecord = {
    version: "v1",
    accepted_at: new Date().toISOString(),
    cookies_version: COOKIES_VERSION,
  };
  localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify(record));
  return record;
}

/**
 * Dispatches the custom event that re-opens the banner. Any component (e.g.
 * the footer "Cookie preferences" link) can call this to let the user review
 * the disclosure again even after they have previously accepted.
 */
export function requestOpenConsent(): void {
  window.dispatchEvent(new CustomEvent(COOKIE_CONSENT_EVENT));
}
