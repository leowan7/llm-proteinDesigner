/**
 * AcceptInvitation — /invitations/accept?token=... landing page.
 *
 * Renders one of four branches (RESEARCH §6.2):
 *
 *   1. signed in + email matches the invitation → "Join {Org} as {role}?"
 *      with an Accept button. Acceptance refreshes OrgContext and switches
 *      the active org to the newly-joined one.
 *
 *   2. signed in + email mismatch → "This invitation is for {invited email}".
 *      Sign Out button signs the current user out and redirects to login
 *      with the token preserved.
 *
 *   3. signed out + token valid → "Sign in as {invited email}" with Sign-in
 *      and Create-account buttons that both carry the token through.
 *
 *   4. invalid token → human-readable error message for each backend reason
 *      (expired, revoked, already_accepted, not_found).
 *
 * /invitations/preview and /invitations/accept are both on the api.ts
 * X-Org-Id opt-out list, so they work whether or not the user has a current
 * active org.
 *
 * /auth/me also has no active-org dependency (in the opt-out list as /auth/*).
 */

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useOrgContext } from "@/components/org/OrganizationContext";
import { api, ApiError } from "@/lib/api";
import {
  acceptInvitation,
  previewInvitation,
  type PreviewResult,
} from "@/lib/organizations";

interface MeResponse {
  user_id: string;
  email: string;
}

/**
 * Maps the backend's "reason" field to a user-friendly message.
 * Covers all four documented invalid reasons: expired, revoked,
 * already_accepted, not_found.
 */
function invalidReasonMessage(preview: PreviewResult): string {
  switch (preview.reason) {
    case "expired":
      return "This invitation has expired. Ask the organization owner to send a new one.";
    case "revoked":
      return "This invitation was revoked by the organization owner.";
    case "already_accepted":
      return "This invitation has already been accepted. Sign in to access the organization.";
    case "not_found":
    default:
      return "Invitation not found. The link may be invalid or mistyped.";
  }
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <main className="max-w-md mx-auto px-6 py-12 space-y-4">{children}</main>
  );
}

export function AcceptInvitation() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();
  // /invitations/accept is registered outside the authenticated layout, so
  // useOrgContext() returns the empty fallback here. After accept, we still
  // call orgCtx.refresh() + orgCtx.setActiveOrg() — they no-op in the
  // fallback case but work correctly when the user already had an active
  // org context from a previous authenticated session in the same tab.
  const orgCtx = useOrgContext();

  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [me, setMe] = useState<MeResponse | null | "loading">("loading");
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setError("Missing invitation token. Open the link from your invitation email.");
      setMe(null);
      return () => {};
    }

    previewInvitation(token)
      .then((p) => {
        if (!cancelled) setPreview(p);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Unable to load invitation.",
          );
        }
      });

    api<MeResponse>("/auth/me")
      .then((u) => {
        if (!cancelled) setMe(u);
      })
      .catch((err) => {
        // 401 means "not signed in" — a documented branch, not an error.
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 401) {
            setMe(null);
          } else {
            setMe(null);
          }
        }
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  if (error) {
    return (
      <Frame>
        <h1 className="font-display text-2xl">Invitation</h1>
        <p className="text-sm text-destructive">{error}</p>
      </Frame>
    );
  }

  if (!preview || me === "loading") {
    return (
      <Frame>
        <p className="text-sm text-muted-foreground">Loading invitation...</p>
      </Frame>
    );
  }

  // Branch 4: invalid (expired / revoked / already_accepted / not_found).
  if (!preview.valid) {
    return (
      <Frame>
        <h1 className="font-display text-2xl">Invitation unavailable</h1>
        <p className="text-sm text-muted-foreground">
          {invalidReasonMessage(preview)}
        </p>
        <Button variant="outline" onClick={() => navigate("/login")}>
          Sign in
        </Button>
      </Frame>
    );
  }

  // Branch 3: signed out + token valid -> route to login (or signup) with
  // the token carried through. Login.tsx and SignUp.tsx preserve query
  // params on submit, so after auth they will land back on
  // /invitations/accept and re-run preview as the signed-in user.
  if (me === null) {
    return (
      <Frame>
        <h1 className="font-display text-2xl">
          You've been invited to {preview.organization_name}
        </h1>
        <p className="text-sm">
          Role: <strong>{preview.role}</strong>
        </p>
        <p className="text-sm text-muted-foreground">
          Sign in as <strong>{preview.email}</strong> to accept this
          invitation.
        </p>
        <div className="flex items-center gap-2 pt-2">
          <Button
            onClick={() =>
              navigate(
                `/login?invite_token=${encodeURIComponent(
                  token,
                )}&next=${encodeURIComponent("/invitations/accept?token=" + token)}`,
              )
            }
          >
            Sign in
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              navigate(
                `/signup?invite_token=${encodeURIComponent(
                  token,
                )}&email=${encodeURIComponent(preview.email ?? "")}`,
              )
            }
          >
            Create account
          </Button>
        </div>
      </Frame>
    );
  }

  // Branch 2: signed in + email mismatch -> sign-out CTA.
  if (
    preview.email &&
    me.email.toLowerCase() !== preview.email.toLowerCase()
  ) {
    return (
      <Frame>
        <h1 className="font-display text-2xl">Wrong account</h1>
        <p className="text-sm">
          This invitation is for <strong>{preview.email}</strong>.
        </p>
        <p className="text-sm text-muted-foreground">
          You're currently signed in as <strong>{me.email}</strong>. Sign out
          and sign in as the invited email to accept.
        </p>
        <Button
          onClick={async () => {
            try {
              await api("/auth/logout", { method: "POST" });
            } catch {
              // Best-effort sign out; still bounce to login.
            }
            navigate(
              `/login?invite_token=${encodeURIComponent(
                token,
              )}&next=${encodeURIComponent("/invitations/accept?token=" + token)}`,
            );
          }}
        >
          Sign out and sign in as {preview.email}
        </Button>
      </Frame>
    );
  }

  // Branch 1: signed in + email matches -> accept CTA.
  if (accepted) {
    return (
      <Frame>
        <p className="text-sm text-muted-foreground">
          You've joined {preview.organization_name}. Loading...
        </p>
      </Frame>
    );
  }

  return (
    <Frame>
      <h1 className="font-display text-2xl">Join {preview.organization_name}</h1>
      <p className="text-sm">
        You've been invited as <strong>{preview.role}</strong>.
      </p>
      <div className="flex items-center gap-2 pt-2">
        <Button
          disabled={submitting}
          onClick={async () => {
            setSubmitting(true);
            try {
              const result = await acceptInvitation(token);
              // Pre-seed localStorage so the post-accept paint lands on the
              // new org. We do this BEFORE calling setActiveOrg so the fallback
              // (no provider mounted) still leaves a fresh value behind.
              try {
                localStorage.setItem(
                  "kendrew.activeOrgId",
                  result.organization_id,
                );
              } catch {
                // localStorage unavailable — best effort.
              }
              await orgCtx.refresh();
              orgCtx.setActiveOrg(result.organization_id);
              setAccepted(true);
              // If setActiveOrg's reload no-ops (fallback), navigate manually
              // so the user lands inside the authenticated layout.
              navigate("/jobs");
            } catch (err) {
              setError(
                err instanceof Error
                  ? err.message
                  : "Failed to accept invitation.",
              );
              setSubmitting(false);
            }
          }}
        >
          {submitting ? "Joining..." : "Accept invitation"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => navigate("/jobs")}
          disabled={submitting}
        >
          Cancel
        </Button>
      </div>
    </Frame>
  );
}

export default AcceptInvitation;
