/**
 * AppHeader — slim header for authenticated pages.
 *
 * Contains:
 * - Skip navigation link (sr-only, visible on focus) as first child
 * - SidebarTrigger (collapse/expand sidebar) with tooltip and aria-label
 * - Kendrew logo mark + wordmark
 * - Optional session title (passed from AuthenticatedLayout)
 */

import { SidebarTrigger } from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface AppHeaderProps {
  /** Optional title for the active session, shown in header center area */
  sessionTitle?: string | null;
}

export function AppHeader({ sessionTitle }: AppHeaderProps) {
  return (
    <header className="flex items-center gap-3 px-4 py-2.5 border-b border-border shrink-0 surface-chat">
      {/* Skip navigation — visible only on keyboard focus */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:bg-background focus:text-foreground focus:px-4 focus:py-2 focus:rounded-md focus:ring-2 focus:ring-ring"
      >
        Skip to main content
      </a>

      {/* Sidebar toggle */}
      <Tooltip>
        <TooltipTrigger asChild>
          <SidebarTrigger
            className="h-8 w-8 shrink-0"
            aria-label="Toggle sidebar"
          />
        </TooltipTrigger>
        <TooltipContent side="right">
          <p>Toggle sidebar</p>
        </TooltipContent>
      </Tooltip>

      {/* Logo mark + wordmark */}
      <div className="flex items-center gap-2">
        <div className="size-6 rounded-md bg-primary flex items-center justify-center text-primary-foreground font-display font-semibold text-xs shrink-0">
          K
        </div>
        <span className="font-display text-base tracking-tight text-foreground hidden sm:block">
          Kendrew<span className="text-primary">.AI</span>
        </span>
      </div>

      {/* Session title — shown when a session is active */}
      {sessionTitle && (
        <span className="ml-3 text-sm text-muted-foreground truncate max-w-[300px]">
          {sessionTitle}
        </span>
      )}
    </header>
  );
}
