/**
 * Job API client for job management, SSE status streaming, and billing.
 *
 * Handles:
 * - SSE-based real-time job status updates (fetch + ReadableStream, not EventSource)
 * - Job fetch, list, and cancellation
 * - Download URL generation
 * - Billing estimate and payment status
 *
 * Note: Uses fetch + ReadableStream (not EventSource) for SSE to match the
 * established pattern from agent.ts. EventSource only supports GET requests
 * without custom headers; this approach works with cookie auth.
 */

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

/**
 * Reads the csrftoken_v2 cookie value from document.cookie.
 * Returns null if no CSRF token cookie is present.
 *
 * Name bumped to csrftoken_v2 — see frontend/src/lib/api.ts for context.
 */
function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken_v2=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** SSE event emitted by GET /jobs/{id}/status */
export interface JobStatusEvent {
  job_id: string;
  status: "queued" | "running" | "complete" | "failed" | "cancelled";
  stage: string;
  gpu_seconds?: number;
  error_category?: string;
}

/** Individual design candidate from job results */
export interface CandidateData {
  rank: number;
  pdb_key: string;
  scores: Record<string, number>;
  download_url: string;
}

/** Full job response from GET /jobs/{id} */
export interface JobData {
  id: string;
  status: string;
  stage: string | null;
  tool: string;
  gpu_seconds: number | null;
  gpu_cost_usd: number | null;
  started_at: string | null;
  completed_at: string | null;
  error_category: string | null;
  results: {
    candidate_count: number;
    next_steps: string;
    zero_output: boolean;
  } | null;
  candidates: CandidateData[];
  job_spec: Record<string, unknown> | null;
  created_at: string | null;
}

/** Summary item from GET /jobs list (paginated)
 *
 * Plan 12-05: jobs are now org-scoped, so the list response includes
 * `created_by_user_id` and `created_by_email` populated by Plan 12-03's
 * backend cutover. Existing single-tenant jobs were backfilled to the
 * job-owner's user id + email by migration 20260604000002.
 */
export interface JobListItem {
  id: string;
  tool: string | null;
  status: string;
  name: string | null;
  created_at: string;
  completed_at: string | null;
  gpu_cost_usd: number | null;
  candidate_count: number | null;
  session_id: string | null;
  /** UUID of the org member who launched the job. */
  created_by_user_id?: string | null;
  /** Email of the org member who launched the job. */
  created_by_email?: string | null;
}

/** Cost estimate from GET /billing/estimate */
export interface CostEstimate {
  low: number;
  high: number;
  currency: string;
}

// ---------------------------------------------------------------------------
// SSE subscription
// ---------------------------------------------------------------------------

/**
 * Subscribe to real-time job status updates via SSE.
 *
 * Opens a fetch-based ReadableStream to the /jobs/{id}/status endpoint.
 * Calls onEvent for each parsed SSE event. Calls onError for non-abort errors.
 *
 * @param jobId   - Job to subscribe to.
 * @param onEvent - Callback invoked for each JobStatusEvent received.
 * @param onError - Optional callback for connection errors (not called on abort).
 * @returns An unsubscribe function that aborts the stream.
 */
