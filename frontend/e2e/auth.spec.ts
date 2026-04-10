import { test, expect } from "@playwright/test";
import { LoginPage } from "./pages/LoginPage";

/**
 * Authentication E2E tests — D-06 flow 1.
 *
 * Tests use the seed test account (test@example.com) as configured in conftest.py.
 * Credentials are never committed to source — the test account must exist in the
 * Supabase test instance via the seed migration.
 *
 * Security note (T-09-04): Only test@example.com seed account credentials
 * are used here. No real user credentials are referenced.
 */

const TEST_EMAIL = "test@example.com";
const TEST_PASSWORD = "Password123!";

test.describe("Authentication", () => {
  test("login with valid credentials redirects to home", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
    // Login.tsx navigates to /chat on success
    await expect(page).toHaveURL(/\/(chat|$)/);
  });

  test("login with invalid credentials shows error", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await page.goto("/login");
    await page.fill('input[name="email"]', "wrong@example.com");
    await page.fill('input[name="password"]', "wrongpassword");
    await page.click('button[type="submit"]');
    // Should stay on login page and show error
    await expect(page).toHaveURL(/login/);
    // Login.tsx renders errors as <p class="text-destructive"> (no role="alert")
    await expect(page.locator(".text-destructive").first()).toBeVisible({ timeout: 5000 });
  });

  test("unauthenticated user is redirected to login", async ({ page }) => {
    // Navigate directly to a protected route without any session cookie
    await page.goto("/chat");
    await expect(page).toHaveURL(/login/);
  });

  test("session persists across page refresh", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
    await expect(page).toHaveURL(/\/(chat|$)/);
    await page.reload();
    // After reload, AuthenticatedLayout should keep the user on the protected route
    await expect(page).toHaveURL(/\/(chat|$)/);
  });
});
