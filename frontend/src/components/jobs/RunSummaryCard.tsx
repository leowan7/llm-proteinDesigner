/**
 * RunSummaryCard — post-run metadata card shown on /jobs/{id} when complete or cancelled.
 *
 * Displays tool name, run date, key run metrics (runtime, GPU cost, design count),
 * collapsible job parameters, and a "Download all designs" button.
 */

import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { downloadAllDesignsUrl } from "@/lib/jobs";

interface RunSummaryCardProps {
  jobId: string;
  tool: string;
  completedAt: string | null;
  gpuSeconds: number | null;
  gpuCostUsd: number | null;
  candidateCount: number;
  jobSpec: Record<string, unknown> | null;
}

/**
 * Formats GPU seconds into a human-readable runtime string.
 * Returns "—" if gpuSeconds is null.
 */
function formatRuntime(gpuSeconds: number | null): string {
  if (gpuSeconds === null) return "—";
  const minutes = Math.round(gpuSeconds / 60);
  if (minutes < 1) return `${gpuSeconds}s`;
  return `${minutes} min`;
}

/**
 * Formats GPU cost for display.
 * Format: "$X.XX (N min on A100)"
 */
function formatGpuCost(gpuCostUsd: number | null, gpuSeconds: number | null): string {
  if (gpuCostUsd === null) return "—";
  const costStr = `$${gpuCostUsd.toFixed(2)}`;
  if (gpuSeconds !== null) {
    const minutes = Math.round(gpuSeconds / 60);
    return `${costStr} (${minutes} min on A100)`;
  }
  return costStr;
}

/**
 * Formats an ISO date string as a human-readable date.
 * Returns "—" if null.
 */
function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function RunSummaryCard({
  jobId,
  tool,
  completedAt,
  gpuSeconds,
  gpuCostUsd,
  candidateCount,
  jobSpec,
}: RunSummaryCardProps) {
  const [downloading, setDownloading] = useState(false);

  function handleDownload() {
    setDownloading(true);
    window.open(downloadAllDesignsUrl(jobId));
    // Reset loading state after a short delay — download is initiated asynchronously
    setTimeout(() => setDownloading(false), 2000);
  }

  const hasParams = jobSpec && Object.keys(jobSpec).length > 0;

  return (
    <Card className="border-border/50">
      <CardHeader className="px-4 pb-2 pt-4">
        <div className="flex items-baseline gap-3">
          <span className="font-display text-xl font-semibold text-foreground">{tool}</span>
          <span className="text-sm text-muted-foreground">{formatDate(completedAt)}</span>
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-4">
        {/* Key-value metrics grid */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <span className="text-muted-foreground">Runtime</span>
          <span className="text-foreground">{formatRuntime(gpuSeconds)}</span>

          <span className="text-muted-foreground">GPU cost</span>
          <span className="font-mono text-sm text-foreground">
            {formatGpuCost(gpuCostUsd, gpuSeconds)}
          </span>

          <span className="text-muted-foreground">Designs generated</span>
          <span className="text-foreground">{candidateCount}</span>
        </div>

        {/* Collapsible parameters */}
        {hasParams && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground transition-colors select-none">
              Parameters
            </summary>
            <div className="mt-2 space-y-1 ml-2">
              {Object.entries(jobSpec).map(([key, value]) => (
                <div key={key} className="flex justify-between items-baseline gap-4">
                  <span className="text-muted-foreground shrink-0">{key}</span>
                  <span className="font-mono text-sm text-foreground text-right">
                    {String(value)}
                  </span>
                </div>
              ))}
            </div>
          </details>
        )}

        <Separator />

        {/* Download all designs button */}
        <Button
          variant="default"
          onClick={handleDownload}
          disabled={downloading}
          className="w-full sm:w-auto"
        >
          {downloading ? "Preparing download..." : "Download all designs"}
        </Button>
      </CardContent>
    </Card>
  );
}
