/**
 * CreateApiKeyModal — 2-stage key creation flow (Plan 13-06, API-01).
 *
 * Stage 1: collect a display name, click Create → POST /user/api-keys.
 * Stage 2: show the plaintext secret EXACTLY ONCE with a copy-to-clipboard
 *          button and an "I have saved this key" confirmation checkbox that
 *          GATES dismissal.
 *
 * Cannot-dismiss invariant (the core contract): while in stage 2 and the
 * confirmation checkbox is unchecked, the modal must not close via ANY
 * vector — Escape, backdrop click, or the X button. All of these route
 * through the Base UI Dialog's onOpenChange handler, so a single wrapper
 * (handleOpenChange) that rejects `open === false` when unconfirmed blocks
 * every close path at once. The plaintext is never re-displayed after close.
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
import { createApiKey, type CreatedApiKey } from "@/lib/api-keys";

interface CreateApiKeyModalProps {
  /** Whether the modal is mounted and visible. */
  open: boolean;
  /** Controlled open state setter — respects the cannot-dismiss invariant. */
  onOpenChange: (open: boolean) => void;
  /** Called with the created key after the user confirms + closes stage 2. */
  onCreated: (key: CreatedApiKey) => void;
}

export function CreateApiKeyModal({
  open,
  onOpenChange,
  onCreated,
}: CreateApiKeyModalProps) {
  const [stage, setStage] = useState<1 | 2>(1);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function resetState() {
    setStage(1);
    setName("");
    setCreating(false);
    setCreated(null);
    setConfirmed(false);
    setCopied(false);
    setError(null);
  }

  async function handleCreate() {
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const key = await createApiKey(name.trim());
      setCreated(key);
      setStage(2);
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : "Could not create the API key. Try again.";
      setError(msg);
    } finally {
      setCreating(false);
    }
  }

  async function handleCopy() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.plaintext);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard write can reject (permissions / insecure context). The
      // user can still select the text manually, so we swallow this.
    }
  }

  /**
   * Single choke point for every close vector. Base UI routes Escape,
   * backdrop clicks, and the X button through onOpenChange(false); we reject
   * all of them while stage 2 is unconfirmed. On a permitted close we fire
   * onCreated + reset before propagating.
   */
  function handleOpenChange(next: boolean) {
    if (!next && stage === 2 && !confirmed) {
      // Reject the close — plaintext-once contract not yet acknowledged.
      return;
    }
    if (!next) {
      if (created) onCreated(created);
      resetState();
    }
    onOpenChange(next);
  }

  function handleConfirmedClose() {
    // Explicit Close button in stage 2 — confirmed gate already enforced by
    // the disabled attribute, but re-route through handleOpenChange for the
    // single onCreated/reset path.
    handleOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent showCloseButton={stage === 1}>
        {stage === 1 ? (
          <>
            <DialogHeader>
              <DialogTitle>Create API key</DialogTitle>
              <DialogDescription>
                Give this key a name so you can recognize it later. You'll see
                the secret once, immediately after it's created.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2 py-2">
              <Label htmlFor="api-key-name">Key name</Label>
              <Input
                id="api-key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Local dev"
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
                disabled={creating}
              >
                Cancel
              </Button>
              <Button
                onClick={handleCreate}
                disabled={!name.trim() || creating}
              >
                {creating ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Save your API key</DialogTitle>
              <DialogDescription>
                This is the only time we'll show this key. Copy it now and
                store it somewhere safe — we can't recover or re-display it.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3 py-2">
              <div className="flex items-center gap-2">
                <Label htmlFor="api-key-plaintext" className="sr-only">
                  API key
                </Label>
                <Input
                  id="api-key-plaintext"
                  value={created?.plaintext ?? ""}
                  readOnly
                  className="font-mono"
                  onFocus={(e) => e.currentTarget.select()}
                />
                <Button
                  variant="outline"
                  onClick={handleCopy}
                  className="shrink-0"
                >
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <Label
                htmlFor="api-key-confirm"
                className="items-start gap-2 font-normal"
              >
                <input
                  id="api-key-confirm"
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  className="mt-0.5 size-4 shrink-0 accent-primary"
                />
                <span className="text-sm text-muted-foreground">
                  I have saved this key. I understand it will not be shown
                  again.
                </span>
              </Label>
            </div>
            <DialogFooter>
              <Button disabled={!confirmed} onClick={handleConfirmedClose}>
                Close
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