export function subscribeToJobStatus(
  jobId: string,
  onEvent: (event: JobStatusEvent) => void,
  onError?: (error: Error) => void,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const response = await fetch(`${API_BASE}/jobs/${jobId}/status`, {
        credentials: "include",
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`SSE connection failed: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        // SSE events separated by newlines; keep incomplete last chunk in buffer
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event = JSON.parse(line.slice(6)) as JobStatusEvent;
              onEvent(event);
            } catch {
              // Ignore malformed SSE data lines (e.g. keep-alive comments)
            }
          }
        }
      }
    } catch (err) {
      // AbortError is expected when unsubscribe() is called — do not propagate
      if (err instanceof Error && err.name !== "AbortError") {
        onError?.(err);
      }
    }
  })();

  // Return unsubscribe function
  return () => controller.abort();
}

// ---------------------------------------------------------------------------
// Job API calls
// ---------------------------------------------------------------------------

/**
 * Fetch full job data including candidates and results.
 *
 * @param jobId - Job ID to fetch.
 * @returns Full JobData including candidates, results, and billing info.
 */
export async function getJob(jobId: string): Promise<JobData> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error(`Failed to fetch job: ${response.status}`);
  return response.json() as Promise<JobData>;
}

/**
 * Fetch the list of jobs for the current user (legacy single-page fetch).
 *
 * @returns Array of JobListItem summaries, most recent first.
 */
export async function getJobList(): Promise<JobListItem[]> {
  const response = await fetch(`${API_BASE}/jobs/`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error(`Failed to fetch job list: ${response.status}`);
  // Backend returns { jobs: [...], has_more: bool } -- not a bare array.
  // The previous `as Promise<JobListItem[]>` cast lied to the type system;
  // the runtime value crashed downstream `.filter()` calls (e.g.
  // JobPage.tsx:152). Discovered live 2026-06-03 when a failure email link
  // landed on /jobs/<id> and the page rendered blank with a TypeError.
  const data = (await response.json()) as { jobs: JobListItem[]; has_more?: boolean };
  return Array.isArray(data) ? data : (data.jobs ?? []);
}

/**
 * Fetch a paginated list of jobs for the current user.
 *
 * Uses keyset pagination: pass the created_at timestamp of the last job
 * in the current page as the `before` cursor to fetch the next page.
 *
 * @param params.limit  - Max jobs to return per page (default 25).
 * @param params.status - Filter by status: "running", "complete", or "failed". Omit for all.
 * @param params.before - ISO timestamp cursor for keyset pagination (exclusive).
 * @returns Object with jobs array and has_more flag.
 */
export async function listJobs(
  params: {
    limit?: number;
    status?: string | null;
    before?: string | null;
  } = {},
): Promise<{ jobs: JobListItem[]; has_more: boolean }> {
  const searchParams = new URLSearchParams();
  if (params.limit) searchParams.set("limit", String(params.limit));
  if (params.status) searchParams.set("status", params.status);
  if (params.before) searchParams.set("before", params.before);
  const query = searchParams.toString();
  const url = query ? `/jobs?${query}` : "/jobs";
  const response = await fetch(`${API_BASE}${url}`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error(`Failed to fetch jobs: ${response.status}`);
  return response.json() as Promise<{ jobs: JobListItem[]; has_more: boolean }>;
}

/**
 * Cancel a running job.
 *
 * Sends a CSRF-protected POST to stop GPU compute immediately.
 * Returns billing info for compute consumed up to cancellation.
 *
 * @param jobId - Job to cancel.
 * @returns Object with final status, gpu_seconds consumed, and gpu_cost_usd charged.
 */
export async function cancelJob(
  jobId: string,
): Promise<{ status: string; gpu_seconds: number; gpu_cost_usd: number }> {
  const csrf = getCsrfToken();
  const response = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
    method: "POST",
    credentials: "include",
    headers: csrf ? { "X-CSRFToken": csrf } : {},
  });
  if (!response.ok) throw new Error(`Failed to cancel job: ${response.status}`);
  return response.json() as Promise<{ status: string; gpu_seconds: number; gpu_cost_usd: number }>;
}

/**
 * Returns the download URL for a zip of all job design PDB files.
 *
 * Used with window.open() to trigger a browser download. The URL points to
 * the /jobs/{id}/download endpoint which streams an application/zip response.
 *
 * @param jobId - Job whose designs to download.
 * @returns Absolute URL string for the download endpoint.
 */
export function downloadAllDesignsUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/download`;
}

// ---------------------------------------------------------------------------
// Billing API calls
// ---------------------------------------------------------------------------

/**
 * Fetch a cost estimate range for a given tool and design count.
 *
 * This endpoint is unauthenticated — safe to call before login.
 *
 * @param tool       - Tool name (e.g. "rfdiffusion", "bindcraft", "boltzgen").
 * @param numDesigns - Number of designs requested (default 1).
 * @returns Low and high estimate bounds with currency code.
 */
export async function getCostEstimate(
  tool: string,
  numDesigns: number = 1,
): Promise<CostEstimate> {
  const response = await fetch(
    `${API_BASE}/billing/estimate?tool=${encodeURIComponent(tool)}&num_designs=${numDesigns}`,
  );
  if (!response.ok) throw new Error(`Failed to fetch estimate: ${response.status}`);
  return response.json() as Promise<CostEstimate>;
}

/**
 * Check whether the current user has a payment method on file.
 *
 * Used by the ReviewCard launch gate to determine whether to redirect to
 * Stripe Checkout before dispatching a job.
 *
 * @returns Object with has_payment_method boolean.
 */
export async function getPaymentStatus(): Promise<{ has_payment_method: boolean }> {
  const response = await fetch(`${API_BASE}/billing/payment-status`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error(`Failed to check payment status: ${response.status}`);
  return response.json() as Promise<{ has_payment_method: boolean }>;
}

/**
 * Dispatch a pre-created job after the payment gate check.
 *
 * The backend verifies the user has a payment method (returning 402 if not),
 * then calls launch_job() to set status=queued and enqueue the arq task.
 *
 * @param jobId - UUID of the existing job row created by the agent wizard.
 * @returns Object with job_id and status="queued" on success.
 * @throws Error with message "payment_required" when the user has no payment method.
 */
export async function launchJob(jobId: string, jobName?: string): Promise<{ job_id: string; status: string }> {
  const csrf = getCsrfToken();
  const response = await fetch(`${API_BASE}/jobs/launch`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRFToken": csrf } : {}),
    },
    body: JSON.stringify({ job_id: jobId, ...(jobName ? { job_name: jobName } : {}) }),
  });
  if (response.status === 402) {
    throw new Error("payment_required");
  }
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Launch failed" }));
    throw new Error((err as { detail: string }).detail ?? "Launch failed");
  }
  return response.json() as Promise<{ job_id: string; status: string }>;
}

/**
 * Create a Stripe Checkout session for adding a payment method.
 *
 * @param returnUrl - URL to redirect to after the Checkout session completes.
 * @returns Object with the Stripe Checkout session URL to redirect the user to.
 */
export async function createCheckoutSession(returnUrl: string): Promise<{ url: string }> {
  const csrf = getCsrfToken();
  const response = await fetch(`${API_BASE}/billing/checkout-session`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRFToken": csrf } : {}),
    },
    body: JSON.stringify({ return_url: returnUrl }),
  });
  if (!response.ok) throw new Error(`Failed to create checkout session: ${response.status}`);
  return response.json() as Promise<{ url: string }>;
}
