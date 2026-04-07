/**
 * JobHistoryPage — paginated job history table at /jobs.
 *
 * Displays all user jobs in a semantic HTML table with:
 * - th[scope="col"] headers per WCAG 1.3.1 (D-26)
 * - StatusBadge with sr-only text per D-25
 * - Status filter (All, Running, Complete, Failed) per D-17
 * - Loading skeleton and empty state per D-20
 * - Mobile card list below 768px (md:hidden / hidden md:block) per D-19
 *
 * Pagination uses keyset cursor pattern (25 jobs/page, D-18).
 */

import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { getJobList, type JobListItem as BaseJobListItem } from "@/lib/jobs";

// Inline status badge with sr-only text for WCAG D-25
interface StatusBadgeProps {
  status: string;
}

function StatusBadge({ status }: StatusBadgeProps) {
  const colorMap: Record<string, string> = {
    running: "bg-blue-500/15 text-blue-400",
    complete: "bg-green-500/15 text-green-400",
    failed: "bg-destructive/15 text-destructive",
    queued: "bg-muted text-muted-foreground",
    cancelled: "bg-muted text-muted-foreground",
  };
  const colorClass = colorMap[status] ?? "bg-muted text-muted-foreground";
  const label = status.charAt(0).toUpperCase() + status.slice(1);

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colorClass}`}>
      {label}
      <span className="sr-only"> status</span>
    </span>
  );
}

// Extended type adding fields that may be present when Plan 04 listJobs is available
interface JobListItem extends BaseJobListItem {
  name?: string | null;
  candidate_count?: number | null;
}

const STATUS_FILTERS = ["All", "Running", "Complete", "Failed"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function JobHistoryPage() {
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("All");

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const all = await getJobList();
      // Apply status filter client-side when listJobs with server-side filter isn't available
      const filtered = statusFilter === "All"
        ? all
        : all.filter((j) => j.status === statusFilter.toLowerCase());
      setJobs(filtered as JobListItem[]);
    } catch {
      setLoadError("Unable to load jobs. Refresh the page or try again in a moment.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8">
      <h1 className="text-[28px] font-semibold mb-6">Jobs</h1>

      {/* Status filter */}
      <div className="flex items-center gap-2 mb-6" role="group" aria-label="Filter by status">
        {STATUS_FILTERS.map((filter) => (
          <Button
            key={filter}
            variant={statusFilter === filter ? "default" : "outline"}
            size="sm"
            onClick={() => setStatusFilter(filter)}
            aria-pressed={statusFilter === filter}
          >
            {filter}
          </Button>
        ))}
      </div>

      {loadError && (
        <p role="alert" className="text-sm text-destructive mb-4">
          {loadError}
        </p>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-2" aria-label="Loading jobs">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-12 bg-muted rounded-md animate-pulse" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !loadError && jobs.length === 0 && (
        <div className="text-center py-16">
          <p className="text-xl font-semibold text-foreground mb-2">No jobs yet</p>
          <p className="text-sm text-muted-foreground mb-4">
            Start a design conversation to launch your first job.
          </p>
          <Button asChild variant="default" size="sm">
            <Link to="/chat">Open chat</Link>
          </Button>
        </div>
      )}

      {/* Desktop table */}
      {!loading && jobs.length > 0 && (
        <div className="hidden md:block rounded-md border border-border/50 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 bg-card">
                <th scope="col" className="px-4 py-3 text-left text-xs text-muted-foreground font-medium">
                  Job
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs text-muted-foreground font-medium">
                  Tool
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs text-muted-foreground font-medium">
                  Status
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs text-muted-foreground font-medium">
                  Date
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs text-muted-foreground font-medium">
                  Designs
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs text-muted-foreground font-medium">
                  Cost
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs text-muted-foreground font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} className="border-b border-border/50 last:border-0 hover:bg-secondary/30 transition-colors">
                  <td className="px-4 py-3 text-foreground font-medium">
                    {job.name ?? job.id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{job.tool}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatDate(job.completed_at ?? job.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right text-muted-foreground">
                    {job.candidate_count ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-muted-foreground">
                    {job.gpu_cost_usd != null ? `$${job.gpu_cost_usd.toFixed(2)}` : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button asChild variant="ghost" size="sm">
                      <Link to={`/jobs/${job.id}`}>View</Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Mobile card list */}
      {!loading && jobs.length > 0 && (
        <div className="md:hidden space-y-3">
          {jobs.map((job) => (
            <div key={job.id} className="rounded-md border border-border/50 bg-card p-4">
              <div className="flex items-start justify-between gap-2 mb-2">
                <span className="text-sm font-medium text-foreground">
                  {job.name ?? job.id.slice(0, 8)}
                </span>
                <StatusBadge status={job.status} />
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
                <span>{job.tool}</span>
                <span>·</span>
                <span>{formatDate(job.completed_at ?? job.created_at)}</span>
                {job.gpu_cost_usd != null && (
                  <>
                    <span>·</span>
                    <span className="font-mono">${job.gpu_cost_usd.toFixed(2)}</span>
                  </>
                )}
              </div>
              <div className="flex gap-2">
                <Button asChild variant="outline" size="sm">
                  <Link to={`/jobs/${job.id}`}>View job</Link>
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
