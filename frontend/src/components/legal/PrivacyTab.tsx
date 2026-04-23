/**
 * PrivacyTab — Settings → Privacy tab body (Plan 10-04).
 *
 * Three sections:
 *   1. Your data — "Export my data" button + status indicator + download link when ready.
 *   2. Delete my account — destructive button opens a Dialog requiring the
 *      user to type "DELETE MY ACCOUNT" (acts as a CSRF-like defense on top
 *      of the global double-submit middleware).
 *   3. When a deletion is pending, a yellow banner with the scheduled date
 *      and a "Cancel deletion" button replaces Section 2.
 *
 * Replaces the Plan 10-06 placeholder body in SettingsPage.tsx — the
 * TabsTrigger/TabsContent registration and whitelist entry are untouched.
 */

import { useState, useEffect, useCallback } from "react";
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
import {
  cancelAccountDeletion,
  getExportStatus,
  requestAccountDeletion,
  requestDataExport,
  type ExportStatus,
  type UserSettings,
} from "@/lib/user";

const CONFIRMATION_PHRASE = "DELETE MY ACCOUNT";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function addDays(isoStart: string, days: number): string {
  const d = new Date(isoStart);
  d.setDate(d.getDate() + days);
  return d.toISOString();
}

interface PrivacyTabProps {
  /** Current settings from GET /user/settings — drives the pending-deletion banner. */
  initialSettings: UserSettings | null;
  /** Called after a state change (delete, cancel, export) so the parent can refresh settings. */
  onChanged: () => void;
}

