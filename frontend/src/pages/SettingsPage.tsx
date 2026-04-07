/**
 * SettingsPage — user settings at /settings.
 *
 * Four tabs:
 *   Account     — display name, email (read-only), password change
 *   Billing     — payment method on file, Stripe portal link
 *   Usage       — current billing period summary and recent charges
 *   Notifications — email toggle preferences
 *
 * All form fields are associated via htmlFor/id pairs.
 * Save buttons persist changes via PUT /user/settings.
 * Error and success states follow the UI-SPEC copywriting contract.
 */

import { useState, useEffect, useCallback } from "react";
import { CreditCard } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  getSettings,
  updateSettings,
  getUsage,
  getPaymentMethod,
  createPortalSession,
  type UserSettings,
  type UsageData,
  type PaymentMethod,
} from "@/lib/user";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Formats an ISO date string as "April 1, 2026".
 * Returns an empty string on null/invalid input.
 */
function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * Capitalizes the first letter of a string (for card brand display).
 */
function capitalize(str: string): string {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

// ---------------------------------------------------------------------------
// Account Tab
// ---------------------------------------------------------------------------

interface AccountTabProps {
  initialSettings: UserSettings | null;
  onSaved: () => void;
}

function AccountTab({ initialSettings, onSaved }: AccountTabProps) {
  const [displayName, setDisplayName] = useState(initialSettings?.display_name ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState(false);

  const email = initialSettings?.email ?? "";

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await updateSettings({ display_name: displayName });
      setSavedMessage(true);
      onSaved();
      setTimeout(() => setSavedMessage(false), 3000);
    } catch {
      setSaveError("Changes could not be saved. Check your connection and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="space-y-2">
        <Label htmlFor="display-name">Display name</Label>
        <Input
          id="display-name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          aria-describedby={saveError ? "account-save-error" : undefined}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          value={email}
          disabled
          className="opacity-60"
          aria-describedby="email-note"
        />
        <p id="email-note" className="text-xs text-muted-foreground">
          Email is managed through your account provider and cannot be changed here.
        </p>
      </div>

      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">Password</p>
        <Button variant="outline" size="sm">
          Change password
        </Button>
      </div>

      {saveError && (
        <p id="account-save-error" role="alert" className="text-sm text-destructive">
          {saveError}
        </p>
      )}

      {savedMessage && (
        <p role="status" className="text-sm text-green-400">
          Changes saved.
        </p>
      )}

      <Button onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save changes"}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Billing Tab
// ---------------------------------------------------------------------------

function BillingTab() {
  const [payment, setPayment] = useState<PaymentMethod | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [portalError, setPortalError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getPaymentMethod()
      .then((data) => {
        if (!cancelled) setPayment(data);
      })
      .catch(() => {
        if (!cancelled) setLoadError("Unable to load settings. Refresh the page to try again.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  async function handleManagePayment() {
    setPortalError(null);
    setRedirecting(true);
    try {
      const url = await createPortalSession(window.location.href);
      window.location.href = url;
    } catch {
      setPortalError("Unable to open billing portal. Try again or contact support.");
    } finally {
      setRedirecting(false);
    }
  }

  if (loading) {
    return (
      <div className="pt-4">
        <div className="h-4 w-48 bg-muted rounded animate-pulse mb-2" />
        <div className="h-4 w-32 bg-muted rounded animate-pulse" />
      </div>
    );
  }

  if (loadError) {
    return <p className="text-sm text-destructive pt-4">{loadError}</p>;
  }

  return (
    <div className="space-y-6 pt-4">
      <div className="space-y-2">
        <p className="text-sm font-semibold text-foreground">Payment method</p>

        {payment?.has_payment_method ? (
          <div className="flex items-center gap-3 py-3 px-4 rounded-md bg-card border border-border/50">
            <CreditCard className="w-5 h-5 text-muted-foreground" aria-hidden="true" />
            <span className="text-sm text-foreground">
              {capitalize(payment.brand ?? "")} **** {payment.last4}
            </span>
            <span className="text-sm text-muted-foreground ml-auto">
              Expires {payment.exp_month}/{payment.exp_year}
            </span>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No payment method on file.</p>
        )}
      </div>

      {portalError && (
        <p role="alert" className="text-sm text-destructive">
          {portalError}
        </p>
      )}

      <Button variant="outline" onClick={handleManagePayment} disabled={redirecting}>
        {redirecting ? "Opening portal..." : "Manage payment method"}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Usage Tab
// ---------------------------------------------------------------------------

function UsageTab() {
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getUsage()
      .then((data) => {
        if (!cancelled) setUsage(data);
      })
      .catch(() => {
        if (!cancelled) setLoadError("Unable to load settings. Refresh the page to try again.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="pt-4 space-y-2">
        <div className="h-4 w-40 bg-muted rounded animate-pulse" />
        <div className="h-4 w-64 bg-muted rounded animate-pulse" />
      </div>
    );
  }

  if (loadError) {
    return <p className="text-sm text-destructive pt-4">{loadError}</p>;
  }

  if (!usage) return null;

  const hasCharges = usage.recent_charges.length > 0;

  return (
    <div className="space-y-6 pt-4">
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">Current billing period</p>
        <p className="text-xs text-muted-foreground">
          Since {formatDate(usage.period_start)}
        </p>
      </div>

      {hasCharges ? (
        <div className="space-y-1">
          <p className="text-xl font-semibold text-foreground">
            {usage.job_count} {usage.job_count === 1 ? "job" : "jobs"} &middot;{" "}
            <span className="font-mono">${usage.total_spend_usd.toFixed(2)}</span> GPU compute
          </p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No usage recorded this billing period.</p>
      )}

      {hasCharges && (
        <div>
          <p className="text-sm font-semibold text-foreground mb-3">Recent charges</p>
          <div className="rounded-md border border-border/50 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 bg-card">
                  <th scope="col" className="px-4 py-2 text-left text-xs text-muted-foreground font-medium">
                    Job
                  </th>
                  <th scope="col" className="px-4 py-2 text-left text-xs text-muted-foreground font-medium">
                    Tool
                  </th>
                  <th scope="col" className="px-4 py-2 text-left text-xs text-muted-foreground font-medium">
                    Date
                  </th>
                  <th scope="col" className="px-4 py-2 text-right text-xs text-muted-foreground font-medium">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody>
                {usage.recent_charges.slice(0, 10).map((charge) => (
                  <tr key={charge.id} className="border-b border-border/50 last:border-0">
                    <td className="px-4 py-2 text-foreground">
                      {charge.name ?? charge.id.slice(0, 8)}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {charge.tool ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {formatDate(charge.completed_at)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-foreground">
                      ${charge.gpu_cost_usd.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notifications Tab
// ---------------------------------------------------------------------------

interface NotificationToggleProps {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}

function NotificationToggle({
  id,
  label,
  description,
  checked,
  onChange,
}: NotificationToggleProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="space-y-0.5">
        <Label htmlFor={id} className="text-sm font-medium cursor-pointer">
          {label}
        </Label>
        <p id={`${id}-desc`} className="text-xs text-muted-foreground">
          {description}
        </p>
      </div>
      <button
        id={id}
        role="switch"
        aria-checked={checked}
        aria-describedby={`${id}-desc`}
        onClick={() => onChange(!checked)}
        className={[
          "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent",
          "transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          checked ? "bg-primary" : "bg-muted",
        ].join(" ")}
      >
        <span className="sr-only">{label}</span>
        <span
          className={[
            "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-lg",
            "transform transition duration-200",
            checked ? "translate-x-5" : "translate-x-0",
          ].join(" ")}
          aria-hidden="true"
        />
      </button>
    </div>
  );
}

interface NotificationsTabProps {
  initialPrefs: UserSettings["notification_preferences"] | null;
  onSaved: () => void;
}

function NotificationsTab({ initialPrefs, onSaved }: NotificationsTabProps) {
  const [jobComplete, setJobComplete] = useState(initialPrefs?.job_complete ?? true);
  const [jobFailure, setJobFailure] = useState(initialPrefs?.job_failure ?? true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState(false);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await updateSettings({
        notification_preferences: { job_complete: jobComplete, job_failure: jobFailure },
      });
      setSavedMessage(true);
      onSaved();
      setTimeout(() => setSavedMessage(false), 3000);
    } catch {
      setSaveError("Changes could not be saved. Check your connection and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4 pt-4">
      <div className="divide-y divide-border/50">
        <NotificationToggle
          id="notif-job-complete"
          label="Job completion"
          description="Email me when a job finishes successfully."
          checked={jobComplete}
          onChange={setJobComplete}
        />
        <NotificationToggle
          id="notif-job-failure"
          label="Job failure"
          description="Email me when a job fails."
          checked={jobFailure}
          onChange={setJobFailure}
        />
      </div>

      {saveError && (
        <p role="alert" className="text-sm text-destructive">
          {saveError}
        </p>
      )}

      {savedMessage && (
        <p role="status" className="text-sm text-green-400">
          Changes saved.
        </p>
      )}

      <Button onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save changes"}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SettingsPage root
// ---------------------------------------------------------------------------

export function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      const data = await getSettings();
      setSettings(data);
    } catch {
      setLoadError("Unable to load settings. Refresh the page to try again.");
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  return (
    <div className="max-w-[640px] mx-auto px-6 py-8">
      <h1 className="font-display text-[28px] font-semibold mb-6">Settings</h1>

      {loadError && (
        <p role="alert" className="text-sm text-destructive mb-4">
          {loadError}
        </p>
      )}

      <Tabs defaultValue="account">
        <TabsList>
          <TabsTrigger value="account">Account</TabsTrigger>
          <TabsTrigger value="billing">Billing</TabsTrigger>
          <TabsTrigger value="usage">Usage</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
        </TabsList>

        <TabsContent value="account">
          <AccountTab
            initialSettings={settings}
            onSaved={loadSettings}
          />
        </TabsContent>

        <TabsContent value="billing">
          <BillingTab />
        </TabsContent>

        <TabsContent value="usage">
          <UsageTab />
        </TabsContent>

        <TabsContent value="notifications">
          <NotificationsTab
            initialPrefs={settings?.notification_preferences ?? null}
            onSaved={loadSettings}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
