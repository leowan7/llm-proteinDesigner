/* eslint-disable react-hooks/set-state-in-effect -- page-level data fetch on mount is intentional */
/**
 * JobPage — the main job status and results page at /jobs/:id.
 *
 * Layout (top to bottom):
 * 1. ExpiryWarningBanner (conditional — within 7 days of 30-day expiry)
 * 2. JobStatusCard (always — real-time while running, static when terminal)
 * 3. RunSummaryCard (complete or cancelled)
 * 4. "Design candidates" section heading + BindCraftZeroOutputCard or CandidateCard list (complete)
 * 5. JobFailureCard (failed)
 * 6. NextStepsCard (complete with next_steps)
 * 7. "Previous jobs" section heading + job history list (all states)
 *
 * SSE subscription starts on mount when job is running/queued and cleans
 * up on unmount via the unsubscribe function.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { JobStatusCard } from "@/components/jobs/JobStatusCard";
import { RunSummaryCard } from "@/components/jobs/RunSummaryCard";
import { CandidateCard } from "@/components/jobs/CandidateCard";
import { NextStepsCard } from "@/components/jobs/NextStepsCard";
import { JobFailureCard } from "@/components/jobs/JobFailureCard";
import { BindCraftZeroOutputCard } from "@/components/jobs/BindCraftZeroOutputCard";
import { ExpiryWarningBanner } from "@/components/jobs/ExpiryWarningBanner";
import {
  subscribeToJobStatus,
  getJob,
  getJobList,
  cancelJob,
  type JobData,
  type JobListItem,
  type JobStatusEvent,
} from "@/lib/jobs";

/** Terminal job statuses — SSE subscription is not needed for these. */
const TERMINAL_STATUSES = new Set(["complete", "failed", "cancelled"]);

