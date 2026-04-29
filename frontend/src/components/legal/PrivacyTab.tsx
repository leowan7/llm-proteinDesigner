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

import { useState, useEffect, useCallback, useRef } from "react";
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
  updateRetentionDays,
  type ExportStatus,
  type UserSettings,
} from "@/lib/user";

const CONFIRMATION_PHRASE = "DELETE MY ACCOUNT";

// Plan 10-05 — retention window bounds mirror the backend CHECK constraint
// (`data_retention_days BETWEEN 30 AND 365`). Duplicated here for fast client
// feedback; the server remains the source of truth.
const RETENTION_MIN_DAYS = 30;
const RETENTION_MAX_DAYS = 365;
const RETENTION_DEFAULT_DAYS = 90;

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
  // Tracks a successful deletion so that onChanged() is deferred until
  // after deleteDialogOpen flips to false and React commits the close.
  // Calling onChanged() synchronously in handleConfirmDelete causes the
  // settings re-fetch re-render to race the Base UI CSS close animation,
  // aborting it mid-flight; Base UI's treatAbortedAsFinished=false path
  // then never calls forceUnmount(), leaving the dialog visually stuck.
  const deletionSucceededRef = useRef(false);

  // ---- Cancel section state ----
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  // ---- Retention section state (Plan 10-05) ----
  // `initialRetentionDays` tracks the server's last-known value so the Save
  // button can remain disabled until the user actually changes something. It
  // updates after a successful PUT so a second click is a no-op.
  const serverRetention =
    initialSettings?.data_retention_days ?? RETENTION_DEFAULT_DAYS;
  const [retentionDays, setRetentionDays] = useState<number>(serverRetention);
  const [initialRetentionDays, setInitialRetentionDays] =
    useState<number>(serverRetention);
  const [retentionSaving, setRetentionSaving] = useState(false);
  const [retentionError, setRetentionError] = useState<string | null>(null);
  const [retentionSaved, setRetentionSaved] = useState(false);

  // Sync the server baseline when /user/settings resolves or re-fetches. We
  // only update the EDITABLE value if the user hasn't touched it yet (i.e. the
  // current value still matches the prior baseline) — otherwise a late-arriving
  // settings response would stomp an in-progress edit.
  useEffect(() => {
    const serverValue = initialSettings?.data_retention_days;
    if (serverValue == null) return;
    setInitialRetentionDays((prevBaseline) => {
      setRetentionDays((prevInput) =>
        prevInput === prevBaseline ? serverValue : prevInput,
      );
      return serverValue;
    });
  }, [initialSettings?.data_retention_days]);

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

  // Fire onChanged() after the dialog's controlled close is committed to DOM
  // (i.e. deleteDialogOpen is already false before this effect runs), so the
  // settings re-fetch re-render cannot race and abort the CSS close animation.
  useEffect(() => {
    if (!deleteDialogOpen && deletionSucceededRef.current) {
      deletionSucceededRef.current = false;
      onChanged();
    }
  }, [deleteDialogOpen, onChanged]);

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
      deletionSucceededRef.current = true;
      setDeleteDialogOpen(false);
      setConfirmationInput("");
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

  async function handleSaveRetention() {
    // Client-side range guard — fast feedback before the round-trip.
    // The backend duplicates this check and is the source of truth.
    if (
      retentionDays < RETENTION_MIN_DAYS ||
      retentionDays > RETENTION_MAX_DAYS ||
      !Number.isFinite(retentionDays)
    ) {
      setRetentionError(
        `Retention must be between ${RETENTION_MIN_DAYS} and ${RETENTION_MAX_DAYS} days.`,
      );
      return;
    }
    setRetentionSaving(true);
    setRetentionError(null);
    setRetentionSaved(false);
    try {
      const resp = await updateRetentionDays(retentionDays);
      setInitialRetentionDays(resp.data_retention_days);
      setRetentionDays(resp.data_retention_days);
      setRetentionSaved(true);
      onChanged();
      setTimeout(() => setRetentionSaved(false), 3000);
    } catch (err) {
      const msg = err instanceof Error
        ? err.message
        : "Unable to update retention. Try again.";
      setRetentionError(msg);
    } finally {
      setRetentionSaving(false);
    }
  }

  const canSubmitDelete = confirmationInput === CONFIRMATION_PHRASE;
  const retentionDirty = retentionDays !== initialRetentionDays;
  const retentionOutOfRange =
    !Number.isFinite(retentionDays) ||
    retentionDays < RETENTION_MIN_DAYS ||
    retentionDays > RETENTION_MAX_DAYS;
  const retentionShortening =
    retentionDirty &&
    !retentionOutOfRange &&
    retentionDays < initialRetentionDays;

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

        {exportStatus && exportStatus.status === "failed" && (
          <p role="alert" className="text-sm text-destructive">
            Your previous export could not be built. Request a new one above
            or contact support@ranomics.com if this repeats.
          </p>
        )}
      </section>

      {/* ------------------------------------------------------------------- */}
      {/* Section 2: Data retention (Plan 10-05)                                */}
      {/* ------------------------------------------------------------------- */}
      <section aria-labelledby="privacy-retention-heading" className="space-y-3">
        <h2
          id="privacy-retention-heading"
          className="text-base font-semibold text-foreground"
        >
          Data retention
        </h2>
        <p className="text-sm text-muted-foreground">
          Job outputs and uploaded structures are automatically deleted after
          the period below. Minimum {RETENTION_MIN_DAYS} days, maximum{" "}
          {RETENTION_MAX_DAYS} days. We email you 7 days before any deletion.
        </p>

        <div className="flex items-center gap-3">
          <Label htmlFor="retention-days" className="sr-only">
            Retention days
          </Label>
          <Input
            id="retention-days"
            type="number"
            min={30}
            max={365}
            value={Number.isFinite(retentionDays) ? retentionDays : ""}
            onChange={(e) => {
              const parsed = parseInt(e.target.value, 10);
              setRetentionDays(
                Number.isFinite(parsed) ? parsed : RETENTION_DEFAULT_DAYS,
              );
              setRetentionSaved(false);
              setRetentionError(null);
            }}
            className="w-24"
            aria-label="Retention days"
            aria-describedby="retention-hint"
          />
          <span id="retention-hint" className="text-sm text-muted-foreground">
            days
          </span>
          <Button
            onClick={handleSaveRetention}
            disabled={!retentionDirty || retentionOutOfRange || retentionSaving}
          >
            {retentionSaving ? "Saving..." : "Save"}
          </Button>
        </div>

        {retentionShortening && (
          <p role="alert" className="text-xs text-yellow-400">
            Shortening retention may delete older jobs at the next daily run.
          </p>
        )}

        {retentionError && (
          <p role="alert" className="text-sm text-destructive">
            {retentionError}
          </p>
        )}

        {retentionSaved && (
          <p role="status" className="text-sm text-green-400">
            Retention updated.
          </p>
        )}
      </section>

      {/* ------------------------------------------------------------------- */}
      {/* Section 3: Delete account OR pending-deletion banner                  */}
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
            <strong>{formatDate(scheduledFor)}</strong>. You can cancel at any
            point during the 30-day grace period. Once the scheduled deletion
            cron begins executing on the final day, a late cancel may not reach
            us in time.
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
        onOpenChange={(nextOpen) => {
          setDeleteDialogOpen(nextOpen);
          if (!nextOpen) {
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
