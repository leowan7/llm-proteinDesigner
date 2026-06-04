/**
 * OrganizationSwitcher — header dropdown for switching the active org.
 *
 * Behavior:
 *   - Reads {orgs, activeOrg, setActiveOrg, loading} from OrgContext.
 *   - Renders null while loading or when the user has 0 or 1 org (solo /
 *     single-tenant fallback). This preserves the existing single-user UX
 *     unchanged for users who never create a team.
 *   - With 2+ orgs, renders a DropdownMenu trigger showing the active org's
 *     name plus a "Personal" suffix when is_personal=true.
 *   - Menu items: every org with its role label; checkmark column on the
 *     currently-active org.
 *   - Footer: "Create organization" -> /organizations/new
 *             "Manage organization" -> /settings?tab=organization
 *
 * Clicking an org calls setActiveOrg() which writes localStorage and reloads.
 */

import { ChevronDown, Plus, Settings as SettingsIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { OrgRole } from "@/lib/organizations";
import { useOrgContext } from "./OrganizationContext";

/** Capitalize first letter; used to label roles in the dropdown. */
function formatRole(role: OrgRole): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

export function OrganizationSwitcher() {
  const { orgs, activeOrg, setActiveOrg, loading } = useOrgContext();

  if (loading) return null;
  if (orgs.length <= 1) return null;

  const activeLabel = activeOrg
    ? activeOrg.is_personal
      ? `${activeOrg.name} (Personal)`
      : activeOrg.name
    : "Select organization";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className="gap-1.5 text-sm"
            aria-label="Switch organization"
          >
            <span className="truncate max-w-[180px]">{activeLabel}</span>
            <ChevronDown className="h-3.5 w-3.5 opacity-70" />
          </Button>
        }
      />
      <DropdownMenuContent align="start" className="min-w-[240px]">
        {orgs.map((org) => (
          <DropdownMenuItem
            key={org.id}
            onClick={() => setActiveOrg(org.id)}
            className={org.id === activeOrg?.id ? "bg-accent" : ""}
            data-testid={`org-switcher-item-${org.id}`}
          >
            <span className="flex-1 truncate">
              {org.name}
              {org.is_personal ? (
                <span className="text-xs text-muted-foreground ml-1">
                  (Personal)
                </span>
              ) : null}
            </span>
            <span className="text-xs text-muted-foreground ml-2">
              {formatRole(org.role)}
            </span>
          </DropdownMenuItem>
        ))}

        <DropdownMenuSeparator />

        <DropdownMenuItem
          render={
            <Link
              to="/organizations/new"
              className="flex items-center gap-2"
              data-testid="org-switcher-create"
            >
              <Plus className="h-3.5 w-3.5" /> Create organization
            </Link>
          }
        />

        <DropdownMenuItem
          render={
            <Link
              to="/settings?tab=organization"
              className="flex items-center gap-2"
              data-testid="org-switcher-manage"
            >
              <SettingsIcon className="h-3.5 w-3.5" /> Manage organization
            </Link>
          }
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
