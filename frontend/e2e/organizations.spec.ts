import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { LoginPage } from "./pages/LoginPage";
import { FLAG_ON_API, FLAG_ON_URL, consentState } from "./stacks";

/**
 * Phase 12 E2E — teams and organizations, against a FLAG-ON backend.
 *
 * Every other spec in this directory runs against the flag-OFF stack
 * (frontend :5173 -> backend :8000), which is what proves the flag-off
 * landing keeps today's single-tenant behaviour. This spec is the only one
 * that needs ``settings.organizations_enabled = true``, so it runs in its own
 * Playwright project against a second stack (frontend :5174 -> backend
 * :8001). Both stacks are declared in playwright.config.ts (projects +
 * webServer) and started by the "E2E Tests" job in
 * .github/workflows/test.yml.
 *
 * Nothing here skips itself. Step 0 asserts the flag-on backend is reachable
 * and reports the flag on, so a missing or misconfigured stack fails with one
 * clear message instead of a green run that tested nothing.
 *
 * Requires, all provided by the CI job:
 *   - backend on :8001 with ORGANIZATIONS_ENABLED=true and CORS allowing
 *     http://localhost:5174
 *   - frontend on :5174 built with VITE_API_BASE=http://localhost:8001
 *   - Supabase local (migrations applied, so public.personal_org_for exists)
 *   - two seeded accounts, usera-e2e@example.com and userb-e2e@example.com,
 *     each with public.users.tos_version current so the re-acceptance modal
 *     does not cover the app
 *
 * Neither seeded account has a personal-org row: both are created by GoTrue
 * admin + a direct public.users upsert, never through /auth/signup. So this
 * spec also exercises the lazy personal-org path in
 * backend/auth/org_dependencies.py — an existing user whose personal org is
 * created on first org-scoped request.
 *
 * Run locally (both stacks up):
 *   cd frontend && npx playwright test --project=chromium-orgs
 *
 * Security note (T-09-04): only env-controlled *-e2e@example.com accounts are
 * referenced. Never point this spec at production — it mutates org state.
 */

// --- env-controlled test accounts + stack ---------------------------------

const USER_A_EMAIL = process.env.PHASE12_USER_A_EMAIL ?? "usera-e2e@example.com";
const USER_A_PW = process.env.PHASE12_USER_A_PW ?? "TestPassword123!";
const USER_B_EMAIL = process.env.PHASE12_USER_B_EMAIL ?? "userb-e2e@example.com";
const USER_B_PW = process.env.PHASE12_USER_B_PW ?? "TestPassword123!";

/** Flag-on backend, from the shared stack topology. */
const ORGS_API_BASE = FLAG_ON_API;

const ORG_NAME = `E2E Acme ${Date.now()}`;

/** localStorage key from frontend/src/components/org/OrganizationContext.tsx. */
const ORG_STORAGE_KEY = "kendrew.activeOrgId";

// --- helpers --------------------------------------------------------------

async function loginAs(page: Page, email: string, password: string) {
  await new LoginPage(page).login(email, password);
}

/** Read the stored active-org id. Tolerates a navigation in flight. */
async function readStoredOrgId(page: Page): Promise<string | null | undefined> {
  try {
    return await page.evaluate((key) => localStorage.getItem(key), ORG_STORAGE_KEY);
  } catch {
    // Execution context destroyed mid-read (setActiveOrg reloads the page).
    return undefined;
  }
}

/**
 * Wait for the stored active-org id to become something other than
 * ``previous``. CreateOrganization and AcceptInvitation both write the id and
 * then reload, so polling is the only reliable read.
 */
async function waitForNewStoredOrgId(
  page: Page,
  previous: string | null,
  timeoutMs = 20_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let last: string | null | undefined;
  while (Date.now() < deadline) {
    last = await readStoredOrgId(page);
    if (typeof last === "string" && last.length > 0 && last !== previous) {
      return last;
    }
    await page.waitForTimeout(250);
  }
  throw new Error(
    `active org id never changed (previous=${previous}, last=${String(last)})`,
  );
}

/** Put ``orgId`` in localStorage and reload, landing the page in that org. */
async function switchToOrgById(page: Page, orgId: string) {
  await page.goto("/jobs");
  await page.evaluate(
    ([key, id]) => localStorage.setItem(key, id),
    [ORG_STORAGE_KEY, orgId],
  );
  await page.reload();
}

/** Open /settings on the Organization tab, then the named sub-tab. */
async function openOrgSubTab(
  page: Page,
  subTab: "Members" | "Invitations" | "Settings",
) {
  await page.goto("/settings?tab=organization");
  // The tab only mounts once OrgProvider has resolved a non-personal active
  // org, so wait for the trigger rather than assuming the deep link painted.
  const trigger = page.getByRole("tab", { name: "Organization" });
  await expect(trigger).toBeVisible({ timeout: 15_000 });
  await trigger.click();
  await page.getByRole("button", { name: subTab, exact: true }).click();
}

