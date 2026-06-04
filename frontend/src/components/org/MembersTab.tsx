/**
 * MembersTab — members table + invite form + role editor + remove + transfer.
 *
 * Owner-only controls:
 *   - "Invite member" form (email + role select).
 *   - Per-row role dropdown (owner|scientist|viewer).
 *   - Per-row Remove button (confirmation dialog).
 *   - "Transfer ownership" button (opens dialog with target select + new
 *     self role select).
 *
 * Non-owner view: read-only members list; no controls.
 *
 * Backend last-owner trigger surfaces as a 400 with detail "Cannot remove
 * the last owner. Transfer ownership first." We catch and display.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useOrgContext } from "./OrganizationContext";
import {
  fetchMembers,
  inviteMember,
  removeMember,
  transferOwnership,
  updateMemberRole,
  type MemberRow,
  type OrgRole,
} from "@/lib/organizations";

const ROLES: OrgRole[] = ["owner", "scientist", "viewer"];

interface MembersTabProps {
  orgId: string;
}

export function MembersTab({ orgId }: MembersTabProps) {
  const { role: myRole } = useOrgContext();
  const isOwner = myRole === "owner";

  const [members, setMembers] = useState<MemberRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Invite form state (owner-only).
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<OrgRole>("scientist");
  const [inviting, setInviting] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null);

  // Remove confirmation dialog.
  const [removeTarget, setRemoveTarget] = useState<MemberRow | null>(null);
  const [removing, setRemoving] = useState(false);

  // Transfer ownership dialog.
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferTargetId, setTransferTargetId] = useState<string>("");
  const [transferNewSelfRole, setTransferNewSelfRole] = useState<
    "scientist" | "viewer"
  >("scientist");
  const [transferring, setTransferring] = useState(false);

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const rows = await fetchMembers(orgId);
      setMembers(rows);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load members.");
    }
  }, [orgId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    setInviting(true);
    setInviteSuccess(null);
    setActionError(null);
    try {
      await inviteMember(orgId, inviteEmail.trim(), inviteRole);
      setInviteSuccess(`Invitation sent to ${inviteEmail.trim()}.`);
      setInviteEmail("");
      setInviteRole("scientist");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to invite.");
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(member: MemberRow, role: OrgRole) {
    setActionError(null);
    try {
      await updateMemberRole(orgId, member.user_id, role);
      await refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to update role.");
    }
  }

  async function handleRemove() {
    if (!removeTarget) return;
    setRemoving(true);
    setActionError(null);
    try {
      await removeMember(orgId, removeTarget.user_id);
      await refresh();
      setRemoveTarget(null);
    } catch (err) {
      setActionError(
        err instanceof Error
          ? err.message
          : "Failed to remove member.",
      );
    } finally {
      setRemoving(false);
    }
  }

  async function handleTransfer() {
    if (!transferTargetId) return;
    setTransferring(true);
    setActionError(null);
    try {
      await transferOwnership(orgId, transferTargetId, transferNewSelfRole);
      setTransferOpen(false);
      // Ownership transferred — the current user's role changed.
      // Reload so OrgContext picks up the new role.
      window.location.reload();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to transfer ownership.",
      );
      setTransferring(false);
    }
  }

  if (loadError) {
    return <p className="text-sm text-destructive pt-4">{loadError}</p>;
  }

  if (members === null) {
    return (
      <div className="pt-4 space-y-2">
        <div className="h-4 w-48 bg-muted rounded animate-pulse" />
        <div className="h-4 w-64 bg-muted rounded animate-pulse" />
      </div>
    );
  }

  const otherOwners = members.filter(
    (m) => m.role === "owner" && m.user_id !== currentUserIdFromMembers(members, myRole),
  );

  return (
    <div className="space-y-6 pt-4">
      {/* Invite member form — owner only */}
      {isOwner && (
        <form
          onSubmit={handleInvite}
          className="space-y-3 border border-border/50 rounded-md p-4"
          aria-label="Invite member"
        >
          <p className="text-sm font-semibold">Invite member</p>
          <div className="flex flex-wrap gap-2 items-end">
            <div className="flex-1 min-w-[180px] space-y-1">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
                required
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="invite-role">Role</Label>
              <select
                id="invite-role"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as OrgRole)}
                className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" disabled={inviting || !inviteEmail.trim()}>
              {inviting ? "Sending..." : "Send invitation"}
            </Button>
          </div>
          {inviteSuccess && (
            <p role="status" className="text-sm text-green-400">
              {inviteSuccess}
            </p>
          )}
        </form>
      )}

      {/* Action error banner */}
      {actionError && (
        <p role="alert" className="text-sm text-destructive">
          {actionError}
        </p>
      )}

      {/* Members table */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm font-semibold">Members ({members.length})</p>
          {isOwner && otherOwners.length === 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setTransferOpen(true);
                // Preselect the first non-owner as the transfer target.
                const candidate = members.find(
                  (m) => m.role !== "owner",
                );
                setTransferTargetId(candidate?.user_id ?? "");
              }}
              disabled={members.filter((m) => m.role !== "owner").length === 0}
            >
              Transfer ownership
            </Button>
          )}
        </div>

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
                  Joined
                </th>
                {isOwner && (
                  <th scope="col" className="px-4 py-2 text-right text-xs text-muted-foreground font-medium">
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id} className="border-b border-border/50 last:border-0">
                  <td className="px-4 py-2 text-foreground" title={m.email}>
                    {m.email}
                  </td>
                  <td className="px-4 py-2">
                    {isOwner ? (
                      <select
                        aria-label={`Role for ${m.email}`}
                        value={m.role}
                        onChange={(e) =>
                          void handleRoleChange(m, e.target.value as OrgRole)
                        }
                        className="bg-secondary border border-border rounded-md px-2 py-1 text-xs text-foreground"
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="text-muted-foreground">{m.role}</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {new Date(m.created_at).toLocaleDateString()}
                  </td>
                  {isOwner && (
                    <td className="px-4 py-2 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setRemoveTarget(m)}
                      >
                        Remove
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
              {members.length === 0 && (
                <tr>
                  <td
                    colSpan={isOwner ? 4 : 3}
                    className="px-4 py-6 text-center text-sm text-muted-foreground"
                  >
                    No members yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Remove confirmation dialog */}
      <Dialog
        open={removeTarget !== null}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove member</DialogTitle>
            <DialogDescription>
              Remove <strong>{removeTarget?.email}</strong> from this
              organization? They will lose access to all org-scoped jobs and
              billing.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setRemoveTarget(null)}
              disabled={removing}
            >
              Cancel
            </Button>
            <Button
              variant="outline"
              onClick={handleRemove}
              disabled={removing}
            >
              {removing ? "Removing..." : "Remove member"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Transfer ownership dialog */}
      <Dialog
        open={transferOpen}
        onOpenChange={(open) => !transferring && setTransferOpen(open)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Transfer ownership</DialogTitle>
            <DialogDescription>
              The selected member becomes owner. You become the role you pick
              below. This action cannot be undone without the new owner's
              cooperation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <Label htmlFor="transfer-target">New owner</Label>
              <select
                id="transfer-target"
                value={transferTargetId}
                onChange={(e) => setTransferTargetId(e.target.value)}
                className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground w-full"
              >
                <option value="">Select a member</option>
                {members
                  .filter((m) => m.role !== "owner")
                  .map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.email}
                    </option>
                  ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="transfer-new-self-role">Your new role</Label>
              <select
                id="transfer-new-self-role"
                value={transferNewSelfRole}
                onChange={(e) =>
                  setTransferNewSelfRole(
                    e.target.value as "scientist" | "viewer",
                  )
                }
                className="bg-secondary border border-border rounded-md px-3 py-1.5 text-sm text-foreground w-full"
              >
                <option value="scientist">scientist</option>
                <option value="viewer">viewer</option>
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setTransferOpen(false)}
              disabled={transferring}
            >
              Cancel
            </Button>
            <Button
              variant="outline"
              onClick={handleTransfer}
              disabled={transferring || !transferTargetId}
            >
              {transferring ? "Transferring..." : "Transfer ownership"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Returns the current user's id by finding the row whose role matches
 * myRole — best-effort heuristic for surfacing the "other owners" count.
 * Falls back to undefined if there's ambiguity.
 */
function currentUserIdFromMembers(
  members: MemberRow[],
  myRole: OrgRole | null,
): string | undefined {
  if (myRole === null) return undefined;
  const matches = members.filter((m) => m.role === myRole);
  return matches.length === 1 ? matches[0].user_id : undefined;
}

export default MembersTab;