export function JobPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [job, setJob] = useState<JobData | null>(null);
  const [jobList, setJobList] = useState<JobListItem[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Fetch the full job data
  const fetchJob = useCallback(async () => {
    if (!id) return;
    try {
      const data = await getJob(id);
      setJob(data);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load job");
    }
  }, [id]);

  // Fetch job history list (excludes current job in display)
  const fetchJobList = useCallback(async () => {
    try {
      const list = await getJobList();
      setJobList(list);
    } catch {
      // Non-critical — silently fail; job history is secondary content
    }
  }, []);

  // Handle incoming SSE events — update status/stage optimistically,
  // then re-fetch full job data when terminal status is reached
  const handleStatusEvent = useCallback(
    async (event: JobStatusEvent) => {
      setJob((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          status: event.status,
          stage: event.stage ?? prev.stage,
        };
      });

      // On terminal status: re-fetch full data to get candidates, results, billing
      if (TERMINAL_STATUSES.has(event.status)) {
        await fetchJob();
      }
    },
    [fetchJob],
  );

  // Cancel the job and re-fetch to show updated state
  const handleCancel = useCallback(async () => {
    if (!id) return;
    await cancelJob(id);
    await fetchJob();
  }, [id, fetchJob]);

  useEffect(() => {
    fetchJob();
    fetchJobList();
  }, [fetchJob, fetchJobList]);

  useEffect(() => {
    if (!id || !job) return;

    // Only subscribe when job is in a non-terminal state
    if (TERMINAL_STATUSES.has(job.status)) return;

    const unsubscribe = subscribeToJobStatus(id, handleStatusEvent, (err) => {
      // SSE drop is not user-visible — log for debugging only
      console.warn("Job SSE connection error:", err.message);
    });

    return () => {
      unsubscribe();
    };
  }, [id, job?.status, handleStatusEvent]);

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  if (loadError) {
    return (
      <div className="max-w-3xl mx-auto pt-8 pb-12 px-4">
        <p className="text-base text-destructive">{loadError}</p>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="max-w-3xl mx-auto pt-8 pb-12 px-4">
        <p className="text-sm text-muted-foreground">Loading job...</p>
      </div>
    );
  }

  const isComplete = job.status === "complete";
  const isFailed = job.status === "failed";
  const showSummary = isComplete || job.status === "cancelled";

  const isBindCraftZero =
    isComplete &&
    job.tool.toLowerCase() === "bindcraft" &&
    job.results?.zero_output === true;

  const showNextSteps =
    isComplete && !!job.results?.next_steps && !isBindCraftZero;

  const previousJobs = jobList.filter((item) => item.id !== job.id);

  return (
    <div className="max-w-3xl mx-auto pt-8 pb-12 px-4 space-y-6">
      {/* 1. Expiry warning banner — within 7 days of 30-day expiry */}
      {job.completed_at && (
        <ExpiryWarningBanner completedAt={job.completed_at} />
      )}

      {/* 2. Job status card — always shown */}
      <JobStatusCard
        jobId={job.id}
        status={job.status as "queued" | "running" | "complete" | "failed" | "cancelled"}
        stage={job.stage}
        tool={job.tool}
        onCancel={handleCancel}
      />

      {/* 3. Run summary — complete or cancelled */}
      {showSummary && (
        <RunSummaryCard
          jobId={job.id}
          tool={job.tool}
          completedAt={job.completed_at}
          gpuSeconds={job.gpu_seconds}
          gpuCostUsd={job.gpu_cost_usd}
          candidateCount={job.results?.candidate_count ?? job.candidates.length}
          jobSpec={job.job_spec}
        />
      )}

      {/* 4. Design candidates section */}
      {isComplete && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-xl font-semibold text-foreground">Design candidates</h2>
            {job.candidates.length > 0 && (
              <Button
                variant="outline"
                onClick={() => {
                  const prompt = `Generate a full analysis report for job ${id} with shortlisted candidates, metric explanations, and next steps.`;
                  navigate(`/chat?prompt=${encodeURIComponent(prompt)}`);
                }}
              >
                Export Report
              </Button>
            )}
          </div>

          {isBindCraftZero ? (
            <BindCraftZeroOutputCard agentGuidance={job.results?.next_steps} />
          ) : (
            <div className="space-y-4">
              {job.candidates.map((candidate) => (
                <CandidateCard
                  key={candidate.rank}
                  rank={candidate.rank}
                  scores={candidate.scores}
                  tool={job.tool}
                  downloadUrl={candidate.download_url}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* 5. Job failure card */}
      {isFailed && (
        <JobFailureCard errorCategory={job.error_category} />
      )}

      {/* 6. Next steps card — complete with next_steps content */}
      {showNextSteps && job.results?.next_steps && (
        <NextStepsCard nextSteps={job.results.next_steps} />
      )}

      {/* 7. Previous jobs section */}
      <div className="space-y-4">
        <h2 className="font-display text-xl font-semibold text-foreground">Previous jobs</h2>

        {previousJobs.length === 0 ? (
          <div>
            <p className="text-base text-muted-foreground">No previous jobs</p>
            <p className="text-sm text-muted-foreground">Your completed jobs will appear here.</p>
          </div>
        ) : (
          <ul className="space-y-2">
            {previousJobs.map((item) => (
              <li key={item.id}>
                <Link
                  to={`/jobs/${item.id}`}
                  className="flex items-center gap-3 py-2 px-3 rounded-md hover:bg-muted/50 transition-colors text-sm"
                >
                  <JobHistoryBadge status={item.status} />
                  <span className="text-foreground flex-1">{item.tool}</span>
                  <span className="text-muted-foreground">
                    {formatHistoryDate(item.completed_at ?? item.created_at)}
                  </span>
                  {item.gpu_cost_usd !== null && (
                    <span className="font-mono text-sm text-muted-foreground">
                      ${item.gpu_cost_usd.toFixed(2)}
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Compact status badge for job history list items. */
function JobHistoryBadge({ status }: { status: string }) {
  if (status === "complete") {
    return <Badge className="bg-emerald-500/15 text-emerald-400 text-xs">Complete</Badge>;
  }
  if (status === "failed") {
    return <Badge variant="destructive" className="text-xs">Failed</Badge>;
  }
  if (status === "running") {
    return <Badge variant="default" className="text-xs">Running</Badge>;
  }
  return (
    <Badge variant="secondary" className="text-xs">
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  );
}

/**
 * Formats an ISO date string as a short date for job history rows.
 * Returns "—" if null.
 */
function formatHistoryDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
