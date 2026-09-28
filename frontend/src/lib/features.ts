/**
 * Runtime feature flags, read from the backend's public /health payload.
 *
 * Why a runtime probe: Vercel deploys `master` on push, so this bundle ships
 * to production the moment Phase 12 merges, while ORGANIZATIONS_ENABLED
 * stays off on Railway until the rollout runbook flips it
 * (docs/runbook-phase-12-rollout.md). Every organization surface in the app
 * gates on this flag, so a flag-off deploy renders the single-tenant UI.
 *
 * Fails closed: a fetch error, a non-JSON body or a missing field all yield
 * false, which hides the org UI. /health answers 503 when a dependency is
 * degraded and still carries the flag (backend/main.py:172-177), so the body
 * is parsed regardless of HTTP status.
 *
 * The probe runs at most once per page load; callers share the promise.
 */

import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

/** In-flight or settled probe, shared by every caller. */
let probe: Promise<boolean> | null = null;

/** Settled probe result, or null while the probe is unresolved. */
let resolved: boolean | null = null;

async function runProbe(): Promise<boolean> {
  let enabled = false;
  try {
    const response = await fetch(`${API_BASE}/health`);
    const data: unknown = await response.json();
    enabled =
      (data as { organizations_enabled?: unknown } | null)
        ?.organizations_enabled === true;
  } catch {
    // Backend unreachable or body not JSON — fail closed.
    enabled = false;
  }
  resolved = enabled;
  return enabled;
}

/**
 * Resolves true only when the backend reports organizations_enabled=true.
 * Subsequent calls return the same promise.
 */
export function organizationsEnabled(): Promise<boolean> {
  if (probe === null) {
    probe = runProbe();
  }
  return probe;
}

/**
 * Synchronous read of the probe result for code that cannot await, notably
 * api()'s X-Org-Id attachment. Returns null until the probe settles; callers
 * treat null as "unknown" and keep their pre-Phase-12 behaviour.
 */
export function organizationsEnabledSync(): boolean | null {
  return resolved;
}

/**
 * Hook form, for surfaces mounted outside <OrgProvider> (the public
 * /invitations/accept route). Returns null until the probe settles; render a
 * loading state then, never an org surface.
 */
export function useOrganizationsEnabled(): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(
    organizationsEnabledSync(),
  );

  useEffect(() => {
    let cancelled = false;
    organizationsEnabled().then((value) => {
      if (!cancelled) setEnabled(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return enabled;
}
