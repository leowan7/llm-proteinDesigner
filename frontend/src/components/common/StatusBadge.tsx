/**
 * StatusBadge — reusable status badge for job status display.
 *
 * Uses color-coded background/foreground pairs per the UI-SPEC color contract.
 * Includes sr-only " status" suffix for WCAG compliance (not color-only meaning).
 */

import { Badge } from "@/components/ui/badge";

const statusStyles: Record<string, string> = {
  running: "bg-blue-500/15 text-blue-400",
  complete: "bg-green-500/15 text-green-400",
  failed: "bg-destructive/15 text-destructive",
  queued: "bg-muted text-muted-foreground",
  pending: "bg-muted text-muted-foreground",
};

interface StatusBadgeProps {
  /** Job status string (running, complete, failed, queued, pending, etc.) */
  status: string;
}

/**
 * Render a color-coded badge for a job status.
 *
 * Includes a sr-only " status" label so screen readers announce
 * e.g. "Running status" rather than just a color cue.
 */
export function StatusBadge({ status }: StatusBadgeProps) {
  const style = statusStyles[status] ?? statusStyles.pending;
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return (
    <Badge className={style}>
      {label}
      <span className="sr-only"> status</span>
    </Badge>
  );
}
