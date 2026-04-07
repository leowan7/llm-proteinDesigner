/**
 * AppSidebar — collapsible sidebar for authenticated pages.
 *
 * Structure (top to bottom):
 * 1. SidebarHeader — Kendrew wordmark
 * 2. "Start new session" button
 * 3. SidebarContent — scrollable session list grouped by date
 * 4. SidebarSeparator
 * 5. Navigation links (Jobs, Settings)
 * 6. SidebarFooter — user avatar, display name, logout
 *
 * State ownership: sessions list is managed by AuthenticatedLayout and
 * passed down as props. This component is presentation-only.
 */

import { useCallback } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { Briefcase, Settings, LogOut, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useState } from "react";
import { createPersistentSession, deleteSessionApi } from "@/lib/sessions";
import { api } from "@/lib/api";
import { clearSentryUser } from "@/lib/sentry";
import type { SessionSummary } from "@/lib/sessions";

interface AppSidebarProps {
  /** Full list of user sessions (managed by AuthenticatedLayout) */
  sessions: SessionSummary[];
  /** True while sessions are loading for the first time */
  sessionsLoading: boolean;
  /** UUID of the currently active session (from URL params) */
  activeSessionId?: string;
  /** Callback to trigger a sidebar session list refresh */
  onRefresh: () => Promise<void>;
}

/** Group sessions by recency relative to today */
function groupSessions(sessions: SessionSummary[]) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekStart = new Date(todayStart);
  weekStart.setDate(weekStart.getDate() - 6); // last 7 days excluding today

  const today: SessionSummary[] = [];
  const thisWeek: SessionSummary[] = [];
  const earlier: SessionSummary[] = [];

  for (const s of sessions) {
    const updatedAt = new Date(s.updated_at);
    if (updatedAt >= todayStart) {
      today.push(s);
    } else if (updatedAt >= weekStart) {
      thisWeek.push(s);
    } else {
      earlier.push(s);
    }
  }

  return { today, thisWeek, earlier };
}

/** Single session list item with title, active state, and context menu */
function SessionItem({
  session,
  isActive,
  onDelete,
  onRenameStart,
}: {
  session: SessionSummary;
  isActive: boolean;
  onDelete: (id: string) => void;
  onRenameStart: (session: SessionSummary) => void;
}) {
  const navigate = useNavigate();
  const title = session.title ?? "Untitled session";

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        asChild
        isActive={isActive}
        aria-current={isActive ? "page" : undefined}
        className="min-h-[44px] group/item"
      >
        <Link to={`/chat/${session.id}`} className="flex items-center gap-2 w-full">
          <span className="truncate flex-1 text-sm">{title}</span>
          {/* Context menu trigger — visible on hover */}
          <span
            className="opacity-0 group-hover/item:opacity-100 focus-within:opacity-100 shrink-0"
            onClick={(e) => e.preventDefault()}
          >
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  aria-label={`Options for ${title}`}
                  className="h-6 w-6 inline-flex items-center justify-center rounded-sm hover:bg-sidebar-accent text-muted-foreground hover:text-foreground"
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-36">
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    onRenameStart(session);
                  }}
                  className="cursor-pointer"
                >
                  <Pencil className="mr-2 h-3.5 w-3.5" />
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(session.id);
                  }}
                  variant="destructive"
                  className="cursor-pointer"
                >
                  <Trash2 className="mr-2 h-3.5 w-3.5" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </span>
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

/** Skeleton loading rows for session list */
function SessionSkeletons() {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="animate-pulse bg-muted rounded-md h-[40px] mx-2 mb-1"
          aria-hidden="true"
        />
      ))}
    </>
  );
}

