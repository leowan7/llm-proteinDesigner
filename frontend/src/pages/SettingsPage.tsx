/* eslint-disable react-hooks/set-state-in-effect -- page-level data fetch on mount is intentional */
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
import { useSearchParams } from "react-router-dom";
import { CreditCard } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { PrivacyTab } from "@/components/legal/PrivacyTab";
import { ApiKeysTab } from "@/components/api-keys/ApiKeysTab";
import { useOrgContext } from "@/components/org/OrganizationContext";
import { MembersTab } from "@/components/org/MembersTab";
import { InvitationsTab } from "@/components/org/InvitationsTab";
import { OrgSettingsTab } from "@/components/org/OrgSettingsTab";
import { fetchMembers } from "@/lib/organizations";
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
  const { role, activeOrgId, activeOrg } = useOrgContext();
  const [payment, setPayment] = useState<PaymentMethod | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [portalError, setPortalError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [ownerEmail, setOwnerEmail] = useState<string | null>(null);

  // Plan 12-05: non-owners see an "ask your owner" message instead of the
  // payment portal — backend /billing/* returns 403 to anyone except the
  // active org's owner. We pre-empt the 403 in the UI for polish.
  const nonOwnerGated =
    role !== null && role !== "owner" && activeOrg !== null && !activeOrg.is_personal;

  // Resolve owner email for the non-owner gate copy.
  useEffect(() => {
    let cancelled = false;
    if (!nonOwnerGated || !activeOrgId) return;
    fetchMembers(activeOrgId)
      .then((members) => {
        if (cancelled) return;
        const owner = members.find((m) => m.role === "owner");
        if (owner) setOwnerEmail(owner.email);
      })
      .catch(() => {
        // Silent failure — the gate copy still works without the email.
      });
    return () => {
      cancelled = true;
    };
  }, [nonOwnerGated, activeOrgId]);

  useEffect(() => {
    let cancelled = false;
    if (nonOwnerGated) {
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
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
  }, [nonOwnerGated]);

  if (nonOwnerGated) {
    return (
      <div className="pt-4 space-y-2">
        <p className="text-sm text-foreground">
          Billing is managed by your organization owner.
        </p>
        <p className="text-sm text-muted-foreground">
          Ask <strong>{ownerEmail ?? "your organization owner"}</strong> for
          access if you need to update the payment method or view invoices.
        </p>
      </div>
    );
  }

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

/** Tabs surfaced by SettingsPage. "privacy" scaffold added in Plan 10-06;
 *  Plan 10-04 fills in the Privacy tab content (Export + Delete buttons).
 *  "organization" added in Plan 12-05 — shown only when activeOrg is non-null
 *  and not the user's personal org. */
const VALID_SETTINGS_TABS = [
  "account",
  "billing",
  "privacy",
  "api-keys",
  "usage",
  "notifications",
  "organization",
] as const;

// ---------------------------------------------------------------------------
// Organization Tab (Plan 12-05)
// ---------------------------------------------------------------------------

type OrgSubTab = "members" | "invitations" | "settings";

function OrganizationTab() {
  const { activeOrg, activeOrgId } = useOrgContext();
  const [subTab, setSubTab] = useState<OrgSubTab>("members");

  if (!activeOrg || !activeOrgId) {
    return (
      <p className="text-sm text-muted-foreground pt-4">
        Loading organization...
      </p>
    );
  }

  if (activeOrg.is_personal) {
    return (
      <p className="text-sm text-muted-foreground pt-4">
        You're in your personal workspace. Create an organization to invite
        teammates and share jobs.
      </p>
    );
  }

  return (
    <div className="pt-4">
      <div className="flex items-center gap-1 mb-4 border-b border-border/50">
        {(
          [
            ["members", "Members"],
            ["invitations", "Invitations"],
            ["settings", "Settings"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setSubTab(value)}
            className={[
              "px-3 py-1.5 text-sm border-b-2 transition-colors",
              subTab === value
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            ].join(" ")}
            aria-current={subTab === value ? "page" : undefined}
          >
            {label}
          </button>
        ))}
      </div>

      {subTab === "members" && <MembersTab orgId={activeOrgId} />}
      {subTab === "invitations" && <InvitationsTab orgId={activeOrgId} />}
      {subTab === "settings" && <OrgSettingsTab orgId={activeOrgId} />}
    </div>
  );
}

export function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { activeOrg } = useOrgContext();
  const showOrgTab = activeOrg !== null && !activeOrg.is_personal;

  // Plan 10-06: deep-link support for /settings?tab=<name>. Hardens the
  // cancel-deletion email link from Plan 10-04 Task 3 — invalid values fall
  // back silently to "account" so a mangled URL never 404s the user.
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const initialTab = (VALID_SETTINGS_TABS as readonly string[]).includes(
    tabParam ?? "",
  )
    ? (tabParam as string)
    : "account";

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

      <Tabs defaultValue={initialTab}>
        <TabsList>
          <TabsTrigger value="account">Account</TabsTrigger>
          <TabsTrigger value="billing">Billing</TabsTrigger>
          <TabsTrigger value="privacy">Privacy</TabsTrigger>
          <TabsTrigger value="api-keys">API Keys</TabsTrigger>
          <TabsTrigger value="usage">Usage</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          {showOrgTab && (
            <TabsTrigger value="organization">Organization</TabsTrigger>
          )}
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

        <TabsContent value="privacy">
          {/*
            Plan 10-04 replaces the Plan 10-06 placeholder with the real
            Privacy controls — Export Data (GDPR Art. 20), Delete Account
            (GDPR Art. 17), and the pending-deletion banner + Cancel button
            when deletion_requested_at is non-null.
          */}
          <PrivacyTab initialSettings={settings} onChanged={loadSettings} />
        </TabsContent>

        <TabsContent value="api-keys">
          <ApiKeysTab />
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

        {showOrgTab && (
          <TabsContent value="organization">
            <OrganizationTab />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
