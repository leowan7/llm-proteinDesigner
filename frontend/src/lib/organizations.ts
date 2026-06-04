/**
 * Organizations API client.
 *
 * Typed wrappers for the /organizations/* and /invitations/* endpoints
 * introduced in Phase 12. Every authenticated org call relies on the
 * X-Org-Id header which is injected by frontend/src/lib/api.ts from the
 * "kendrew.activeOrgId" localStorage key.
 *
 * Endpoints with no active-org context (GET /organizations/mine,
 * POST /organizations, POST /invitations/accept, GET /invitations/preview)
 * are on the X-Org-Id opt-out list in api.ts.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type OrgRole = "owner" | "scientist" | "viewer";

export interface OrgResponse {
  id: string;
  name: string;
  role: OrgRole;
  is_personal: boolean;
}

export interface MemberRow {
  user_id: string;
  email: string;
  role: OrgRole;
  created_at: string;
}

export interface InvitationRow {
  id: string;
  email: string;
  role: OrgRole;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export type InvitationInvalidReason =
  | "not_found"
  | "revoked"
  | "expired"
  | "already_accepted";

export interface PreviewResult {
  valid: boolean;
  reason?: InvitationInvalidReason;
  organization_name?: string;
  role?: OrgRole;
  email?: string;
}

// ---------------------------------------------------------------------------
// Organization endpoints
// ---------------------------------------------------------------------------

/** GET /organizations/mine — list memberships for the current user. */
export async function fetchMyOrgs(): Promise<OrgResponse[]> {
  const data = await api<{ orgs: OrgResponse[] }>("/organizations/mine");
  return data.orgs;
}

/** POST /organizations — create a new team org owned by the current user. */
export async function createOrg(name: string): Promise<OrgResponse> {
  return api<OrgResponse>("/organizations", {
    method: "POST",
    body: { name },
  });
}

/** PATCH /organizations/{id} — rename an organization. */
export async function renameOrg(orgId: string, name: string): Promise<OrgResponse> {
  return api<OrgResponse>(`/organizations/${orgId}`, {
    method: "PATCH",
    body: { name },
  });
}

/** DELETE /organizations/{id} — delete an organization (owner only). */
export async function deleteOrg(orgId: string): Promise<void> {
  await api(`/organizations/${orgId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Member endpoints
// ---------------------------------------------------------------------------

/** GET /organizations/{id}/members — list members of an org. */
export async function fetchMembers(orgId: string): Promise<MemberRow[]> {
  const data = await api<{ members: MemberRow[] }>(
    `/organizations/${orgId}/members`,
  );
  return data.members;
}

/** PATCH /organizations/{id}/members/{user_id} — change a member's role. */
export async function updateMemberRole(
  orgId: string,
  userId: string,
  role: OrgRole,
): Promise<void> {
  await api(`/organizations/${orgId}/members/${userId}`, {
    method: "PATCH",
    body: { role },
  });
}

/** DELETE /organizations/{id}/members/{user_id} — remove a member. */
export async function removeMember(orgId: string, userId: string): Promise<void> {
  await api(`/organizations/${orgId}/members/${userId}`, { method: "DELETE" });
}

/** POST /organizations/{id}/members/transfer — transfer ownership. */
export async function transferOwnership(
  orgId: string,
  targetUserId: string,
  newSelfRole: "scientist" | "viewer",
): Promise<void> {
  await api(`/organizations/${orgId}/members/transfer`, {
    method: "POST",
    body: { target_user_id: targetUserId, new_self_role: newSelfRole },
  });
}

// ---------------------------------------------------------------------------
// Invitation endpoints
// ---------------------------------------------------------------------------

/** POST /organizations/{id}/invitations — invite a new member by email. */
export async function inviteMember(
  orgId: string,
  email: string,
  role: OrgRole,
): Promise<{ id: string }> {
  return api(`/organizations/${orgId}/invitations`, {
    method: "POST",
    body: { email, role },
  });
}

/** GET /organizations/{id}/invitations?status=pending */
export async function fetchPendingInvitations(
  orgId: string,
): Promise<InvitationRow[]> {
  const data = await api<{ invitations: InvitationRow[] }>(
    `/organizations/${orgId}/invitations?status=pending`,
  );
  return data.invitations;
}

/** DELETE /organizations/{id}/invitations/{invite_id} — revoke a pending invitation. */
export async function revokeInvitation(
  orgId: string,
  inviteId: string,
): Promise<void> {
  await api(`/organizations/${orgId}/invitations/${inviteId}`, {
    method: "DELETE",
  });
}

/** GET /invitations/preview?token=... — preview without consuming the token. */
export async function previewInvitation(token: string): Promise<PreviewResult> {
  return api<PreviewResult>(
    `/invitations/preview?token=${encodeURIComponent(token)}`,
  );
}

/** POST /invitations/accept — redeem an invitation token. */
export async function acceptInvitation(
  token: string,
): Promise<{ organization_id: string; role: OrgRole }> {
  return api("/invitations/accept", {
    method: "POST",
    body: { token },
  });
}
