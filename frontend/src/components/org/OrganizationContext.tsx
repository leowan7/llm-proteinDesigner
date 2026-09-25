/* eslint-disable react-refresh/only-export-components -- ships a context provider + hook from the same module by design */
/**
 * OrganizationContext — active-org state for the authenticated app.
 *
 * Lives once at the root of the authenticated route tree (AuthenticatedLayout
 * mounts <OrgProvider> around <Outlet />). Responsibilities:
 *
 *   1. On mount, fetch GET /organizations/mine to learn which orgs the user
 *      belongs to, plus their role per org.
 *   2. Resolve the active org id:
 *        - Try localStorage["kendrew.activeOrgId"] first.
 *        - If missing/invalid, default to the user's personal org
 *          (is_personal=true). If somehow absent, fall back to orgs[0].
 *   3. Expose the orgs list, the active org, the user's role in the active
 *      org, and helpers to refresh after mutations + switch active org.
 *   4. setActiveOrg() writes localStorage and triggers a full reload so all
 *      in-flight queries + SSE streams re-fetch under the new org's scope.
 *   5. Flag-off landing: organizationsEnabled() probes /health first. When
 *      the backend reports organizations_enabled=false the provider skips
 *      /organizations/mine entirely, exposes enabled=false with orgs=[], and
 *      clears any active-org id left in localStorage by an earlier flag-on
 *      session, so no request carries a stale X-Org-Id header. Every org UI
 *      surface reads `enabled` and renders the single-tenant view when it is
 *      false. A 4xx from /organizations/mine with the flag on degrades the
 *      same way (orgs=[]) rather than blocking render.
 *   6. Logout helper clearActiveOrgOnLogout() removes the stored id so a
 *      different user signing in on the same browser starts fresh.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { organizationsEnabled } from "@/lib/features";
import {
  fetchMyOrgs,
  type OrgResponse,
  type OrgRole,
} from "@/lib/organizations";

const STORAGE_KEY = "kendrew.activeOrgId";

interface OrgContextValue {
  /**
   * True only when the backend reports organizations_enabled=true. Every org
   * UI surface gates on this so a flag-off deploy renders as single-tenant.
   */
  enabled: boolean;
  /** All orgs the current user is a member of. Empty when feature flag off. */
  orgs: OrgResponse[];
  /** The id of the active org, or null when no orgs / feature off. */
  activeOrgId: string | null;
  /** The active org record, or null. */
  activeOrg: OrgResponse | null;
  /** The user's role in the active org, or null. */
  role: OrgRole | null;
  /** True while the initial /organizations/mine fetch is outstanding. */
  loading: boolean;
  /** Re-fetch /organizations/mine; call after mutations (create/accept). */
  refresh: () => Promise<void>;
  /**
   * Switch to a different active org. Writes localStorage and reloads the
   * page so all in-flight queries + SSE streams re-fetch under the new org.
   */
  setActiveOrg: (orgId: string) => void;
}

const OrgContext = createContext<OrgContextValue | null>(null);

/**
 * Reads the active-org id stored in localStorage. Safe under jsdom + private
 * browsing.
 */
function readStoredActiveOrgId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Writes the active-org id to localStorage if available. */
function writeStoredActiveOrgId(value: string | null): void {
  try {
    if (value === null) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, value);
    }
  } catch {
    // localStorage unavailable — proceed without persistence.
  }
}

/**
 * Resolves the active org id from a fresh orgs list:
 *   1. If the stored id is in the list, use it.
 *   2. Otherwise pick the personal org (is_personal=true).
 *   3. Otherwise the first org.
 *   4. Otherwise null.
 */
function resolveActiveOrgId(
  list: OrgResponse[],
  stored: string | null,
): string | null {
  if (stored && list.some((o) => o.id === stored)) return stored;
  const personal = list.find((o) => o.is_personal);
  if (personal) return personal.id;
  if (list[0]) return list[0].id;
  return null;
}

export function OrgProvider({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabled] = useState(false);
  const [orgs, setOrgs] = useState<OrgResponse[]>([]);
  const [activeOrgId, setActiveOrgIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const flagOn = await organizationsEnabled();
      setEnabled(flagOn);
      let list: OrgResponse[] = [];
      if (flagOn) {
        try {
          list = await fetchMyOrgs();
        } catch (err) {
          // Flag on but the call failed. Leave orgs empty so the rest of the
          // app keeps working as a single user. We log for visibility; we do
          // not bubble the error.
          console.warn("Organizations feature unavailable:", err);
          list = [];
        }
      }
      // With an empty list resolveActiveOrgId() returns null, which clears any
      // stored id below — that is what keeps a flag-off deploy from sending a
      // stale X-Org-Id header.
      setOrgs(list);
      const stored = readStoredActiveOrgId();
      const resolved = resolveActiveOrgId(list, stored);
      if (resolved !== stored) {
        writeStoredActiveOrgId(resolved);
      }
      setActiveOrgIdState(resolved);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh().catch((err) => console.error("OrgProvider initial fetch failed:", err));
  }, [refresh]);

  const setActiveOrg = useCallback((orgId: string) => {
    writeStoredActiveOrgId(orgId);
    // Full reload so all in-flight queries + SSE streams re-fetch under the
    // new org's X-Org-Id scope.
    window.location.reload();
  }, []);

  const activeOrg = orgs.find((o) => o.id === activeOrgId) ?? null;
  const role = activeOrg?.role ?? null;

  const value: OrgContextValue = {
    enabled,
    orgs,
    activeOrgId,
    activeOrg,
    role,
    loading,
    refresh,
    setActiveOrg,
  };

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>;
}

/**
 * Empty fallback returned by useOrgContext() when no <OrgProvider> is in the
 * tree. Covers:
 *
 *   - Pages reached before the authenticated layout mounts (e.g. logout race)
 *   - Vitest scaffolds that render a page directly under <MemoryRouter>
 *     without the full layout chain (the Phase 9 + Phase 10 test pattern)
 *   - Single-tenant deployments where the feature flag is off and
 *     fetchMyOrgs() returns nothing (handled by the provider too, but a hook
 *     consumed outside the provider should still degrade gracefully)
 *
 * Components that render inside this fallback see enabled=false and orgs=[],
 * so every gating decision skips its org-specific branch. This is the
 * pre-Plan-12-05 behavior for a solo user.
 */
const FALLBACK_ORG_CONTEXT: OrgContextValue = {
  enabled: false,
  orgs: [],
  activeOrgId: null,
  activeOrg: null,
  role: null,
  loading: false,
  refresh: async () => {},
  setActiveOrg: () => {},
};

/**
 * Hook returning the active-org context. Returns a safe empty fallback when
 * no <OrgProvider> is mounted (single-tenant + test-scaffold compatibility).
 */
export function useOrgContext(): OrgContextValue {
  const ctx = useContext(OrgContext);
  return ctx ?? FALLBACK_ORG_CONTEXT;
}

/**
 * Removes the active-org id from localStorage. Call from logout handlers so a
 * different identity signing in on the same browser starts with a fresh
 * resolution.
 */
export function clearActiveOrgOnLogout(): void {
  writeStoredActiveOrgId(null);
}
