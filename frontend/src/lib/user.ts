/**
 * User settings, billing, and usage API client.
 *
 * All requests go through the shared api() helper which handles base URL,
 * auth cookies, CSRF tokens, and 401 refresh retry.
 */

import { api } from "./api";

export interface UserSettings {
  email: string;
  display_name: string;
  notification_preferences: {
    job_complete: boolean;
    job_failure: boolean;
  };
  is_admin?: boolean;
  /** Version string the user most recently accepted (Plan 10-02). */
  tos_version?: string | null;
  /** Current backend TOS version (Plan 10-02). */
  tos_current?: string;
  /** Per-user retention window in days, 30-365 (Plan 10-02). */
  data_retention_days?: number;
  /** ISO-8601 timestamp of a pending GDPR Art. 17 deletion request, or null
   *  when no deletion is scheduled (Plan 10-04). Drives the Privacy tab's
   *  pending-deletion banner + Cancel Deletion button. */
  deletion_requested_at?: string | null;
}

/** Status of the most recent GDPR Art. 20 data export (Plan 10-04). */
export interface ExportStatus {
  status: "none" | "pending" | "ready" | "expired";
  url?: string;
  expires_at?: string;
}

export interface UsageData {
  period_start: string;
  job_count: number;
  total_spend_usd: number;
  recent_charges: Array<{
    id: string;
    name: string | null;
    tool: string | null;
    completed_at: string;
    gpu_cost_usd: number;
  }>;
}

export interface PaymentMethod {
  has_payment_method: boolean;
  brand?: string;
  last4?: string;
  exp_month?: number;
  exp_year?: number;
}

/**
 * Fetches the current user's settings.
 * Returns email, display_name, and notification_preferences.
 */
export async function getSettings(): Promise<UserSettings> {
  return api<UserSettings>("/user/settings", { method: "GET" });
}

/**
 * Updates user settings (display_name and/or notification_preferences).
 * Partial update — omit fields that should not change.
 */
export async function updateSettings(data: {
  display_name?: string;
  notification_preferences?: { job_complete: boolean; job_failure: boolean };
}): Promise<void> {
  await api("/user/settings", {
    method: "PUT",
    body: data,
  });
}

/**
 * Fetches usage data for the current billing period.
 * Returns job count, total spend, and recent charge list.
 */
export async function getUsage(): Promise<UsageData> {
  return api<UsageData>("/user/usage", { method: "GET" });
}

/**
 * Fetches the current Stripe payment method on file.
 * Returns has_payment_method flag; card details if a card exists.
 */
export async function getPaymentMethod(): Promise<PaymentMethod> {
  return api<PaymentMethod>("/billing/payment-method", { method: "GET" });
}

/**
 * Creates a Stripe Customer Portal session and returns the redirect URL.
 * @param returnUrl - URL to return to after the portal session ends.
 */
export async function createPortalSession(returnUrl: string): Promise<string> {
  const data = await api<{ url: string }>("/billing/portal", {
    method: "POST",
    body: { return_url: returnUrl },
  });
  return data.url;
}

// ---------------------------------------------------------------------------
// Plan 10-04 — GDPR Art. 17 (erasure) + Art. 20 (data portability)
// ---------------------------------------------------------------------------

/**
 * Schedules a GDPR Article 20 data export. The backend responds 202 immediately
 * and emails a presigned ZIP link when the background build finishes.
 *
 * Rate-limited to 1/hour per user (backend). Repeated calls within the window
 * return an HTTP 429 that surfaces as an ApiError here.
 */
export async function requestDataExport(): Promise<{
  status: string;
  message: string;
}> {
  return api("/user/data-export", { method: "POST" });
}

/**
 * Returns the status of the most recent data export request.
 * "none" means the user has never requested one.
 */
export async function getExportStatus(): Promise<ExportStatus> {
  return api<ExportStatus>("/user/data-export", { method: "GET" });
}

/**
 * Submits a GDPR Article 17 account deletion request. Requires the literal
 * phrase "DELETE MY ACCOUNT" as a defense-in-depth CSRF-like gate on top of
 * the global double-submit middleware.
 *
 * On success, returns the ISO-8601 timestamp when the hard-delete will run
 * (30 days after the request). The user can cancel at any point during the
 * grace period via cancelAccountDeletion().
 */
export async function requestAccountDeletion(
  confirmation: string,
): Promise<{ deletion_scheduled_for: string }> {
  return api("/user/delete-account", {
    method: "POST",
    body: { confirmation_phrase: confirmation },
  });
}

/**
 * Cancels a pending account deletion during the 30-day grace period.
 * Clears `deletion_requested_at` on the user row.
 */
export async function cancelAccountDeletion(): Promise<{ cancelled: boolean }> {
  return api("/user/cancel-deletion", { method: "POST" });
}
