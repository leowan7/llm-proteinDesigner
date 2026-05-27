import { useState } from "react";
import { Link } from "react-router-dom";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { acceptTos } from "@/lib/legal";

interface ReAcceptanceModalProps {
  /** Whether the modal is mounted and visible. */
  open: boolean;
  /** Called after the backend successfully records the new acceptance. */
  onAccepted: () => void;
}

/**
 * Blocking modal shown to authenticated users whose stored tos_version has
 * drifted from the backend's tos_current (Plan 10-02).
 *
 * Design notes:
 * - No dismiss affordance: the close button is suppressed via
 *   showCloseButton={false}, onOpenChange is a no-op, and the only way out
 *   is clicking "I accept".
 * - This is a UX signal, not a security control — backend API routes are
 *   intentionally not gated on tos_current drift (see threat T-10.02-03).
 * - Authoritative re-acceptance enforcement (blocking API calls) is deferred
 *   to a future hardening pass.
 */
export function ReAcceptanceModal({ open, onAccepted }: ReAcceptanceModalProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAccept() {
    setSubmitting(true);
    setError(null);
    try {
      await acceptTos();
      onAccepted();
    } catch {
      setError("Could not record acceptance. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={() => { /* no-op: acceptance is the only exit */ }}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Updated Terms of Service</DialogTitle>
          <DialogDescription>
            Our Terms of Service and Privacy Policy have been updated. Review
            and accept the new version to continue using Bindwave.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-sm">
          <p>
            <Link
              to="/legal/terms"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2 hover:text-foreground"
            >
              Read Terms of Service
            </Link>
          </p>
          <p>
            <Link
              to="/legal/privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2 hover:text-foreground"
            >
              Read Privacy Policy
            </Link>
          </p>
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button onClick={handleAccept} disabled={submitting}>
            {submitting ? "Saving..." : "I accept"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
