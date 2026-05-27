/**
 * AdminLayout — shell component for all /admin/* routes.
 *
 * Responsibilities:
 * 1. Auth guard — fetches /user/settings to check is_admin flag.
 *    - Non-admin (or unauthenticated) users are silently redirected to /chat
 *      without revealing that an admin area exists (D-04).
 *    - Network errors (401) redirect to /login.
 * 2. Sidebar navigation with collapsible icon-only mode.
 * 3. Header with "Bindwave Admin" branding and admin email.
 * 4. Renders child routes via <Outlet />.
 *
 * Structurally separate from AuthenticatedLayout — admin has different nav
 * structure and no session sidebar (D-06).
 *
 * Usage in App.tsx:
 * ```tsx
 * <Route element={<AdminLayout />}>
 *   <Route path="/admin" element={<AdminUsersPage />} />
 *   ...
 * </Route>
 * ```
 */

import { useState, useEffect } from "react";
import { Outlet, useNavigate, useLocation, Link } from "react-router-dom";
import {
  Users,
  Cpu,
  DollarSign,
  Activity,
  ClipboardList,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { api } from "@/lib/api";

interface UserSettings {
  email: string;
  display_name: string;
  is_admin: boolean;
  notification_preferences?: Record<string, unknown>;
}

/** Nav items for the admin sidebar — order matches D-07 */
const navItems = [
  { path: "/admin/users", label: "Users", icon: Users },
  { path: "/admin/jobs", label: "Jobs", icon: Cpu },
  { path: "/admin/revenue", label: "Revenue", icon: DollarSign },
  { path: "/admin/system", label: "System", icon: Activity },
  { path: "/admin/audit", label: "Audit Log", icon: ClipboardList },
] as const;

export function AdminLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  /** null = auth check in progress; true/false = result */
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [adminEmail, setAdminEmail] = useState<string>("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function checkAdmin() {
      try {
        const settings = await api<UserSettings>("/user/settings");
        if (cancelled) return;

        if (settings.is_admin === true) {
          setIsAdmin(true);
          setAdminEmail(settings.email);
        } else {
          // Non-admin: silent redirect, do not reveal admin area exists
          navigate("/chat", { replace: true });
        }
      } catch {
        if (cancelled) return;
        // Auth failure (401) or network error — redirect to login
        navigate("/login", { replace: true });
      }
    }

    void checkAdmin();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  // While auth check is in progress, render nothing to prevent flash
  if (isAdmin === null) return null;
  if (!isAdmin) return null;

  /**
   * Determine active nav item.
   * /admin and /admin/users both highlight the Users tab.
   */
  function isActive(itemPath: string): boolean {
    if (itemPath === "/admin/users") {
      return (
        location.pathname === "/admin" ||
        location.pathname.startsWith("/admin/users")
      );
    }
    return location.pathname.startsWith(itemPath);
  }

  return (
    <div className="flex flex-col h-screen bg-background text-foreground">
      {/* ── Admin Header ── */}
      <header className="h-12 flex items-center justify-between px-4 border-b border-border flex-shrink-0">
        <span className="text-sm font-semibold text-foreground">
          Bindwave Admin
        </span>
        {adminEmail && (
          <span className="text-xs text-muted-foreground">{adminEmail}</span>
        )}
      </header>

      {/* ── Body: Sidebar + Main content ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* ── Sidebar ── */}
        <aside
          className={`
            flex flex-col flex-shrink-0 border-r border-border bg-background transition-all duration-200
            ${sidebarCollapsed ? "w-14" : "w-[200px]"}
          `}
        >
          <nav aria-label="Admin navigation" className="flex-1 py-2">
            {navItems.map(({ path, label, icon: Icon }) => {
              const active = isActive(path);
              return (
                <Link
                  key={path}
                  to={path}
                  className={`
                    flex items-center gap-3 px-3 min-h-[44px] text-sm transition-colors
                    ${active
                      ? "border-l-2 border-primary bg-secondary text-foreground font-medium"
                      : "border-l-2 border-transparent text-muted-foreground hover:bg-secondary hover:text-foreground"
                    }
                  `}
                  title={sidebarCollapsed ? label : undefined}
                >
                  <Icon className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                  {!sidebarCollapsed && (
                    <span className="truncate">{label}</span>
                  )}
                </Link>
              );
            })}
          </nav>

          {/* Collapse toggle button */}
          <button
            onClick={() => setSidebarCollapsed((prev) => !prev)}
            className="flex items-center justify-center h-10 border-t border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </button>
        </aside>

        {/* ── Main Content ── */}
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
