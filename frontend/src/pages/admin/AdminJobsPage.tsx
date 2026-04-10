/**
 * AdminJobsPage — all-user jobs management table at /admin/jobs.
 *
 * Features:
 * - Summary cards: Running Jobs, Queued Jobs, Failed (last 24h), Total Jobs
 * - Filter bar: Status dropdown, Tool dropdown, Email search (300ms debounce)
 * - Paginated table (50/page) with keyset pagination on created_at DESC
 * - Row expansion: click row to fetch full job detail, show params JSON, error, candidate count, session link
 * - Cancel button (destructive) on running/queued rows — opens confirmation Dialog
 * - Loading skeleton and empty state
 * - Error state on API failure
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { AdminStatCard } from "@/components/admin/AdminStatCard";
import { StatusBadge } from "@/components/common/StatusBadge";
import {
  fetchAdminJobs,
  fetchAdminJobDetail,
  cancelAdminJob,
} from "@/lib/admin";
import type { AdminJob } from "@/lib/admin";

const PAGE_SIZE = 50;

/**
 * Format a date string as a relative time (e.g. "2 hours ago").
 * Falls back to locale date string if the date is more than 30 days ago.
 */
function relativeDate(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(isoString).toLocaleDateString();
}

/**
 * Format gpu_seconds as "Xm Ys" — e.g. "2m 35s".
 */