export function AppSidebar({ sessions, sessionsLoading, activeSessionId, onRefresh }: AppSidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();

  // Delete confirmation dialog state
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Rename state (simple prompt for now — full inline edit deferred)
  const [renameTarget, setRenameTarget] = useState<SessionSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // User info for footer
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [logoutLoading, setLogoutLoading] = useState(false);

  // Fetch user email once for footer display
  const fetchUser = useCallback(async () => {
    try {
      const data = await api<{ email: string | null }>("/auth/me");
      setUserEmail(data.email);
    } catch {
      // User not authenticated — AuthenticatedLayout will handle redirect
    }
  }, []);

  // Load user info on mount
  useState(() => {
    fetchUser();
  });

  const handleNewSession = useCallback(async () => {
    try {
      const newSession = await createPersistentSession();
      await onRefresh();
      navigate(`/chat/${newSession.id}`);
    } catch (err) {
      console.error("Failed to create session:", err);
    }
  }, [navigate, onRefresh]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTargetId) return;
    setIsDeleting(true);
    try {
      await deleteSessionApi(deleteTargetId);
      await onRefresh();
      // If we deleted the active session, navigate to /chat to get a new one
      if (deleteTargetId === activeSessionId) {
        navigate("/chat");
      }
    } catch (err) {
      console.error("Failed to delete session:", err);
    } finally {
      setIsDeleting(false);
      setDeleteTargetId(null);
    }
  }, [deleteTargetId, activeSessionId, navigate, onRefresh]);

  const handleRenameConfirm = useCallback(async () => {
    if (!renameTarget || !renameValue.trim()) return;
    try {
      const { updateSessionTitle } = await import("@/lib/sessions");
      await updateSessionTitle(renameTarget.id, renameValue.trim());
      await onRefresh();
    } catch (err) {
      console.error("Failed to rename session:", err);
    } finally {
      setRenameTarget(null);
      setRenameValue("");
    }
  }, [renameTarget, renameValue, onRefresh]);

  const handleSignOut = useCallback(async () => {
    setLogoutLoading(true);
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      // Redirect anyway
    }
    clearSentryUser();
    navigate("/login");
  }, [navigate]);

  const { today, thisWeek, earlier } = groupSessions(sessions);
  const deleteTargetTitle = sessions.find((s) => s.id === deleteTargetId)?.title ?? "Untitled session";

  return (
    <>
      <Sidebar collapsible="icon">
        {/* Header — logo mark only (wordmark hidden when collapsed) */}
        <SidebarHeader className="px-3 pt-3 pb-2">
          <div className="flex items-center gap-2">
            <div className="size-7 rounded-md bg-primary flex items-center justify-center text-primary-foreground font-display font-semibold text-xs shrink-0">
              K
            </div>
            <span className="font-display text-base tracking-tight text-foreground group-data-[collapsible=icon]:hidden">
              Kendrew<span className="text-primary">.AI</span>
            </span>
          </div>
        </SidebarHeader>

        {/* New Session button */}
        <div className="px-2 pb-2 group-data-[collapsible=icon]:hidden">
          <Button
            variant="default"
            size="sm"
            className="w-full"
            onClick={handleNewSession}
          >
            Start new session
          </Button>
        </div>

        {/* Session history list */}
        <SidebarContent>
          <ScrollArea className="flex-1">
            {sessionsLoading ? (
              <SessionSkeletons />
            ) : sessions.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground group-data-[collapsible=icon]:hidden">
                No sessions yet. Start one above.
              </p>
            ) : (
              <>
                {today.length > 0 && (
                  <SidebarGroup>
                    <SidebarGroupLabel>Today</SidebarGroupLabel>
                    <SidebarMenu>
                      {today.map((s) => (
                        <SessionItem
                          key={s.id}
                          session={s}
                          isActive={s.id === activeSessionId}
                          onDelete={setDeleteTargetId}
                          onRenameStart={(sess) => {
                            setRenameTarget(sess);
                            setRenameValue(sess.title ?? "");
                          }}
                        />
                      ))}
                    </SidebarMenu>
                  </SidebarGroup>
                )}
                {thisWeek.length > 0 && (
                  <SidebarGroup>
                    <SidebarGroupLabel>This week</SidebarGroupLabel>
                    <SidebarMenu>
                      {thisWeek.map((s) => (
                        <SessionItem
                          key={s.id}
                          session={s}
                          isActive={s.id === activeSessionId}
                          onDelete={setDeleteTargetId}
                          onRenameStart={(sess) => {
                            setRenameTarget(sess);
                            setRenameValue(sess.title ?? "");
                          }}
                        />
                      ))}
                    </SidebarMenu>
                  </SidebarGroup>
                )}
                {earlier.length > 0 && (
                  <SidebarGroup>
                    <SidebarGroupLabel>Earlier</SidebarGroupLabel>
                    <SidebarMenu>
                      {earlier.map((s) => (
                        <SessionItem
                          key={s.id}
                          session={s}
                          isActive={s.id === activeSessionId}
                          onDelete={setDeleteTargetId}
                          onRenameStart={(sess) => {
                            setRenameTarget(sess);
                            setRenameValue(sess.title ?? "");
                          }}
                        />
                      ))}
                    </SidebarMenu>
                  </SidebarGroup>
                )}
              </>
            )}
          </ScrollArea>
        </SidebarContent>

        {/* Separator + Navigation links */}
        <SidebarSeparator />

        <SidebarGroup className="group-data-[collapsible=icon]:px-1">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                isActive={location.pathname === "/jobs" || location.pathname.startsWith("/jobs/")}
                className="min-h-[44px]"
                tooltip="Jobs"
              >
                <Link to="/jobs">
                  <Briefcase className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>Jobs</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                isActive={location.pathname === "/settings"}
                className="min-h-[44px]"
                tooltip="Settings"
              >
                <Link to="/settings">
                  <Settings className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>Settings</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>

        {/* Footer — user info + logout */}
        <SidebarFooter className="pb-3">
          <SidebarMenu>
            <SidebarMenuItem>
              <div className="flex items-center gap-2.5 px-2 py-1.5">
                {/* Avatar circle */}
                <div
                  className="h-8 w-8 rounded-full bg-secondary text-foreground flex items-center justify-center font-semibold text-sm shrink-0"
                  aria-hidden="true"
                >
                  {userEmail ? userEmail[0].toUpperCase() : "?"}
                </div>
                {/* Email — hidden when collapsed */}
                <span className="text-sm text-muted-foreground truncate flex-1 group-data-[collapsible=icon]:hidden">
                  {userEmail ?? "Loading..."}
                </span>
                {/* Logout button */}
                <button
                  onClick={handleSignOut}
                  disabled={logoutLoading}
                  aria-label="Sign out"
                  className="h-7 w-7 inline-flex items-center justify-center rounded-sm hover:bg-sidebar-accent text-muted-foreground hover:text-foreground disabled:opacity-50 shrink-0 group-data-[collapsible=icon]:hidden"
                >
                  <LogOut className="h-3.5 w-3.5" />
                </button>
              </div>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>

      {/* Delete confirmation dialog */}
      <Dialog open={!!deleteTargetId} onOpenChange={(open) => !open && setDeleteTargetId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete session?</DialogTitle>
            <DialogDescription>
              This will permanently delete this conversation and all associated messages. Jobs linked to this session are not deleted.
            </DialogDescription>
          </DialogHeader>
          <div className="py-1 text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{deleteTargetTitle}</span>
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setDeleteTargetId(null)}
              disabled={isDeleting}
            >
              Keep session
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={isDeleting}
            >
              {isDeleting ? "Deleting..." : "Delete session"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rename dialog */}
      <Dialog open={!!renameTarget} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename session</DialogTitle>
          </DialogHeader>
          <input
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRenameConfirm()}
            className="w-full px-3 py-2 text-sm bg-input border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            aria-label="Session title"
            autoFocus
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameTarget(null)}>
              Cancel
            </Button>
            <Button onClick={handleRenameConfirm} disabled={!renameValue.trim()}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
