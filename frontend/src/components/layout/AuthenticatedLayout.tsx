/**
 * AuthenticatedLayout — layout wrapper for all authenticated routes.
 *
 * Responsibilities:
 * 1. Auth guard — redirects unauthenticated users to /login
 * 2. Session list state management (lifted from AppSidebar)
 * 3. SidebarProvider + AppSidebar + AppHeader composition
 * 4. Exposes refreshSessions callback to child routes via useOutletContext
 *
 * Usage in App.tsx:
 * ```tsx
 * <Route element={<AuthenticatedLayout />}>
 *   <Route path="/chat/:sessionId" element={<ChatPage />} />
 *   ...
 * </Route>
 * ```
 *
 * Child routes can access the layout context:
 * ```tsx
 * import { useLayoutContext } from "../layout/AuthenticatedLayout";
 * const { refreshSessions } = useLayoutContext();
 * ```
 */

import { useState, useEffect, useCallback } from "react";
import { Outlet, useOutletContext, useNavigate, useParams } from "react-router-dom";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppSidebar } from "./AppSidebar";
import { AppHeader } from "./AppHeader";
import { AppFooter } from "./AppFooter";
import { ReAcceptanceModal } from "@/components/legal/ReAcceptanceModal";
import { listSessions } from "@/lib/sessions";
import { api } from "@/lib/api";
import { getSettings } from "@/lib/user";
import { needsReAcceptance } from "@/lib/legal";
import type { SessionSummary } from "@/lib/sessions";

/** Context shape exposed to child routes via Outlet */
export interface LayoutContext {
  refreshSessions: () => Promise<void>;
}

/**
 * Typed hook for child routes to consume the layout context.
 *
 * @example
 * const { refreshSessions } = useLayoutContext();
 */
export function useLayoutContext() {
  return useOutletContext<LayoutContext>();
}

export function AuthenticatedLayout() {
  const navigate = useNavigate();
  const params = useParams<{ sessionId?: string; id?: string }>();

  // Session list state — owned here, passed to sidebar
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);

  // Auth check state — null means not yet checked
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);

  // Plan 10-02: blocking re-acceptance modal when users.tos_version drifts
  // from settings.tos_current_version.
  const [reAcceptanceOpen, setReAcceptanceOpen] = useState(false);

  // Session title for header — resolved from active session in list
  const activeSessionId = params.sessionId;
  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const sessionTitle = activeSession?.title ?? null;

  /** Fetch and refresh the sessions list */
  const refreshSessions = useCallback(async () => {
    try {
      const data = await listSessions();
      setSessions(data.sessions);
    } catch (err) {
      console.error("Failed to load sessions:", err);
    }
  }, []);

  /** Initial auth check and session load */
  useEffect(() => {
    let cancelled = false;

    async function init() {
      // Verify auth by fetching /auth/me
      try {
        await api("/auth/me");
        if (cancelled) return;
        setIsAuthenticated(true);
      } catch {
        if (cancelled) return;
        setIsAuthenticated(false);
        navigate("/login", { replace: true });
        return;
      }

      // Plan 10-02: pull settings to check tos_version drift. Settings
      // failures must not break the authenticated layout — log and move on.
      try {
        const settings = await getSettings();
        if (
          !cancelled &&
          needsReAcceptance(settings.tos_version, settings.tos_current)
        ) {
          setReAcceptanceOpen(true);
        }
      } catch (err) {
        console.error("Failed to load user settings:", err);
      }

      // Fetch sessions
      try {
        const data = await listSessions();
        if (!cancelled) {
          setSessions(data.sessions);
        }
      } catch (err) {
        console.error("Failed to load sessions:", err);
      } finally {
        if (!cancelled) setSessionsLoading(false);
      }
    }

    init();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  // While auth check is in progress, render nothing to prevent flash
  if (isAuthenticated === null) {
    return null;
  }

  // Not authenticated — redirect handled in useEffect above
  if (!isAuthenticated) {
    return null;
  }

  return (
    <TooltipProvider>
      <SidebarProvider>
        <div className="flex h-screen w-full overflow-hidden">
          <AppSidebar
            sessions={sessions}
            sessionsLoading={sessionsLoading}
            activeSessionId={activeSessionId}
            onRefresh={refreshSessions}
          />
          <main
            id="main-content"
            className="flex-1 flex flex-col min-h-screen overflow-hidden"
          >
            <AppHeader sessionTitle={sessionTitle} />
            <div className="flex-1 overflow-auto">
              <Outlet context={{ refreshSessions } satisfies LayoutContext} />
            </div>
            <AppFooter />
          </main>
        </div>
      </SidebarProvider>
      <ReAcceptanceModal
        open={reAcceptanceOpen}
        onAccepted={() => setReAcceptanceOpen(false)}
      />
    </TooltipProvider>
  );
}
