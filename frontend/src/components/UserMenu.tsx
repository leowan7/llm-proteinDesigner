/**
 * UserMenu — avatar button with dropdown for sign out, settings, and sessions.
 *
 * Shows the first letter of the user's email in a circle. Dropdown includes:
 * - User email (display only)
 * - Previous sessions (placeholder — navigates to /sessions when built)
 * - Settings (placeholder — navigates to /settings when built)
 * - Sign out
 */

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { LogOut, Settings, History } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { clearSentryUser } from "@/lib/sentry";

interface UserInfo {
  user_id: string;
  email: string | null;
}

export function UserMenu() {
  const navigate = useNavigate();
  const [user, setUser] = useState<UserInfo | null>(null);

  useEffect(() => {
    api<UserInfo>("/auth/me")
      .then(setUser)
      .catch(() => {
        // Not authenticated — redirect to login
        navigate("/login");
      });
  }, [navigate]);

  async function handleSignOut() {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      // Clear cookies failed — still redirect
    }
    clearSentryUser();
    navigate("/login");
  }

  const initial = user?.email ? user.email[0].toUpperCase() : "?";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-primary/15 text-primary font-semibold text-sm hover:bg-primary/25 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {initial}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuGroup>
          <DropdownMenuLabel>
            {user?.email ?? "Loading..."}
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => navigate("/sessions")}
          className="cursor-pointer"
        >
          <History className="mr-2 h-4 w-4" />
          Previous sessions
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => navigate("/settings")}
          className="cursor-pointer"
        >
          <Settings className="mr-2 h-4 w-4" />
          Settings
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={handleSignOut}
          variant="destructive"
          className="cursor-pointer"
        >
          <LogOut className="mr-2 h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