function formatDuration(gpuSeconds: number): string {
  const minutes = Math.floor(gpuSeconds / 60);
  const seconds = gpuSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

/**
 * Whether a job's created_at falls within the last 24 hours.
 */
function isLast24h(createdAt: string): boolean {
  return Date.now() - new Date(createdAt).getTime() < 24 * 60 * 60 * 1000;
}

export function AdminJobsPage() {
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [statusFilter, setStatusFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [emailFilter, setEmailFilter] = useState("");
  const [debouncedEmail, setDebouncedEmail] = useState("");

  // Keyset pagination
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);

  // Row expansion state
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [expandedJobDetail, setExpandedJobDetail] = useState<AdminJob | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Cancel dialog state
  const [cancelDialogJobId, setCancelDialogJobId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  // Debounce email input: 300ms delay before triggering fetch
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleEmailChange = (value: string) => {
    setEmailFilter(value);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setDebouncedEmail(value);
      setCursorStack([]);
      setCurrentCursor(null);
    }, 300);
  };

  const resetPagination = () => {
    setCursorStack([]);
    setCurrentCursor(null);
  };

  const handleStatusChange = (value: string) => {
    setStatusFilter(value);
    resetPagination();
  };

  const handleToolChange = (value: string) => {
    setToolFilter(value);
    resetPagination();
  };

  const fetchJobs = useCallback(
    async (
      cursor: string | null,
      status: string,
      tool: string,
      email: string,
    ) => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchAdminJobs({
          status: status || undefined,
          tool: tool || undefined,
          email: email || undefined,
          before: cursor ?? undefined,
          limit: PAGE_SIZE,
        });
        setJobs(result.jobs);
        setHasMore(result.has_more);
      } catch {
        setError("Failed to load data. Refresh the page or check the backend logs.");
        setJobs([]);
        setHasMore(false);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void fetchJobs(currentCursor, statusFilter, toolFilter, debouncedEmail);
  }, [currentCursor, statusFilter, toolFilter, debouncedEmail, fetchJobs]);

  const handleNext = () => {
    if (!hasMore || jobs.length === 0) return;
    const lastJob = jobs[jobs.length - 1];
    setCursorStack((prev) => [...prev, currentCursor ?? ""]);
    setCurrentCursor(lastJob.created_at);
  };

  const handlePrevious = () => {
    if (cursorStack.length === 0) return;
    const newStack = [...cursorStack];
    const prevCursor = newStack.pop() ?? null;
    setCursorStack(newStack);
    setCurrentCursor(prevCursor === "" ? null : prevCursor);
  };

  const isFirstPage = cursorStack.length === 0;

  /**
   * Toggle job row expansion.
   * On first expand, fetch full detail from GET /admin/jobs/{id}.
   * Clicking an already-expanded row collapses it.
   */
  const handleRowClick = async (jobId: string) => {
    if (expandedJobId === jobId) {
      setExpandedJobId(null);
      setExpandedJobDetail(null);
      return;
    }
    setExpandedJobId(jobId);
    setExpandedJobDetail(null);
    setDetailLoading(true);
    try {
      const detail = await fetchAdminJobDetail(jobId);
      setExpandedJobDetail(detail);
    } catch {
      // If detail fetch fails, show the row data we already have
      const fallback = jobs.find((j) => j.id === jobId) ?? null;
      setExpandedJobDetail(fallback);
    } finally {
      setDetailLoading(false);
    }
  };

  /**
   * Execute the cancel after the confirmation dialog is confirmed.
   */
  const handleCancelConfirm = async () => {
    if (!cancelDialogJobId) return;
    setCancelling(true);
    try {
      await cancelAdminJob(cancelDialogJobId);
      setCancelDialogJobId(null);
      // Refetch the current page to reflect the updated status
      void fetchJobs(currentCursor, statusFilter, toolFilter, debouncedEmail);
    } catch {
      // Surface failure silently — operator can retry
    } finally {
      setCancelling(false);
    }
  };

  // Derive summary stats from the current page
  const runningCount = jobs.filter((j) => j.status === "running").length;
  const queuedCount = jobs.filter((j) => j.status === "queued").length;
  const failed24h = jobs.filter(
    (j) => j.status === "failed" && isLast24h(j.created_at),
  ).length;
  const totalOnPage = jobs.length;

  return (
    <div>
      {/* Page heading */}
      <h1 className="text-xl font-semibold text-foreground mb-6">Jobs</h1>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {loading ? (
          <>
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
          </>
        ) : (
          <>
            <AdminStatCard label="Running Jobs" value={String(runningCount)} subLabel="this page" />
            <AdminStatCard label="Queued Jobs" value={String(queuedCount)} subLabel="this page" />
            <AdminStatCard label="Failed (24h)" value={String(failed24h)} subLabel="this page" />
            <AdminStatCard label="Total Jobs" value={String(totalOnPage)} subLabel="this page" />
          </>
        )}
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <select
          value={statusFilter}
          onChange={(e) => handleStatusChange(e.target.value)}
          className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">All Statuses</option>
          <option value="running">Running</option>
          <option value="queued">Queued</option>
          <option value="complete">Complete</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>

        <select
          value={toolFilter}
          onChange={(e) => handleToolChange(e.target.value)}
          className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">All Tools</option>
          <option value="rfdiffusion">RFdiffusion</option>
          <option value="bindcraft">BindCraft</option>
          <option value="rfantibody">RFantibody</option>
          <option value="boltzgen">BoltzGen</option>
          <option value="pxdesign">PXDesign</option>
        </select>

        <input
          type="text"
          placeholder="Filter by email..."
          value={emailFilter}
          onChange={(e) => handleEmailChange(e.target.value)}
          className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring min-w-[200px]"
        />
      </div>

      {/* Error state */}
      {error && (
        <div className="text-sm text-muted-foreground py-8 text-center">{error}</div>
      )}

      {/* Loading skeleton — table rows */}
      {loading && !error && (
        <div className="space-y-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      )}

      {/* Table */}
      {!loading && !error && (
        <>
          {jobs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <h2 className="text-base font-semibold text-foreground mb-2">
                No jobs found
              </h2>
              <p className="text-sm text-muted-foreground">
                Adjust your filters or check back once users start running jobs.
              </p>
            </div>
          ) : (
            <div className="bg-card border border-border rounded-[0.625rem] overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Job ID</TableHead>
                    <TableHead scope="col">User</TableHead>
                    <TableHead scope="col">Tool</TableHead>
                    <TableHead scope="col">Status</TableHead>
                    <TableHead scope="col">Name</TableHead>
                    <TableHead scope="col">Started</TableHead>
                    <TableHead scope="col">Duration</TableHead>
                    <TableHead scope="col">GPU Cost</TableHead>
                    <TableHead scope="col">Error</TableHead>
                    <TableHead scope="col">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {jobs.map((job) => (
                    <>
                      {/* Main job row */}
                      <TableRow
                        key={job.id}
                        className={`cursor-pointer hover:bg-secondary min-h-[48px] ${
                          expandedJobId === job.id ? "bg-secondary" : ""
                        }`}
                        onClick={() => void handleRowClick(job.id)}
                      >
                        <TableCell className="font-mono text-sm text-muted-foreground">
                          {job.id.slice(0, 8)}
                        </TableCell>
                        <TableCell className="text-sm text-foreground">
                          {job.email}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground capitalize">
                          {job.tool}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={job.status} />
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {job.name || "—"}
                        </TableCell>
                        <TableCell
                          className="text-sm text-muted-foreground"
                          title={job.created_at}
                        >
                          {relativeDate(job.created_at)}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground font-mono">
                          {job.gpu_seconds != null
                            ? formatDuration(job.gpu_seconds)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-sm font-mono">
                          {job.gpu_cost_usd != null
                            ? `$${job.gpu_cost_usd.toFixed(2)}`
                            : "—"}
                        </TableCell>
                        <TableCell>
                          {job.error_category ? (
                            <span className="text-destructive text-xs">
                              {job.error_category}
                            </span>
                          ) : (
                            <span className="text-muted-foreground text-sm">—</span>
                          )}
                        </TableCell>
                        <TableCell
                          onClick={(e) => e.stopPropagation()}
                        >
                          {(job.status === "running" || job.status === "queued") && (
                            <Button
                              variant="destructive"
                              size="sm"
                              onClick={() => setCancelDialogJobId(job.id)}
                            >
                              Cancel
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>

                      {/* Expanded detail row */}
                      {expandedJobId === job.id && (
                        <TableRow key={`${job.id}-detail`}>
                          <TableCell
                            colSpan={10}
                            className="bg-secondary/50 p-4"
                          >
                            {detailLoading ? (
                              <div className="space-y-2">
                                <Skeleton className="h-4 w-1/3" />
                                <Skeleton className="h-20 w-full" />
                              </div>
                            ) : expandedJobDetail ? (
                              <div className="space-y-3">
                                {/* Parameters JSON */}
                                <div>
                                  <p className="text-xs font-semibold text-muted-foreground mb-1 uppercase tracking-wide">
                                    Parameters
                                  </p>
                                  <pre className="text-xs bg-secondary p-3 rounded-md overflow-auto max-h-48 whitespace-pre-wrap text-foreground">
                                    {JSON.stringify(expandedJobDetail.job_spec, null, 2) || "—"}
                                  </pre>
                                </div>

                                {/* Error message */}
                                {expandedJobDetail.results?.error_message && (
                                  <div>
                                    <p className="text-xs font-semibold text-muted-foreground mb-1 uppercase tracking-wide">
                                      Error
                                    </p>
                                    <p className="text-sm text-destructive">
                                      {String(expandedJobDetail.results.error_message)}
                                    </p>
                                  </div>
                                )}

                                {/* Metadata row */}
                                <div className="flex gap-6 text-sm text-muted-foreground">
                                  <span>
                                    <span className="font-semibold text-foreground">Candidates:</span>{" "}
                                    {expandedJobDetail.candidate_count ?? "—"}
                                  </span>
                                  {expandedJobDetail.session_id && (
                                    <span>
                                      <Link
                                        to={`/chat/${expandedJobDetail.session_id}`}
                                        className="text-primary hover:underline"
                                        onClick={(e) => e.stopPropagation()}
                                      >
                                        View session
                                      </Link>
                                    </span>
                                  )}
                                </div>
                              </div>
                            ) : (
                              <p className="text-sm text-muted-foreground">
                                No detail available.
                              </p>
                            )}
                          </TableCell>
                        </TableRow>
                      )}
                    </>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Pagination */}
          {jobs.length > 0 && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
              <Button
                variant="outline"
                size="sm"
                onClick={handlePrevious}
                disabled={isFirstPage}
                aria-disabled={isFirstPage}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleNext}
                disabled={!hasMore}
                aria-disabled={!hasMore}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}

      {/* Cancel confirmation dialog */}
      <Dialog
        open={cancelDialogJobId !== null}
        onOpenChange={(open) => {
          if (!open && !cancelling) setCancelDialogJobId(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel this job?</DialogTitle>
            <DialogDescription>
              This will stop the GPU run immediately. Partial compute time will be billed.
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => setCancelDialogJobId(null)}
              disabled={cancelling}
            >
              Keep running
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleCancelConfirm()}
              disabled={cancelling}
            >
              {cancelling ? "Cancelling..." : "Yes, cancel job"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
