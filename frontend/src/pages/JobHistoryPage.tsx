/**
 * JobHistoryPage — paginated job history at /jobs.
 *
 * Layout:
 * - Page heading "Jobs"
 * - Status filter dropdown (All / Running / Complete / Failed)
 * - Semantic table with 7 columns: Job, Tool, Status, Date, Designs, Cost, Actions
 * - Keyset pagination: Previous/Next buttons, 25 jobs per page
 * - Empty state with CTA to /chat when no jobs exist
 * - Loading skeleton while fetching
 * - Mobile card layout below 768px
 *
 * Pagination:
 * - Uses a cursor stack: push last job's created_at for "Next", pop for "Previous"
 * - "Previous" disabled when at page 1 (cursorStack is empty)
 * - "Next" disabled when has_more is false
 */

import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/common/StatusBadge";
import { listJobs, downloadAllDesignsUrl } from "@/lib/jobs";
import type { JobListItem } from "@/lib/jobs";

const PAGE_SIZE = 25;

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

export function JobHistoryPage() {
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Status filter: null means "All"
  const [statusFilter, setStatusFilter] = useState<string | null>(null);

  // Keyset pagination: stack of cursors (created_at of last item on each page)
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  // The current active cursor (undefined = first page)
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);

  const fetchJobs = useCallback(
    async (cursor: string | null, status: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const result = await listJobs({
          limit: PAGE_SIZE,
          status: status ?? undefined,
          before: cursor ?? undefined,
        });
        setJobs(result.jobs);
        setHasMore(result.has_more);
      } catch {
        setError("Unable to load jobs. Refresh the page or try again in a moment.");
        setJobs([]);
        setHasMore(false);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // Fetch on mount and whenever filter or cursor changes
  useEffect(() => {
    void fetchJobs(currentCursor, statusFilter);
  }, [currentCursor, statusFilter, fetchJobs]);

  const handleStatusChange = (value: string) => {
    const newStatus = value === "all" ? null : value;
    setStatusFilter(newStatus);
    // Reset pagination when filter changes
    setCursorStack([]);
    setCurrentCursor(null);
  };

  const handleNext = () => {
    if (!hasMore || jobs.length === 0) return;
    const lastJob = jobs[jobs.length - 1];
    const newCursor = lastJob.created_at;
    setCursorStack((prev) => [...prev, currentCursor ?? ""]);
    setCurrentCursor(newCursor);
  };

  const handlePrevious = () => {
    if (cursorStack.length === 0) return;
    const newStack = [...cursorStack];
    const prevCursor = newStack.pop() ?? null;
    setCursorStack(newStack);
    setCurrentCursor(prevCursor === "" ? null : prevCursor);
  };

  const isFirstPage = cursorStack.length === 0;

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Page heading */}
      <h1 className="text-xl font-semibold text-foreground mb-6">Jobs</h1>

      {/* Status filter */}
      <div className="flex items-center gap-3 mb-4">
        <label htmlFor="status-filter" className="text-sm text-muted-foreground">
          Status
        </label>
        <select
          id="status-filter"
          value={statusFilter ?? "all"}
          onChange={(e) => handleStatusChange(e.target.value)}
          className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="all">All</option>
          <option value="running">Running</option>
          <option value="complete">Complete</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Error state */}
      {error && (
        <div className="text-sm text-muted-foreground py-8 text-center">
          {error}
        </div>
      )}

      {/* Loading skeleton — 5 rows */}
      {loading && !error && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="animate-pulse bg-muted rounded h-[48px] w-full"
            />
          ))}
        </div>
      )}

      {/* Content — shown when loaded */}
      {!loading && !error && (
        <>
          {/* Empty state */}
          {jobs.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <h2 className="text-base font-semibold text-foreground mb-2">No jobs yet</h2>
              <p className="text-sm text-muted-foreground mb-6">
                Start a design conversation to launch your first job.
              </p>
              <Button variant="default" size="sm" render={<Link to="/chat">Open chat</Link>} />
            </div>
          )}

          {/* Desktop table — hidden on mobile */}
          {jobs.length > 0 && (
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Job</TableHead>
                    <TableHead scope="col">Tool</TableHead>
                    <TableHead scope="col">Status</TableHead>
                    <TableHead scope="col">Date</TableHead>
                    <TableHead scope="col">Designs</TableHead>
                    <TableHead scope="col">Cost</TableHead>
                    <TableHead scope="col">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {jobs.map((job) => (
                    <TableRow key={job.id} className="hover:bg-secondary">
                      <TableCell className="font-medium">
                        <Link
                          to={`/jobs/${job.id}`}
                          className="text-foreground hover:underline"
                        >
                          {job.name ?? job.id.slice(0, 8)}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground capitalize">
                        {job.tool ?? "—"}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={job.status} />
                      </TableCell>
                      <TableCell
                        className="text-muted-foreground"
                        title={job.created_at}
                      >
                        {relativeDate(job.created_at)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {job.candidate_count ?? "—"}
                      </TableCell>
                      <TableCell>
                        {job.gpu_cost_usd != null ? (
                          <span className="font-mono text-sm">
                            ${job.gpu_cost_usd.toFixed(2)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Button variant="outline" size="sm" render={<Link to={`/jobs/${job.id}`}>View</Link>} />
                          {job.status === "complete" && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => window.open(downloadAllDesignsUrl(job.id), "_blank")}
                            >
                              Download
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Mobile card list — shown below 768px */}
          {jobs.length > 0 && (
            <div className="md:hidden space-y-3">
              {jobs.map((job) => (
                <Card key={job.id} className="p-4">
                  <div className="flex justify-between items-start mb-2">
                    <span className="font-semibold text-sm truncate">
                      {job.name ?? job.id.slice(0, 8)}
                    </span>
                    <StatusBadge status={job.status} />
                  </div>
                  <div className="text-xs text-muted-foreground space-y-1">
                    <div>
                      {job.tool ?? "Unknown tool"} · {relativeDate(job.created_at)}
                    </div>
                    {job.gpu_cost_usd != null && (
                      <div className="font-mono">${job.gpu_cost_usd.toFixed(2)}</div>
                    )}
                  </div>
                  <div className="flex gap-2 mt-3">
                    <Button variant="outline" size="sm" render={<Link to={`/jobs/${job.id}`}>View job</Link>} />
                    {job.status === "complete" && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => window.open(downloadAllDesignsUrl(job.id), "_blank")}
                      >
                        Download results
                      </Button>
                    )}
                  </div>
                </Card>
              ))}
            </div>
          )}

          {/* Pagination controls */}
          {jobs.length > 0 && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
              <Button
                variant="outline"
                size="sm"
                onClick={handlePrevious}
                disabled={isFirstPage}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleNext}
                disabled={!hasMore}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