/**
 * Pending invitation token for ``email``, read through the owner-only
 * GET /organizations/{id}/invitations?status=pending. page.request shares the
 * page's cookies, and the endpoint requires X-Org-Id to equal the path org id
 * (backend/organizations/router.py).
 */
async function fetchInviteToken(
  page: Page,
  orgId: string,
  email: string,
): Promise<string | null> {
  const res = await page.request.get(
    `${ORGS_API_BASE}/organizations/${orgId}/invitations?status=pending`,
    { headers: { "X-Org-Id": orgId } },
  );
  expect(res.status(), "owner invitation list should be readable").toBe(200);
  const body = (await res.json()) as {
    invitations: Array<{ email: string; token: string | null }>;
  };
  const match = body.invitations.find(
    (i) => i.email.toLowerCase() === email.toLowerCase(),
  );
  return match?.token ?? null;
}

// --- the spec -------------------------------------------------------------

test.describe.serial("Phase 12: full teams flow", () => {
  let teamOrgId: string | null = null;
  let inviteToken: string | null = null;
  let userBContext: BrowserContext | null = null;

  test("0. the flag-on backend is up and reports organizations_enabled", async ({
    request,
  }) => {
    const res = await request.get(`${ORGS_API_BASE}/health`);
    // /health answers 503 whenever any dependency is degraded and still
    // carries the flag, so assert the field, not the status code.
    const body = (await res.json()) as { organizations_enabled?: boolean };
    expect(
      body.organizations_enabled,
      `${ORGS_API_BASE} must run with ORGANIZATIONS_ENABLED=true; ` +
        `got ${JSON.stringify(body)}`,
    ).toBe(true);
  });

  test("1. User A creates a team org", async ({ page }) => {
    await loginAs(page, USER_A_EMAIL, USER_A_PW);

    const before = (await readStoredOrgId(page)) ?? null;

    await page.goto("/organizations/new");
    await page.fill("#org-name", ORG_NAME);
    await page.getByRole("button", { name: "Create organization" }).click();

    // createOrg -> refresh -> setActiveOrg writes localStorage and reloads;
    // the route itself does not change.
    teamOrgId = await waitForNewStoredOrgId(page, before);
    expect(teamOrgId).toMatch(/^[0-9a-f-]{36}$/);
  });

  test("2. User A invites User B and can read the invitation token", async ({
    page,
  }) => {
    expect(teamOrgId, "step 1 must have created the team org").not.toBeNull();
    await loginAs(page, USER_A_EMAIL, USER_A_PW);
    await switchToOrgById(page, teamOrgId!);

    await openOrgSubTab(page, "Members");

    const inviteForm = page.locator('form[aria-label="Invite member"]');
    await expect(inviteForm, "owner sees the invite form").toBeVisible({
      timeout: 15_000,
    });
    await page.fill("#invite-email", USER_B_EMAIL);
    await page.selectOption("#invite-role", "scientist");
    await inviteForm.getByRole("button", { name: "Send invitation" }).click();

    await expect(page.getByRole("status")).toContainText(
      `Invitation sent to ${USER_B_EMAIL}`,
      { timeout: 15_000 },
    );

    // The pending row shows up on the Invitations sub-tab.
    await page.getByRole("button", { name: "Invitations", exact: true }).click();
    await expect(page.getByText(USER_B_EMAIL)).toBeVisible({ timeout: 10_000 });

    inviteToken = await fetchInviteToken(page, teamOrgId!, USER_B_EMAIL);
    expect(inviteToken, "owner-only list returns the bearer token").toBeTruthy();
  });

  test("3. User B accepts the invitation", async ({ browser }) => {
    expect(inviteToken, "step 2 must have captured a token").toBeTruthy();

    // Fresh context = fresh cookies, so B is independent of A. newContext()
    // does not inherit the project's `use` options, so baseURL and the
    // pre-dismissed cookie consent have to be passed explicitly.
    userBContext = await browser.newContext({
      baseURL: FLAG_ON_URL,
      storageState: consentState(FLAG_ON_URL),
    });
    const page = await userBContext.newPage();

    await loginAs(page, USER_B_EMAIL, USER_B_PW);
    await page.goto(
      `/invitations/accept?token=${encodeURIComponent(inviteToken!)}`,
    );

    await expect(
      page.getByRole("heading", { name: `Join ${ORG_NAME}` }),
    ).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Accept invitation" }).click();

    await page.waitForURL(/\/jobs/, { timeout: 15_000 });
    expect(await readStoredOrgId(page)).toBe(teamOrgId);
  });

  test("4. User A sees User B in the members list", async ({ page }) => {
    await loginAs(page, USER_A_EMAIL, USER_A_PW);
    await switchToOrgById(page, teamOrgId!);
    await openOrgSubTab(page, "Members");

    await expect(page.getByText(USER_B_EMAIL)).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator(`select[aria-label="Role for ${USER_B_EMAIL}"]`),
    ).toHaveValue("scientist");
  });

  test("5. User B reads the team-org job list", async () => {
    expect(userBContext, "step 3 must have established user B").not.toBeNull();
    const page = await userBContext!.newPage();
    await switchToOrgById(page, teamOrgId!);

    // A fresh org has no jobs. What is under test is that the org-scoped read
    // succeeds for a member: JobHistoryPage renders this empty state only on
    // `!loading && !error`, so a 403 or 500 on GET /jobs shows the error text
    // instead and this assertion fails.
    await expect(page.getByText("No jobs yet")).toBeVisible({ timeout: 15_000 });
  });

  test("6. User B sees the non-owner billing gate", async () => {
    const page = await userBContext!.newPage();
    await switchToOrgById(page, teamOrgId!);
    await page.goto("/settings?tab=billing");

    await expect(
      page.getByText("Billing is managed by your organization owner."),
    ).toBeVisible({ timeout: 15_000 });
    // The gate names the owner so the member knows who to ask.
    await expect(page.getByText(USER_A_EMAIL)).toBeVisible();
  });

  test("7. User A is not gated out of billing as owner", async ({ page }) => {
    await loginAs(page, USER_A_EMAIL, USER_A_PW);
    await switchToOrgById(page, teamOrgId!);
    await page.goto("/settings?tab=billing");

    // CI has no Stripe keys, so the owner view legitimately renders either
    // billing content or an error — the assertion is that it is never the
    // non-owner gate.
    await expect(
      page.getByText("Billing is managed by your organization owner."),
    ).toHaveCount(0, { timeout: 15_000 });
  });

  test("8. the last owner cannot demote themselves", async ({ page }) => {
    await loginAs(page, USER_A_EMAIL, USER_A_PW);
    await switchToOrgById(page, teamOrgId!);
    await openOrgSubTab(page, "Members");

    const selfRole = page.locator(
      `select[aria-label="Role for ${USER_A_EMAIL}"]`,
    );
    await expect(selfRole).toBeVisible({ timeout: 15_000 });
    await selfRole.selectOption("scientist");

    // protect_last_owner raises check_violation; the backend maps it to 400
    // and MembersTab surfaces the detail in a role="alert" banner.
    await expect(page.getByRole("alert")).toContainText(/last owner/i, {
      timeout: 15_000,
    });
  });

  test("9. User A transfers ownership to User B", async ({ page }) => {
    await loginAs(page, USER_A_EMAIL, USER_A_PW);
    await switchToOrgById(page, teamOrgId!);
    await openOrgSubTab(page, "Members");

    await page
      .getByRole("button", { name: "Transfer ownership", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.locator("#transfer-target").selectOption({ label: USER_B_EMAIL });
    await dialog.locator("#transfer-new-self-role").selectOption("scientist");
    await dialog
      .getByRole("button", { name: "Transfer ownership", exact: true })
      .click();

    // handleTransfer reloads on success. A is a scientist afterwards, so the
    // owner-only affordances are gone.
    await expect(
      page.locator('form[aria-label="Invite member"]'),
    ).toHaveCount(0, { timeout: 20_000 });
    await expect(
      page.locator(`select[aria-label="Role for ${USER_A_EMAIL}"]`),
    ).toHaveCount(0);
    // Non-owners see their role as plain text, so A's own row now reads
    // scientist (MembersTab renders <span>{m.role}</span> off isOwner).
    await expect(
      page.getByRole("row").filter({ hasText: USER_A_EMAIL }),
    ).toContainText("scientist");
  });

  test("10. User B is the owner and reaches billing", async () => {
    const page = await userBContext!.newPage();
    await switchToOrgById(page, teamOrgId!);
    await page.goto("/settings?tab=billing");

    await expect(
      page.getByText("Billing is managed by your organization owner."),
    ).toHaveCount(0, { timeout: 15_000 });
    // Owner-only affordance, and B is now the owner.
    await openOrgSubTab(page, "Members");
    await expect(page.locator('form[aria-label="Invite member"]')).toBeVisible({
      timeout: 15_000,
    });
  });

  test.afterAll(async () => {
    // The org row is left behind under the timestamped name `E2E Acme <ts>` so
    // re-runs never collide; purge with
    // DELETE FROM public.organizations WHERE name LIKE 'E2E Acme %' if the
    // local database gets noisy.
    await userBContext?.close();
  });
});
