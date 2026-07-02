/**
 * RevokeConfirmModal — type-the-key-name-to-confirm destructive action
 * (Plan 13-06, API-03, threat T-13-02).
 *
 * Revoking a key is irreversible and takes effect immediately — apps using
 * it start receiving 401s. To prevent a fat-finger revoke on a key that's in
 * production use, the Revoke button stays disabled until the user types the
 * key's exact name. The modal is controlled by `apiKey`: non-null → open.
 */

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { revokeApiKey, type ApiKey } from "@/lib/api-keys";

interface RevokeConfirmModalProps {
  /** The key being revoked, or null when the modal is closed. */
  apiKey: ApiKey | null;
  /** Controlled close — called with false to dismiss. */
  onOpenChange: (open: boolean) => void;
  /** Called after a successful revoke so the parent can refetch the list. */
  onRevoked: () => void;
}

export function RevokeConfirmModal({
  apiKey,
  onOpenChange,
  onRevoked,
}: RevokeConfirmModalProps) {
  const [typedName, setTypedName] = useState("");
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = apiKey !== null;
  const canRevoke = apiKey !== null && typedName === apiKey.name;

  function handleOpenChange(next: boolean) {
    if (!next) {
      setTypedName("");
      setError(null);
    }
    onOpenChange(next);
  }

  async function handleRevoke() {
    if (!apiKey || !canRevoke) return;
    setRevoking(true);
    setError(null);
    try {
      await revokeApiKey(apiKey.id);
      setTypedName("");
      onRevoked();
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : "Could not revoke the key. Try again.";
      setError(msg);
    } finally {
      setRevoking(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke this API key?</DialogTitle>
          <DialogDescription>
            Apps using{" "}
            <code className="font-mono text-foreground">
              {apiKey?.prefix}…
            </code>{" "}
            will start receiving 401 errors immediately. This cannot be undone.
            Type the key name to confirm.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 py-2">
          <Label htmlFor="revoke-confirm-name">Key name</Label>
          <Input
            id="revoke-confirm-name"
            value={typedName}
            onChange={(e) => setTypedName(e.target.value)}
            placeholder={apiKey?.name ?? ""}
            autoComplete="off"
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => handleOpenChange(false)}
            disabled={revoking}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleRevoke}
            disabled={!canRevoke || revoking}
          >
            {revoking ? "Revoking..." : "Revoke key"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
