import * as Sentry from "@sentry/react";

/**
 * Initialize Sentry error tracking for the frontend.
 * Only activates when VITE_SENTRY_DSN_FRONTEND is set (disabled in local dev by default).
 */
export function initSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN_FRONTEND;
  if (!dsn) return;

  Sentry.init({
    dsn,
    environment: import.meta.env.MODE, // "development" or "production"
    // No performance monitoring for v1 — error tracking only
    tracesSampleRate: 0,
    // Only send errors in production-like environments
    enabled: import.meta.env.PROD || !!dsn,
    beforeSend(event) {
      // Strip PII from breadcrumbs if needed
      return event;
    },
  });
}

/**
 * Set user context on Sentry so errors are associated with a user.
 * Call after login.
 */
export function setSentryUser(userId: string, email?: string): void {
  Sentry.setUser({ id: userId, email });
}

/**
 * Clear user context on Sentry. Call on logout.
 */
export function clearSentryUser(): void {
  Sentry.setUser(null);
}
