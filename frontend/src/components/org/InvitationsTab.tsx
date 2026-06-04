/**
 * InvitationsTab — pending invitations table (owner-only).
 *
 * Columns: invited email, role, expires, actions (copy invite link + revoke).
 *
 * Copy-link generates a public-facing URL with the invitation token; users
 * can paste it into Slack/email or rely on the auto-sent invite email.
 *
 * Revoke calls DELETE /organizations/{id}/invitations/{invite_id} and
 * refreshes the list.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useOrgContext } from "./OrganizationContext";
import {
  fetchPendingInvitations,
  revokeInvitation,
  type InvitationRow,
} from "@/lib/organizations";

interface InvitationsTabProps {
  orgId: string;
}

export function InvitationsTab({ orgId }: InvitationsTabProps) {
  const { role: myRole } = useOrgContext();
  const isOwner = myRole === "owner";

  const [invitations, setInvitations] = useState<InvitationRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const rows = await fetchPendingInvitations(orgId);
      setInvitations(rows);
    } catch (err) {
      setLoadError(
        err instanceof Error ? err.message : "Failed to load invitations.",
      );
    }
  }, [orgId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleRevoke(invite: InvitationRow) {
    setActionError(null);
    try {
      await revokeInvitation(orgId, invite.id);
      await refresh();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to revoke invitation.",
      );
    }
  }

  async function handleCopyLink(invite: InvitationRow) {
    // The accept URL uses the bearer ``token`` from the invitation row, not
    // the row UUID — the backend looks up by ``WHERE token = $1`` (Plan 12-06
    // bug-fix). Only owners see the token; if it's null (older response or
    // permission gap) the action surfaces an explanatory error.
    if (!invite.token) {
      setActionError(
        "Copy-link is unavailable for this invitation. Resend the invite to generate a fresh link.",
      );
      return;
    }
    const url = `${window.location.origin}/invitations/accept?token=${encodeURIComponent(invite.token)}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedId(invite.id);
      setTimeout(() => setCopiedId((id) => (id === invite.id ? null : id)), 2000);
    } catch {
      setActionError("Failed to copy link. Copy manually: " + url);
    }
  }

  if (!isOwner) {
    return (
      <p className="text-sm text-muted-foreground pt-4">
        Only organization owners can view pending invitations.
      </p>
    );
  }

  if (loadError) {
    return <p className="text-sm text-destructive pt-4">{loadError}</p>;
  }

  if (invitations === null) {
    return (
      <div className="pt-4 space-y-2">
        <div className="h-4 w-48 bg-muted rounded animate-pulse" />
        <div className="h-4 w-64 bg-muted rounded animate-pulse" />
      </div>
    );
  }

  return (
    <div className="space-y-4 pt-4">
      {actionError && (
        <p role="alert" className="text-sm text-destructive">
          {actionError}
        </p>
      )}

      <div className="rounded-md border border-border/50 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/50 bg-card">
              <th scope="col" className="px-4 py-2 text-left text-xs text-muted-foreground font-medium">
                Email
              </th>
              <th scope="col" className="px-4 py-2 text-left text-xs text-muted-foreground font-medium">
                Role
              </th>
              <th scope="col" className="px-4 py-2 text-left text-xs text-muted-foreground font-medium">
                Expires
              </th>
              <th scope="col" className="px-4 py-2 text-right text-xs text-muted-foreground font-medium">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {invitations.map((invite) => (
              <tr
                key={invite.id}
                className="border-b border-border/50 last:border-0"
              >
                <td className="px-4 py-2 text-foreground" title={invite.email}>
                  {invite.email}
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  {invite.role}
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  {new Date(invite.expires_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-2 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void handleCopyLink(invite)}
                    >
                      {copiedId === invite.id ? "Copied" : "Copy link"}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void handleRevoke(invite)}
                    >
                      Revoke
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
            {invitations.length === 0 && (
              <tr>
                <td
                  colSpan={4}
                  className="px-4 py-6 text-center text-sm text-muted-foreground"
                >
                  No pending invitations.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default InvitationsTab;
