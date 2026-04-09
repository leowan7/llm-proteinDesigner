/**
 * Admin API client — typed functions for all /admin/* backend endpoints.
 *
 * All functions use the shared api() fetch wrapper which handles credentials,
 * CSRF, and 401 retry. Admin endpoints require is_admin=true on the server side.
 *
 * These functions are used by the admin dashboard pages only.
 */

import { api } from "./api";

// ─────────────────────────────────────────────
// TypeScript interfaces for admin data shapes
// ─────────────────────────────────────────────

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
  last_login: string | null;
  payment_status: "active" | "none";
  job_count: number;
  total_spend: number;
}

export interface AdminJob {
  id: string;
  email: string;
  tool: string;
  status: string;
  name: string;
  created_at: string;
  completed_at: string | null;
  gpu_seconds: number | null;
  gpu_cost_usd: number | null;
  error_category: string | null;
  job_spec: Record<string, unknown> | null;
  results: Record<string, unknown> | null;
  session_id: string | null;
  candidate_count: number | null;
}

export interface AdminRevenue {
  total_revenue: number;
  completed_jobs: number;
  running_jobs: number;
  failed_jobs: number;
  avg_revenue_per_job: number;
  cost_of_goods_usd: number | null;
  margin_usd: number | null;
  by_tool: Array<{ tool: string; revenue: number; job_count: number }>;
  period: string;
}

export interface AdminSystemHealth {
  api: string;
  db: string;
  redis: string;
  running_jobs: number;
  queued_jobs: number;
  storage: null;
}

export interface AuditEntry {
  id: string;
  admin_email: string;
  action: string;
  target_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AdminUsersResponse {
  users: AdminUser[];
  has_more: boolean;
}

export interface AdminJobsResponse {
  jobs: AdminJob[];
  has_more: boolean;
}

export interface AdminAuditResponse {
  entries: AuditEntry[];
  has_more: boolean;
}

// ─────────────────────────────────────────────
// API fetch functions
// ─────────────────────────────────────────────

/**
 * Fetch a page of admin users with optional email filter, sort, and keyset cursor.
 */
export async function fetchAdminUsers(params: {
  email?: string;
  sort?: string;
  before?: string;
  limit?: number;
}): Promise<AdminUsersResponse> {
  const qs = new URLSearchParams();
  if (params.email) qs.append("email", params.email);
  if (params.sort) qs.append("sort", params.sort);
  if (params.before) qs.append("before", params.before);
  if (params.limit != null) qs.append("limit", String(params.limit));
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return api<AdminUsersResponse>(`/admin/users${query}`);
}

/**
 * Fetch a page of admin jobs with optional status, tool, email filter, and keyset cursor.
 */
export async function fetchAdminJobs(params: {
  status?: string;
  tool?: string;
  email?: string;
  before?: string;
  limit?: number;
}): Promise<AdminJobsResponse> {
  const qs = new URLSearchParams();
  if (params.status) qs.append("status", params.status);
  if (params.tool) qs.append("tool", params.tool);
  if (params.email) qs.append("email", params.email);
  if (params.before) qs.append("before", params.before);
  if (params.limit != null) qs.append("limit", String(params.limit));
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return api<AdminJobsResponse>(`/admin/jobs${query}`);
}

/**
 * Fetch full detail for a single job by ID.
 */
export async function fetchAdminJobDetail(jobId: string): Promise<AdminJob> {
  return api<AdminJob>(`/admin/jobs/${jobId}`);
}

/**
 * Cancel a running or queued job by ID.
 * Returns updated status, gpu_seconds, and gpu_cost_usd.
 */
export async function cancelAdminJob(
  jobId: string,
): Promise<{ status: string; gpu_seconds: number | null; gpu_cost_usd: number | null }> {
  return api(`/admin/jobs/${jobId}/cancel`, { method: "POST" });
}

/**
 * Fetch revenue summary for a time period.
 * @param period - "this_month" | "last_30_days" | "all_time"
 */
export async function fetchAdminRevenue(period: string): Promise<AdminRevenue> {
  return api<AdminRevenue>(`/admin/revenue?period=${encodeURIComponent(period)}`);
}

/**
 * Fetch current system health snapshot (API, DB, Redis, GPU queue).
 */
export async function fetchAdminSystem(): Promise<AdminSystemHealth> {
  return api<AdminSystemHealth>("/admin/system");
}

/**
 * Fetch a page of audit log entries, newest first.
 */
export async function fetchAdminAudit(params: {
  before?: string;
  limit?: number;
}): Promise<AdminAuditResponse> {
  const qs = new URLSearchParams();
  if (params.before) qs.append("before", params.before);
  if (params.limit != null) qs.append("limit", String(params.limit));
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return api<AdminAuditResponse>(`/admin/audit${query}`);
}
