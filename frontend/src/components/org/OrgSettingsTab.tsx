/**
 * OrgSettingsTab — organization name + delete-org (owner only).
 *
 * Name field is read-only for non-owners.
 * Delete-org button opens a confirmation dialog and POSTs the typed-name
 * pattern to guard against accidental clicks. On success, redirects the
 * user to the active-org reset flow (window.location reload after clearing
 * the stored id).
 */

import { useEffect, useState } from "react";

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
import { deleteOrg, renameOrg } from "@/lib/organizations";

interface OrgSettingsTabProps {
  orgId: string;
}

export function OrgSettingsTab({ orgId }: OrgSettingsTabProps) {
  const { activeOrg, role: myRole, refresh } = useOrgContext();
  const isOwner = myRole === "owner";

  const [name, setName] = useState(activeOrg?.name ?? "");
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Keep local name in sync if activeOrg changes (e.g. after refresh).
  useEffect(() => {
    setName(activeOrg?.name ?? "");
  }, [activeOrg?.name]);

  async function handleSave() {
    if (!name.trim() || name.trim() === activeOrg?.name) return;
    setSaving(true);
    setSaveError(null);
    try {
      await renameOrg(orgId, name.trim());
      await refresh();
      setSavedMessage(true);
      setTimeout(() => setSavedMessage(false), 3000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (deleteConfirm !== activeOrg?.name) {
      setDeleteError("Type the organization name exactly to confirm.");
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteOrg(orgId);
      // Clear active-org id so the next mount falls back to the personal org.
      try {
        localStorage.removeItem("kendrew.activeOrgId");
      } catch {
        // ignore
      }
      window.location.href = "/jobs";
    } catch (err) {
      setDeleteError(
        err instanceof Error ? err.message : "Failed to delete organization.",
      );
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="space-y-2">
        <Label htmlFor="org-name">Organization name</Label>
        <Input
          id="org-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={100}
          disabled={!isOwner || saving}
          aria-describedby={saveError ? "org-name-error" : undefined}
        />
        {saveError && (
          <p id="org-name-error" role="alert" className="text-sm text-destructive">
            {saveError}
          </p>
        )}
        {savedMessage && (
          <p role="status" className="text-sm text-green-400">
            Changes saved.
          </p>
        )}
        {isOwner && (
          <Button
            onClick={handleSave}
            disabled={
              saving ||
              !name.trim() ||
              name.trim() === activeOrg?.name
            }
            size="sm"
          >
            {saving ? "Saving..." : "Save changes"}
          </Button>
        )}
      </div>

      {isOwner && activeOrg && !activeOrg.is_personal && (
        <div className="border-t border-border/50 pt-6">
          <p className="text-sm font-semibold text-foreground mb-1">
            Danger zone
          </p>
          <p className="text-xs text-muted-foreground mb-3">
            Deleting an organization removes all members and detaches any
            shared job history. This cannot be undone.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setDeleteOpen(true);
              setDeleteConfirm("");
              setDeleteError(null);
            }}
          >
            Delete organization
          </Button>
        </div>
      )}

      <Dialog
        open={deleteOpen}
        onOpenChange={(open) => !deleting && setDeleteOpen(open)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete organization</DialogTitle>
            <DialogDescription>
              Type <strong>{activeOrg?.name}</strong> to confirm. All members
              will lose access. Existing jobs remain billed to the org's
              Stripe customer.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Input
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              placeholder={activeOrg?.name ?? ""}
              autoFocus
              aria-label="Type organization name to confirm"
            />
            {deleteError && (
              <p role="alert" className="text-sm text-destructive mt-2">
                {deleteError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setDeleteOpen(false)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="outline"
              onClick={handleDelete}
              disabled={deleting || deleteConfirm !== activeOrg?.name}
            >
              {deleting ? "Deleting..." : "Delete organization"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default OrgSettingsTab;
