/**
 * AdminRevenuePage — revenue overview at /admin/revenue.
 *
 * Features:
 * - Time period selector: This Month | Last 30 Days | All Time
 * - Summary cards: Total Revenue, Cost of Goods (N/A if not tracked), Margin (N/A if not tracked), Avg Revenue/Job
 * - Recharts BarChart of revenue by tool with per-tool color tokens
 * - By-tool data table: Tool | Jobs | Revenue | % of Total
 * - Loading skeleton while fetching
 * - Empty state when no revenue data
 * - Error state on API failure
 *
 * Per D-17 through D-21 and UI-SPEC /admin/revenue contract.
 */

import { useState, useEffect, useCallback } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { AdminStatCard } from "@/components/admin/AdminStatCard";
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
import { fetchAdminRevenue } from "@/lib/admin";
import type { AdminRevenue } from "@/lib/admin";

// Chart color tokens from globals.css (chart-1 through chart-5)
const CHART_COLORS = [
  "oklch(0.809 0.105 251.813)",
  "oklch(0.623 0.214 259.815)",
  "oklch(0.546 0.245 262.881)",
  "oklch(0.488 0.243 264.376)",
  "oklch(0.424 0.199 265.638)",
];

const PERIODS = [
  { value: "this_month", label: "This Month" },
  { value: "last_30_days", label: "Last 30 Days" },
  { value: "all_time", label: "All Time" },
];

export function AdminRevenuePage() {
  const [revenue, setRevenue] = useState<AdminRevenue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState("this_month");

  const fetchRevenue = useCallback(async (selectedPeriod: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAdminRevenue(selectedPeriod);
      setRevenue(result);
    } catch {
      setError("Failed to load data. Refresh the page or check the backend logs.");
      setRevenue(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchRevenue(period);
  }, [period, fetchRevenue]);

  const handlePeriodChange = (newPeriod: string) => {
    setPeriod(newPeriod);
  };

  return (
    <div>
      {/* Page heading */}
      <h1 className="text-xl font-semibold text-foreground mb-6">Revenue</h1>

      {/* Time period selector */}
      <div className="flex gap-2 mb-6">
        {PERIODS.map((p) => (
          <Button
            key={p.value}
            variant={period === p.value ? "default" : "secondary"}
            size="sm"
            onClick={() => handlePeriodChange(p.value)}
          >
            {p.label}
          </Button>
        ))}
      </div>

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
            <AdminStatCard
              label="Total Revenue"
              value={revenue ? `$${revenue.total_revenue.toFixed(2)}` : "—"}
              subLabel={period === "this_month" ? "this month" : period === "last_30_days" ? "last 30 days" : "all time"}
            />
            <AdminStatCard
              label="Cost of Goods"
              value={
                revenue?.cost_of_goods_usd != null
                  ? `$${revenue.cost_of_goods_usd.toFixed(2)}`
                  : "N/A"
              }
              subLabel="not tracked" // shown when null per D-18
            />
            <AdminStatCard
              label="Margin"
              value={
                revenue?.margin_usd != null
                  ? `$${revenue.margin_usd.toFixed(2)}`
                  : "N/A"
              }
            />
            <AdminStatCard
              label="Avg Revenue / Job"
              value={revenue ? `$${revenue.avg_revenue_per_job.toFixed(2)}` : "—"}
              subLabel={revenue ? `${revenue.completed_jobs} completed` : undefined}
            />
          </>
        )}
      </div>

      {/* Error state */}
      {error && (
        <div className="text-sm text-muted-foreground py-8 text-center">{error}</div>
      )}

      {/* Chart and table */}
      {!loading && !error && revenue && (
        <>
          {revenue.total_revenue === 0 ? (
            // Empty state
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <h2 className="text-base font-semibold text-foreground mb-2">No revenue yet</h2>
              <p className="text-sm text-muted-foreground">
                Revenue data will appear once jobs complete.
              </p>
            </div>
          ) : (
            <>
              {/* Bar chart */}
              <div
                className="bg-card border border-border rounded-[0.625rem] p-4 mb-6"
                role="img"
                aria-label="Revenue by tool, bar chart"
              >
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-4">
                  Revenue by Tool
                </p>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={revenue.by_tool}>
                    <XAxis
                      dataKey="tool"
                      stroke="oklch(0.62 0 0)"
                      tick={{ fontSize: 12 }}
                    />
                    <YAxis
                      stroke="oklch(0.62 0 0)"
                      tickFormatter={(v: number) => `$${v}`}
                      tick={{ fontSize: 12 }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "oklch(0.22 0.004 260)",
                        border: "1px solid oklch(1 0 0 / 10%)",
                        borderRadius: "0.625rem",
                      }}
                      formatter={(value: number) => [`$${value.toFixed(2)}`, "Revenue"]}
                    />
                    <Bar dataKey="revenue" radius={[4, 4, 0, 0]}>
                      {revenue.by_tool.map((_, index) => (
                        <Cell
                          key={index}
                          fill={CHART_COLORS[index % CHART_COLORS.length]}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* By-tool breakdown table */}
              <div className="bg-card border border-border rounded-[0.625rem] overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Tool</TableHead>
                      <TableHead scope="col">Jobs</TableHead>
                      <TableHead scope="col">Revenue</TableHead>
                      <TableHead scope="col">% of Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {revenue.by_tool.map((row) => (
                      <TableRow key={row.tool} className="hover:bg-secondary min-h-[48px]">
                        <TableCell className="font-medium text-foreground capitalize">
                          {row.tool}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {row.job_count}
                        </TableCell>
                        <TableCell>
                          <span className="font-mono text-sm">
                            ${row.revenue.toFixed(2)}
                          </span>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {revenue.total_revenue > 0
                            ? `${((row.revenue / revenue.total_revenue) * 100).toFixed(1)}%`
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </>
      )}

      {/* Loading skeleton — chart and table */}
      {loading && !error && (
        <div className="space-y-2">
          <Skeleton className="h-[240px] w-full rounded-lg" />
          <div className="space-y-2 mt-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