export function PrivacyTab({ initialSettings, onChanged }: PrivacyTabProps) {
  // ---- Export section state ----
  const [exportStatus, setExportStatus] = useState<ExportStatus | null>(null);
  const [exportSubmitting, setExportSubmitting] = useState(false);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  // ---- Delete section state ----
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [confirmationInput, setConfirmationInput] = useState("");
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // ---- Cancel section state ----
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const deletionRequestedAt = initialSettings?.deletion_requested_at ?? null;
  const deletionPending = Boolean(deletionRequestedAt);
  const scheduledFor = deletionPending && deletionRequestedAt
    ? addDays(deletionRequestedAt, 30)
    : null;

  const refreshExportStatus = useCallback(async () => {
    try {
      const status = await getExportStatus();
      setExportStatus(status);
    } catch {
      // Status fetch is best-effort; leave the last known value in place.
    }
  }, []);

  useEffect(() => {
    refreshExportStatus();
  }, [refreshExportStatus]);

  async function handleRequestExport() {
    setExportSubmitting(true);
    setExportError(null);
    setExportMessage(null);
    try {
      const resp = await requestDataExport();
      setExportMessage(resp.message ?? "Export is being prepared; you will receive an email when it is ready.");
      // Status flips to "pending" immediately; we poll once after a short delay
      // so the UI shows "pending" from the backend rather than a stale "none".
      setTimeout(() => {
        refreshExportStatus();
      }, 500);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unable to request export. Try again or contact support.";
      setExportError(msg);
    } finally {
      setExportSubmitting(false);
    }
  }

  async function handleConfirmDelete() {
    setDeleteSubmitting(true);
    setDeleteError(null);
    try {
      await requestAccountDeletion(confirmationInput);
      setDeleteDialogOpen(false);
      setConfirmationInput("");
      onChanged();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unable to schedule deletion. Try again.";
      setDeleteError(msg);
    } finally {
      setDeleteSubmitting(false);
    }
  }

  async function handleCancelDelete() {
    setCancelSubmitting(true);
    setCancelError(null);
    try {
      await cancelAccountDeletion();
      onChanged();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unable to cancel deletion. Try again.";
      setCancelError(msg);
    } finally {
      setCancelSubmitting(false);
    }
  }

  const canSubmitDelete = confirmationInput === CONFIRMATION_PHRASE;

  return (
    <div className="space-y-8 pt-4">
      {/* ------------------------------------------------------------------- */}
      {/* Section 1: Export my data                                             */}
      {/* ------------------------------------------------------------------- */}
      <section aria-labelledby="privacy-export-heading" className="space-y-3">
        <h2 id="privacy-export-heading" className="text-base font-semibold text-foreground">
          Your data
        </h2>
        <p className="text-sm text-muted-foreground">
          Download a ZIP of everything we hold on your account — profile,
          sessions, jobs, and a manifest of referenced structure files. GDPR
          Article 20 (data portability).
        </p>

        <Button onClick={handleRequestExport} disabled={exportSubmitting}>
          {exportSubmitting ? "Requesting..." : "Export my data"}
        </Button>

        {exportMessage && (
          <p role="status" className="text-sm text-muted-foreground">
            {exportMessage}
          </p>
        )}

        {exportError && (
          <p role="alert" className="text-sm text-destructive">
            {exportError}
          </p>
        )}

        {exportStatus && exportStatus.status === "ready" && exportStatus.url && (
          <div className="rounded-md border border-border/50 bg-card p-3 text-sm">
            <p className="text-foreground mb-1">Your export is ready.</p>
            <a
              href={exportStatus.url}
              className="text-primary underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              Download my data
            </a>
            {exportStatus.expires_at && (
              <p className="mt-1 text-xs text-muted-foreground">
                Link expires {formatDate(exportStatus.expires_at)}.
              </p>
            )}
          </div>
        )}

        {exportStatus && exportStatus.status === "pending" && (
          <p className="text-sm text-muted-foreground">
            Export is being prepared; you will receive an email when it is ready.
          </p>
        )}

        {exportStatus && exportStatus.status === "expired" && (
          <p className="text-sm text-muted-foreground">
            Your previous export link has expired. Request a new one above.
          </p>
        )}
      </section>

      {/* ------------------------------------------------------------------- */}
      {/* Section 2: Delete account OR pending-deletion banner                  */}
      {/* ------------------------------------------------------------------- */}
      {deletionPending ? (
        <section
          aria-labelledby="privacy-pending-heading"
          className="space-y-3 rounded-md border border-yellow-500/40 bg-yellow-500/10 p-4"
        >
          <h2 id="privacy-pending-heading" className="text-base font-semibold text-foreground">
            Account deletion scheduled
          </h2>
          <p className="text-sm text-foreground">
            Your account and all associated data will be permanently deleted on{" "}
            <strong>{formatDate(scheduledFor)}</strong>. You can cancel any time
            before then.
          </p>
          {cancelError && (
            <p role="alert" className="text-sm text-destructive">
              {cancelError}
            </p>
          )}
          <Button
            variant="outline"
            onClick={handleCancelDelete}
            disabled={cancelSubmitting}
          >
            {cancelSubmitting ? "Cancelling..." : "Cancel deletion"}
          </Button>
        </section>
      ) : (
        <section aria-labelledby="privacy-delete-heading" className="space-y-3">
          <h2 id="privacy-delete-heading" className="text-base font-semibold text-foreground">
            Delete my account
          </h2>
          <p className="text-sm text-muted-foreground">
            Permanently delete your account, all stored structures, job history,
            and Stripe customer record. GDPR Article 17 (right to erasure). A
            30-day grace period applies — you can cancel during that window.
          </p>
          <Button variant="destructive" onClick={() => setDeleteDialogOpen(true)}>
            Delete my account
          </Button>
        </section>
      )}

      {/* Delete confirmation dialog */}
      <Dialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteDialogOpen(false);
            setConfirmationInput("");
            setDeleteError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete account?</DialogTitle>
            <DialogDescription>
              This cannot be undone after the 30-day grace period. Type{" "}
              <code className="font-mono text-foreground">{CONFIRMATION_PHRASE}</code>{" "}
              below to confirm.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="delete-confirm">Confirmation</Label>
            <Input
              id="delete-confirm"
              value={confirmationInput}
              onChange={(e) => setConfirmationInput(e.target.value)}
              placeholder={CONFIRMATION_PHRASE}
              autoComplete="off"
            />
            {deleteError && (
              <p role="alert" className="text-sm text-destructive">
                {deleteError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setDeleteDialogOpen(false);
                setConfirmationInput("");
                setDeleteError(null);
              }}
              disabled={deleteSubmitting}
            >
              Keep account
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={!canSubmitDelete || deleteSubmitting}
            >
              {deleteSubmitting ? "Scheduling..." : "Schedule deletion"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
