/**
 * AppSidebar — collapsible sidebar for the authenticated app shell.
 *
 * Accessibility:
 * - All icon-only buttons have aria-label (WCAG 1.3.1, 4.1.2)
 * - Touch targets are min 44px tall (WCAG 2.5.8)
 * - Active nav item has aria-current="page"
 * - Session list is a semantic list (role="list" on ul, role="listitem" on li)
 *
 * Navigation links:
 * - Jobs (/jobs)
 * - Settings (/settings)
 *
 * Session list: shows previous sessions grouped Today / This week / Earlier.
 * New session button is at the top.
 */

import { Briefcase, Settings, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Session {
  id: string;
  title: string;
}

interface AppSidebarProps {
  sessions?: Session[];
  currentSessionId?: string;
  onNewSession?: () => void;
  onDeleteSession?: (id: string) => void;
  onNavigateToSession?: (id: string) => void;
  currentPath?: string;
}

export function AppSidebar({
  sessions = [],
  currentSessionId,
  onNewSession,
  onDeleteSession,
  onNavigateToSession,
  currentPath = "/",
}: AppSidebarProps) {
  return (
    <aside
      aria-label="Application sidebar"
      className="flex flex-col h-full w-64 bg-sidebar border-r border-border"
    >
      {/* Sidebar header with logo */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="text-base font-semibold text-foreground">
          Kendrew<span className="text-primary">.AI</span>
        </span>
      </div>

      {/* New session button */}
      <div className="px-3 py-3">
        <Button
          variant="default"
          size="sm"
          className="w-full"
          onClick={onNewSession}
          aria-label="Start new session"
        >
          <Plus className="w-4 h-4 mr-2" aria-hidden="true" />
          Start new session
        </Button>
      </div>

      {/* Session history list */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {sessions.length === 0 ? (
          <p className="text-xs text-muted-foreground px-2 py-1">
            No sessions yet. Start one above.
          </p>
        ) : (
          <ul role="list" className="space-y-0.5">
            {sessions.map((session) => (
              <li key={session.id} role="listitem" className="flex items-center group">
                <button
                  className={[
                    "flex-1 text-left text-sm px-2 py-2 rounded-md min-h-[44px] flex items-center truncate",
                    "hover:bg-secondary transition-colors",
                    session.id === currentSessionId
                      ? "bg-sidebar-primary text-primary font-medium"
                      : "text-foreground",
                  ].join(" ")}
                  onClick={() => onNavigateToSession?.(session.id)}
                  aria-current={session.id === currentSessionId ? "page" : undefined}
                >
                  {session.title || "Untitled session"}
                </button>
                <button
                  aria-label={`Delete session: ${session.title || "Untitled session"}`}
                  className="ml-1 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-destructive/15 text-muted-foreground hover:text-destructive transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
                  onClick={() => onDeleteSession?.(session.id)}
                >
                  <X className="w-3.5 h-3.5" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Navigation links */}
      <nav aria-label="Main navigation" className="px-3 py-3 border-t border-border">
        <ul role="list" className="space-y-0.5">
          <li role="listitem">
            <a
              href="/jobs"
              aria-current={currentPath === "/jobs" ? "page" : undefined}
              className={[
                "flex items-center gap-2 px-2 py-2 rounded-md text-sm min-h-[44px]",
                "hover:bg-secondary transition-colors",
                currentPath === "/jobs"
                  ? "text-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              <Briefcase className="w-4 h-4" aria-hidden="true" />
              Jobs
            </a>
          </li>
          <li role="listitem">
            <a
              href="/settings"
              aria-current={currentPath === "/settings" ? "page" : undefined}
              className={[
                "flex items-center gap-2 px-2 py-2 rounded-md text-sm min-h-[44px]",
                "hover:bg-secondary transition-colors",
                currentPath === "/settings"
                  ? "text-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              <Settings className="w-4 h-4" aria-hidden="true" />
              Settings
            </a>
          </li>
        </ul>
      </nav>
    </aside>
  );
}
