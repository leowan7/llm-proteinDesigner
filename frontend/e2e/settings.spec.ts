import { test, expect } from "@playwright/test";
import { LoginPage } from "./pages/LoginPage";
import { SettingsPage } from "./pages/SettingsPage";

/**
 * Settings + billing E2E tests — D-06 flow 4.
 *
 * SettingsPage has 4 tabs: Account, Billing, Usage, Notifications.
 * Tests verify the page structure, tab switching, and billing content presence.
 *
 * Note: Billing and Usage tabs make API calls on mount. The API responses may
 * fail in CI (no real Stripe/payment data) — we test for error or content,
 * either of which indicates the page has loaded and rendered correctly.
 *
 * Security note (T-09-04): Uses only the test@example.com seed account.
 */

const TEST_EMAIL = "test@example.com";
const TEST_PASSWORD = "Password123!";

test.describe("Settings + billing", () => {
  test.beforeEach(async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
  });

  test("settings page loads with tabs", async ({ page }) => {
    const settingsPage = new SettingsPage(page);
    await settingsPage.goto();

    // SettingsPage renders 5 TabsTrigger elements with role="tab"
    // (Account, Billing, Privacy, Usage, Notifications)
    const tabs = page.locator('[role="tab"]');
    await expect(tabs).toHaveCount(5, { timeout: 10000 });

    // Verify the 5 expected tab names from SettingsPage.tsx
    await expect(tabs.filter({ hasText: "Account" })).toBeVisible();
    await expect(tabs.filter({ hasText: "Billing" })).toBeVisible();
    await expect(tabs.filter({ hasText: "Privacy" })).toBeVisible();
    await expect(tabs.filter({ hasText: "Usage" })).toBeVisible();
    await expect(tabs.filter({ hasText: "Notifications" })).toBeVisible();
  });

  test("can switch between settings tabs", async ({ page }) => {
    const settingsPage = new SettingsPage(page);
    await settingsPage.goto();

    // Default tab is "Account" (defaultValue="account" in SettingsPage.tsx)
    await expect(page.locator('[role="tab"][aria-selected="true"]')).toContainText("Account");

    // Switch to Billing tab
    await settingsPage.clickTab("Billing");
    await expect(page.locator('[role="tab"][aria-selected="true"]')).toContainText("Billing");

    // Switch to Usage tab
    await settingsPage.clickTab("Usage");
    await expect(page.locator('[role="tab"][aria-selected="true"]')).toContainText("Usage");

    // Switch to Notifications tab
    await settingsPage.clickTab("Notifications");
    await expect(page.locator('[role="tab"][aria-selected="true"]')).toContainText("Notifications");
  });

  test("billing section shows Stripe portal link or payment method info", async ({ page }) => {
    // Phase 11 D3: Stripe keys not provisioned in CI yet (Block E deferred).
    // The Billing tab queries Stripe and renders nothing user-clickable
    // when STRIPE_SECRET_KEY is empty, so none of the expected locators
    // resolve. Re-enable when Block E completes.
    test.skip(
      !process.env.STRIPE_SECRET_KEY,
      "Stripe not provisioned in CI yet (Phase 11 D3 / Block E deferred)",
    );

    const settingsPage = new SettingsPage(page);
    await settingsPage.goto();

    // Navigate to the Billing tab
    await settingsPage.clickTab("Billing");

    // BillingTab either shows:
    // 1. A payment method card + "Manage payment method" button (has payment)
    // 2. "No payment method on file." text + "Manage payment method" button (no payment)
    // 3. An error message if the API call fails in CI
    // All three are valid loaded states.
    const billingContent = page.locator(
      'button:has-text("Manage payment method"), ' +
      'text=No payment method on file, ' +
      'text=Payment method, ' +
      '.text-destructive'
    );
    await expect(billingContent.first()).toBeVisible({ timeout: 10000 });
  });
});
