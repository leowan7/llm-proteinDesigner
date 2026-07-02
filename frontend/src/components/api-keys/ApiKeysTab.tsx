/* eslint-disable react-hooks/set-state-in-effect -- tab-level data fetch on mount is intentional */
/**
 * ApiKeysTab — Settings → API Keys tab body (Plan 13-06).
 *
 * Lists the caller's active-org API keys (non-revoked), surfaces Create +
 * Revoke actions, and visually highlights idle keys — those never used or
 * last used more than 30 days ago — with an amber "Unused" badge (D-04).
 *
 * Wired into SettingsPage as the tab between Privacy and Usage.
 */

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { listApiKeys, type ApiKey, type CreatedApiKey } from "@/lib/api-keys";
import { CreateApiKeyModal } from "./CreateApiKeyModal";
import { RevokeConfirmModal } from "./RevokeConfirmModal";

const IDLE_THRESHOLD_MS = 30 * 24 * 3600 * 1000;

/** ISO → "April 1, 2026"; empty string on null/invalid input. */
function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

/**
 * A key is idle when it has never been used, or its last use was more than
 * 30 days ago (D-04). Idle keys are candidates for revocation.
 */
export function isIdle(lastUsedAt: string | null): boolean {
  if (lastUsedAt === null) return true;
  const last = new Date(lastUsedAt).getTime();
  if (Number.isNaN(last)) return true;
  return Date.now() - last > IDLE_THRESHOLD_MS;
}

/** Whole-days idle count for the badge copy ("Unused 31d"). */
function idleDays(lastUsedAt: string | null): number | null {
  if (lastUsedAt === null) return null;
  const last = new Date(lastUsedAt).getTime();
  if (Number.isNaN(last)) return null;
  return Math.floor((Date.now() - last) / (24 * 3600 * 1000));
}

export function ApiKeysTab() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeKey, setRevokeKey] = useState<ApiKey | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const data = await listApiKeys();
      setKeys(data);
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : "Unable to load API keys. Refresh the page to try again.";
      setError(msg);
      setKeys([]);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  function handleCreated(_key: CreatedApiKey) {
    setCreateOpen(false);
    refetch();
  }

  function handleRevoked() {
    setRevokeKey(null);
    refetch();
  }

  const hasKeys = keys !== null && keys.length > 0;

  return (
    <div className="space-y-6 pt-4">
      <header className="flex items-center justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-base font-semibold text-foreground">API Keys</h2>
          <p className="text-sm text-muted-foreground">
            Programmatic access to Bindwave. Treat keys like passwords — anyone
            with a key can run and bill jobs on your organization.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)} className="shrink-0">
          Create new key
        </Button>
      </header>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {keys === null && !error ? (
        <div className="space-y-2">
          <div className="h-4 w-48 animate-pulse rounded bg-muted" />
          <div className="h-4 w-32 animate-pulse rounded bg-muted" />
        </div>
      ) : hasKeys ? (
        <div className="overflow-hidden rounded-md border border-border/50">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 bg-card">
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-medium text-muted-foreground"
                >
                  Name
                </th>
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-medium text-muted-foreground"
                >
                  Prefix
                </th>
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-medium text-muted-foreground"
                >
                  Created
                </th>
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-medium text-muted-foreground"
                >
                  Last used
                </th>
                <th
                  scope="col"
                  className="px-4 py-2 text-right text-xs font-medium text-muted-foreground"
                >
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {keys!.map((key) => {
                const idle = isIdle(key.last_used_at);
                const days = idleDays(key.last_used_at);
                return (
                  <tr
                    key={key.id}
                    className="border-b border-border/50 last:border-0"
                  >
                    <td className="px-4 py-2 text-foreground">{key.name}</td>
                    <td className="px-4 py-2 font-mono text-muted-foreground">
                      {key.prefix}…
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {formatDate(key.created_at)}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      <span className="inline-flex items-center gap-2">
                        {key.last_used_at
                          ? formatDate(key.last_used_at)
                          : "Never"}
                        {idle && (
                          <Badge
                            variant="outline"
                            className="border-amber-500/40 bg-amber-500/10 text-amber-500"
                          >
                            {days === null ? "Unused" : `Unused ${days}d`}
                          </Badge>
                        )}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setRevokeKey(key)}
                      >
                        Revoke
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-md border border-border/50 bg-card p-6 text-center">
          <p className="text-sm text-foreground">No API keys yet.</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Create a key to call Bindwave from scripts, CI, or the SDK.
          </p>
        </div>
      )}

      <CreateApiKeyModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={handleCreated}
      />

      <RevokeConfirmModal
        apiKey={revokeKey}
        onOpenChange={(next) => {
          if (!next) setRevokeKey(null);
        }}
        onRevoked={handleRevoked}
      />
    </div>
  );
}
