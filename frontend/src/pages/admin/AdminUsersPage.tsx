/**
 * AdminUsersPage — user management table at /admin/users.
 *
 * Features:
 * - Summary cards row: Total Users, Active This Month, With Payment Method, Total Platform Revenue
 * - Email search with 300ms debounce
 * - Sort dropdown: Joined: Newest / Joined: Oldest / Jobs: Most
 * - Paginated table (50/page) with keyset pagination on created_at
 * - Loading skeleton while fetching
 * - Empty state when no users match
 * - Error state on API failure
 *
 * View-only per D-11 — no edit/delete. Admin manages users in Supabase Studio.
 */

import { useState, useEffect, useCallback, useRef } from "react";
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
import { AdminStatCard } from "@/components/admin/AdminStatCard";
import { fetchAdminUsers } from "@/lib/admin";
import type { AdminUser } from "@/lib/admin";

const PAGE_SIZE = 50;

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

/**
 * Check whether a last_login date falls within the current calendar month.
 */
function isActiveThisMonth(lastLogin: string | null): boolean {
  if (!lastLogin) return false;
  const now = new Date();
  const login = new Date(lastLogin);
  return login.getFullYear() === now.getFullYear() && login.getMonth() === now.getMonth();
}

export function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter and sort state
  const [emailFilter, setEmailFilter] = useState("");
  const [debouncedEmail, setDebouncedEmail] = useState("");
  const [sort, setSort] = useState("created_at_desc");

  // Keyset pagination
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);

  // Debounce email input: 300ms delay before triggering fetch
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleEmailChange = (value: string) => {
    setEmailFilter(value);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setDebouncedEmail(value);
      // Reset pagination when filter changes
      setCursorStack([]);
      setCurrentCursor(null);
    }, 300);
  };

  const handleSortChange = (value: string) => {
    setSort(value);
    setCursorStack([]);
    setCurrentCursor(null);
  };

  const fetchUsers = useCallback(
    async (cursor: string | null, email: string, sortParam: string) => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchAdminUsers({
          email: email || undefined,
          sort: sortParam,
          before: cursor ?? undefined,
          limit: PAGE_SIZE,
        });
        setUsers(result.users);
        setHasMore(result.has_more);
      } catch {
        setError("Failed to load data. Refresh the page or check the backend logs.");
        setUsers([]);
        setHasMore(false);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void fetchUsers(currentCursor, debouncedEmail, sort);
  }, [currentCursor, debouncedEmail, sort, fetchUsers]);

  const handleNext = () => {
    if (!hasMore || users.length === 0) return;
    const lastUser = users[users.length - 1];
    const newCursor = lastUser.created_at;
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

  // Derive summary stats from the current page
  // Note: These are page-level approximations; accurate totals require a dedicated summary endpoint.
  const totalUsersOnPage = users.length;
  const activeThisMonth = users.filter((u) => isActiveThisMonth(u.last_login)).length;
  const withPayment = users.filter((u) => u.payment_status === "active").length;
  const totalRevenue = users.reduce((sum, u) => sum + u.total_spend, 0);

  return (
    <div>
      {/* Page heading */}
      <h1 className="text-xl font-semibold text-foreground mb-6">Users</h1>

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
            <AdminStatCard label="Total Users" value={String(totalUsersOnPage)} subLabel="this page" />
            <AdminStatCard label="Active This Month" value={String(activeThisMonth)} subLabel="by last login" />
            <AdminStatCard
              label="With Payment Method"
              value={String(withPayment)}
              subLabel="active billing"
            />
            <AdminStatCard
              label="Total Platform Revenue"
              value={`$${totalRevenue.toFixed(2)}`}
              subLabel="this page"
            />
          </>
        )}
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <input
          type="text"
          placeholder="Filter by email..."
          value={emailFilter}
          onChange={(e) => handleEmailChange(e.target.value)}
          className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring min-w-[200px]"
        />
        <select
          value={sort}
          onChange={(e) => handleSortChange(e.target.value)}
          className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="created_at_desc">Joined: Newest</option>
          <option value="created_at_asc">Joined: Oldest</option>
          <option value="job_count_desc">Jobs: Most</option>
        </select>
      </div>

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
          {users.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <h2 className="text-base font-semibold text-foreground mb-2">No users yet</h2>
              <p className="text-sm text-muted-foreground">
                Users will appear here once someone signs up.
              </p>
            </div>
          ) : (
            <div className="bg-card border border-border rounded-[0.625rem] overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Email</TableHead>
                    <TableHead scope="col">Display Name</TableHead>
                    <TableHead scope="col">Joined</TableHead>
                    <TableHead scope="col">Last Login</TableHead>
                    <TableHead scope="col">Payment</TableHead>
                    <TableHead scope="col">Jobs</TableHead>
                    <TableHead scope="col">Spend</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => (
                    <TableRow key={user.id} className="hover:bg-secondary min-h-[48px]">
                      <TableCell className="font-medium text-foreground">
                        {user.email}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {user.display_name || "—"}
                      </TableCell>
                      <TableCell
                        className="text-muted-foreground"
                        title={user.created_at}
                      >
                        {relativeDate(user.created_at)}
                      </TableCell>
                      <TableCell
                        className="text-muted-foreground"
                        title={user.last_login ?? undefined}
                      >
                        {user.last_login ? relativeDate(user.last_login) : "Never"}
                      </TableCell>
                      <TableCell>
                        {user.payment_status === "active" ? (
                          <span className="text-green-400 text-sm font-medium">Active</span>
                        ) : (
                          <span className="text-muted-foreground text-sm">None</span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {user.job_count}
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-sm">
                          ${user.total_spend.toFixed(2)}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Pagination */}
          {users.length > 0 && (
            <div
              className="flex items-center justify-between mt-4 pt-4 border-t border-border"
            >
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
