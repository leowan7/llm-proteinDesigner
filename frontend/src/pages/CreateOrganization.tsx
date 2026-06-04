/**
 * CreateOrganization — standalone /organizations/new page.
 *
 * Owner-self path for spinning up a new team org:
 *   1. Single "Organization name" input (max 100 chars).
 *   2. On submit, POST /organizations.
 *   3. On success: refresh() the OrgContext, then setActiveOrg(newOrgId)
 *      which writes localStorage and reloads into the new org's scope.
 *      The reload lands the user on /settings?tab=organization where they
 *      can immediately invite teammates.
 *
 * Failure modes:
 *   - Empty name: submit disabled.
 *   - 409 (duplicate name) and other 4xx: error banner with the backend
 *     detail string.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useOrgContext } from "@/components/org/OrganizationContext";
import { createOrg } from "@/lib/organizations";

export function CreateOrganization() {
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { refresh, setActiveOrg } = useOrgContext();
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;

    setSubmitting(true);
    setError(null);
    try {
      const org = await createOrg(trimmed);
      // Refresh org list, then switch active — the switch triggers reload.
      await refresh();
      // Pre-seed the localStorage entry so /settings?tab=organization resolves
      // to the new org on the post-reload paint.
      setActiveOrg(org.id);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to create organization.";
      setError(message);
      setSubmitting(false);
    }
  }

  return (
    <main className="max-w-md mx-auto px-6 py-12">
      <h1 className="font-display text-2xl mb-2">Create organization</h1>
      <p className="text-sm text-muted-foreground mb-6">
        Invite teammates and share job history, billing, and results across
        your organization.
      </p>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="org-name">Organization name</Label>
          <Input
            id="org-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            required
            autoFocus
            aria-describedby={error ? "create-org-error" : undefined}
          />
        </div>

        {error && (
          <p
            id="create-org-error"
            role="alert"
            className="text-sm text-destructive"
          >
            {error}
          </p>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button type="submit" disabled={submitting || !name.trim()}>
            {submitting ? "Creating..." : "Create organization"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => navigate(-1)}
            disabled={submitting}
          >
            Cancel
          </Button>
        </div>
      </form>
    </main>
  );
}

export default CreateOrganization;
