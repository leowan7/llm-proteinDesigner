/**
 * AdminAuditPage — reverse-chronological audit log at /admin/audit.
 *
 * Features:
 * - Table: Timestamp | Admin | Action | Target | Details
 * - Action enums rendered as human-readable labels (e.g. "Cancelled Job")
 * - Target ID truncated to 8 chars with full value in Tooltip
 * - Details: first key-value pair from metadata JSONB, truncated to 40 chars
 * - Keyset pagination: 50 per page on created_at DESC
 * - Loading skeleton while fetching
 * - Empty state when no audit events
 * - Error state on API failure
 *
 * No filters — simple reverse-chronological list for v1 per UI-SPEC.
 * Per D-26 through D-29 and UI-SPEC /admin/audit contract.
 */

import { useState, useEffect, useCallback } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchAdminAudit } from "@/lib/admin";
import type { AuditEntry } from "@/lib/admin";

const PAGE_SIZE = 50;

/**
 * Maps raw action enum values to human-readable display labels.
 * Unknown actions fall back to the raw string.
 */
const ACTION_LABELS: Record<string, string> = {
  view_users: "Viewed Users",
  view_jobs: "Viewed Jobs",
  view_job_detail: "Viewed Job Detail",
  cancel_job: "Cancelled Job",
  view_revenue: "Viewed Revenue",
  view_system: "Viewed System",
  view_audit: "Viewed Audit Log",
};

/**
 * Extract a one-line details summary from metadata JSONB.
 * Returns the first key-value pair as "key: value", truncated to 40 chars.
 */
function formatMetadata(metadata: Record<string, unknown>): string {
  const entries = Object.entries(metadata);
  if (entries.length === 0) return "—";
  const [key, val] = entries[0];
  const summary = `${key}: ${String(val)}`;
  return summary.length > 40 ? `${summary.slice(0, 40)}…` : summary;
}

export function AdminAuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Keyset pagination
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);

  const fetchEntries = useCallback(async (cursor: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAdminAudit({
        before: cursor ?? undefined,
        limit: PAGE_SIZE,
      });
      setEntries(result.entries);
      setHasMore(result.has_more);
    } catch {
      setError("Failed to load data. Refresh the page or check the backend logs.");
      setEntries([]);
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchEntries(currentCursor);
  }, [currentCursor, fetchEntries]);

  const handleNext = () => {
    if (!hasMore || entries.length === 0) return;
    const lastEntry = entries[entries.length - 1];
    const newCursor = lastEntry.created_at;
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
    <div>
      {/* Page heading */}
      <h1 className="text-xl font-semibold text-foreground mb-6">Audit Log</h1>

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
          {entries.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <h2 className="text-base font-semibold text-foreground mb-2">
                No audit events recorded
              </h2>
              <p className="text-sm text-muted-foreground">
                Admin actions will be logged here automatically.
              </p>
            </div>
          ) : (
            <div className="bg-card border border-border rounded-[0.625rem] overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Timestamp</TableHead>
                    <TableHead scope="col">Admin</TableHead>
                    <TableHead scope="col">Action</TableHead>
                    <TableHead scope="col">Target</TableHead>
                    <TableHead scope="col">Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((entry) => (
                    <TableRow key={entry.id} className="hover:bg-secondary min-h-[48px]">
                      <TableCell className="text-muted-foreground text-sm whitespace-nowrap">
                        {new Date(entry.created_at).toLocaleString()}
                      </TableCell>
                      <TableCell className="text-foreground text-sm">
                        {entry.admin_email}
                      </TableCell>
                      <TableCell className="text-foreground text-sm">
                        {ACTION_LABELS[entry.action] ?? entry.action}
                      </TableCell>
                      <TableCell>
                        {entry.target_id ? (
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <span className="font-mono text-sm cursor-help text-foreground">
                                  {entry.target_id.slice(0, 8)}
                                </span>
                              }
                            />
                            <TooltipContent>{entry.target_id}</TooltipContent>
                          </Tooltip>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatMetadata(entry.metadata)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Pagination */}
          {entries.length > 0 && (
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
    </div>
  );
}
