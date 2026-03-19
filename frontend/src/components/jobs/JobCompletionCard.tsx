/**
 * JobCompletionCard — inline chat card posted on job completion.
 *
 * Posted by the agent into the chat thread when a job finishes. Shows a
 * brief summary and a link to the full results page. Follows the existing
 * chat card pattern (border-border/50).
 */

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useNavigate } from "react-router-dom";

interface JobCompletionCardProps {
  jobId: string;
  candidateCount: number;
  gpuSeconds: number | null;
  gpuCostUsd: number | null;
}

/**
 * Formats GPU seconds into minutes for the summary row.
 */
function formatMinutes(gpuSeconds: number | null): string {
  if (gpuSeconds === null) return "—";
  const minutes = Math.round(gpuSeconds / 60);
  return minutes < 1 ? "<1 min" : `${minutes} min`;
}

/**
 * Formats GPU cost as "$X.XX" or "—" if null.
 */
function formatCost(gpuCostUsd: number | null): string {
  if (gpuCostUsd === null) return "—";
  return `$${gpuCostUsd.toFixed(2)}`;
}

export function JobCompletionCard({
  jobId,
  candidateCount,
  gpuSeconds,
  gpuCostUsd,
}: JobCompletionCardProps) {
  const navigate = useNavigate();

  return (
    <Card className="my-2 border-border/50">
      <CardContent className="px-4 py-4 space-y-3">
        {/* Summary row */}
        <p className="text-base text-foreground">
          {candidateCount} {candidateCount === 1 ? "design" : "designs"} generated in{" "}
          {formatMinutes(gpuSeconds)} — {formatCost(gpuCostUsd)}
        </p>

        {/* Link to full results page */}
        <Button
          variant="default"
          className="bg-primary text-primary-foreground hover:bg-primary/90"
          onClick={() => navigate(`/jobs/${jobId}`)}
        >
          View full results
        </Button>
      </CardContent>
    </Card>
  );
}
