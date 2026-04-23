/**
 * Legal / compliance client helpers (Plan 10-02).
 *
 * - Re-exports the canonical TOS_VERSION from versions.ts so call sites can
 *   `import { TOS_VERSION } from "@/lib/legal"` alongside the other helpers.
 * - `needsReAcceptance` compares a user's stored tos_version against the
 *   current backend tos_current and decides whether to show the blocking
 *   re-acceptance modal.
 * - `acceptTos` posts to /user/accept-tos; the backend writes
 *   tos_accepted_at = now() and tos_version = settings.tos_current_version.
 */

import { api } from "./api";
import { TOS_VERSION } from "@/pages/legal/versions";

export { TOS_VERSION };

/** True when the user's accepted tos_version drifts from the backend's tos_current. */
export function needsReAcceptance(
  userTosVersion: string | null | undefined,
  tosCurrent: string | null | undefined,
): boolean {
  // If the backend did not return a current version, do not prompt — this
  // avoids false positives on older backends.
  if (!tosCurrent) return false;
  // User has never accepted (null / undefined) OR accepted an older version.
  return userTosVersion !== tosCurrent;
}

/** Record re-acceptance for the authenticated user. */
export async function acceptTos(): Promise<void> {
  await api("/user/accept-tos", { method: "POST" });
}
