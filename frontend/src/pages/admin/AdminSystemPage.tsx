/**
 * AdminSystemPage — system health dashboard at /admin/system.
 *
 * Features:
 * - Status banner: "All systems operational" or degradation warning
 * - Summary cards: API Status, DB Status, Redis Status (with colored dot indicators)
 * - GPU Queue section: Running and Queued job counts in Display typography
 * - Manual "Refresh Status" button (no auto-polling per D-25)
 * - Loading skeleton while fetching
 * - Error state if health check fails
 *
 * Per D-22 through D-25 and UI-SPEC /admin/system contract.
 */

import { useState, useEffect, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchAdminSystem } from "@/lib/admin";
import type { AdminSystemHealth } from "@/lib/admin";

/**
 * Individual service status card with colored dot indicator.
 * Green dot (oklch(0.7 0.2 142)) = operational; red dot (--destructive) = degraded.
 * Includes sr-only text for screen reader accessibility per UI-SPEC.
 */
function StatusCard({ label, status }: { label: string; status: string }) {
  const isOk = status === "ok";
  return (
    <div className="bg-card border border-border rounded-[0.625rem] p-4 min-h-[80px]">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
        {label}
      </p>
      <div className="flex items-center gap-2 mt-2">
        <span
          className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
            isOk ? "bg-[oklch(0.7_0.2_142)]" : "bg-destructive"
          }`}
          aria-hidden="true"
        />
        <span className="text-sm font-medium text-foreground">
          {isOk ? "Operational" : "Degraded"}
        </span>
        <span className="sr-only">{isOk ? "Operational" : "Degraded"}</span>
      </div>
    </div>
  );
}

export function AdminSystemPage() {
  const [health, setHealth] = useState<AdminSystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAdminSystem();
      setHealth(result);
    } catch {
      setError("Health check failed. The API did not respond. Check Railway logs.");
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchHealth();
  }, [fetchHealth]);

  const handleRefresh = () => {
    void fetchHealth();
  };

  // Determine overall system status
  const allOperational =
    health?.api === "ok" && health?.db === "ok" && health?.redis === "ok";

  return (
    <div>
      {/* Page heading with refresh button */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-foreground">System</h1>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefresh}
          disabled={loading}
        >
          <RefreshCw
            className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`}
          />
          Refresh Status
        </Button>
      </div>

      {/* Overall status banner */}
      {!loading && !error && health && (
        <div
          className={`text-sm font-medium mb-6 px-4 py-3 rounded-[0.625rem] border ${
            allOperational
              ? "text-[oklch(0.7_0.2_142)] bg-card border-border"
              : "text-destructive bg-card border-border"
          }`}
        >
          {allOperational
            ? "All systems operational"
            : "One or more services are degraded. Check the status indicators below."}
        </div>
      )}

      {/* Summary cards: API, DB, Redis status */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {loading ? (
          <>
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-20 w-full rounded-lg" />
          </>
        ) : health ? (
          <>
            <StatusCard label="API Status" status={health.api} />
            <StatusCard label="DB Status" status={health.db} />
            <StatusCard label="Redis Status" status={health.redis} />
            {/* Running jobs count as a stat card */}
            <div className="bg-card border border-border rounded-[0.625rem] p-4 min-h-[80px]">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                Running Jobs
              </p>
              <p className="text-[28px] font-semibold leading-tight text-foreground mt-1">
                {health.running_jobs}
              </p>
            </div>
          </>
        ) : null}
      </div>

      {/* Error state */}
      {error && (
        <div className="text-sm text-muted-foreground py-8 text-center">{error}</div>
      )}

      {/* GPU Queue section */}
      {!loading && !error && health && (
        <div className="bg-card border border-border rounded-[0.625rem] p-6">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-4">
            GPU Queue
          </p>
          <div className="grid grid-cols-2 gap-8">
            <div>
              <p className="text-xs font-semibold text-muted-foreground">Running</p>
              <p className="text-[28px] font-semibold text-foreground">
                {health.running_jobs}
              </p>
            </div>
            <div>
              <p className="text-xs font-semibold text-muted-foreground">Queued</p>
              <p className="text-[28px] font-semibold text-foreground">
                {health.queued_jobs}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Loading skeleton — GPU queue */}
      {loading && !error && (
        <Skeleton className="h-32 w-full rounded-lg" />
      )}
    </div>
  );
}
