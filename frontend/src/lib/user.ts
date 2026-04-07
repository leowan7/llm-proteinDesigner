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
