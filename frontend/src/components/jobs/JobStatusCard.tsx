/**
 * JobStatusCard — real-time job status card with stage progress indicator.
 *
 * Displays the current stage in a left-to-right progress row, a status badge,
 * and a cancel button when the job is running. Cancel triggers an inline
 * confirmation section (no modal overlay).
 *
 * Used on /jobs/{id} and inside the chat thread after job launch.
 */

import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface JobStatusCardProps {
  jobId: string;
  status: "queued" | "running" | "complete" | "failed" | "cancelled";
  stage: string | null;
  tool: string;
  onCancel: () => Promise<void>;
}

/** Maps a tool name to its tool-specific running stage label. */
function getRunningStageLabel(tool: string): string {
  const normalized = tool.toLowerCase();
  if (normalized === "bindcraft") return "Running binding optimization";
  if (normalized === "boltzgen") return "Running structure generation";
  // rfdiffusion and rfantibody both use diffusion
  return "Running diffusion";
}

/** Ordered stage names for the progress row. */
function getStages(tool: string): string[] {
  return [
    "Queued",
    "Initializing GPU",
    getRunningStageLabel(tool),
    "Scoring designs",
    "Complete",
  ];
}

/** Maps job status to the shadcn Badge variant or className override. */
function StatusBadge({ status }: { status: string }) {
  if (status === "complete") {
    return (
      <Badge className="bg-emerald-500/15 text-emerald-400">Complete</Badge>
    );
  }
  if (status === "failed") {
    return <Badge variant="destructive">Failed</Badge>;
  }
  if (status === "running") {
    return <Badge variant="default">Running</Badge>;
  }
  // queued and cancelled use secondary
  return (
    <Badge variant="secondary">
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  );
}

/**
 * Determines the visual state of a stage step relative to the current stage.
 *
 * - "active": this is the current stage
 * - "complete": this stage has been passed
 * - "future": this stage has not been reached yet
 */
function getStageState(
  stageName: string,
  currentStage: string | null,
  stages: string[],
): "active" | "complete" | "future" {
  if (!currentStage) return "future";

  const currentIndex = stages.findIndex(
    (s) => s.toLowerCase() === currentStage.toLowerCase(),
  );
  const thisIndex = stages.indexOf(stageName);

  if (thisIndex === currentIndex) return "active";
  if (currentIndex !== -1 && thisIndex < currentIndex) return "complete";
  return "future";
}

export function JobStatusCard({
  jobId,
  status,
  stage,
  tool,
  onCancel,
}: JobStatusCardProps) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const stages = getStages(tool);

  async function handleConfirmCancel() {
    setCancelling(true);
    try {
      await onCancel();
    } finally {
      setCancelling(false);
      setShowConfirm(false);
    }
  }

  return (
    <Card className="border-border/50">
      <CardHeader className="px-4 pb-2 pt-4 flex flex-row items-start justify-between gap-4">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-h-[44px]">
          {stages.map((stageName, index) => {
            const state = getStageState(stageName, stage, stages);
            return (
              <span key={stageName} className="flex items-center gap-x-2">
                <span
                  className={
                    state === "active"
                      ? "text-primary font-semibold text-sm"
                      : state === "complete"
                        ? "text-muted-foreground line-through text-sm"
                        : "text-muted-foreground text-sm"
                  }
                >
                  {stageName}
                </span>
                {index < stages.length - 1 && (
                  <span className="text-muted-foreground text-sm select-none">→</span>
                )}
              </span>
            );
          })}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <StatusBadge status={status} />
          {status === "running" && !showConfirm && (
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setShowConfirm(true)}
            >
              Cancel job
            </Button>
          )}
        </div>
      </CardHeader>

      {/* Inline cancel confirmation — shown instead of a modal */}
      {showConfirm && (
        <CardContent className="px-4 pb-4 pt-0">
          <div className="border border-destructive/40 rounded-md p-4 space-y-3">
            <p className="text-base font-semibold text-foreground">
              Cancel this job?
            </p>
            <p className="text-sm text-muted-foreground">
              GPU compute will stop immediately. You will be charged for time
              consumed up to this point.
            </p>
            <div className="flex gap-3">
              <Button
                variant="destructive"
                size="sm"
                onClick={handleConfirmCancel}
                disabled={cancelling}
              >
                {cancelling ? "Cancelling..." : "Yes, cancel job"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowConfirm(false)}
                disabled={cancelling}
              >
                Keep running
              </Button>
            </div>
          </div>
        </CardContent>
      )}

      {/* Suppress unused jobId lint warning — used by parent to re-fetch on cancel */}
      <span className="hidden">{jobId}</span>
    </Card>
  );
}
