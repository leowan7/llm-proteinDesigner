import { test, expect } from "@playwright/test";

const TARGET_URL = process.env.SMOKE_TARGET_URL || "http://localhost:5173";

test.describe("Production Smoke Test", () => {
  test("frontend loads and renders login page", async ({ page }) => {
    await page.goto(`${TARGET_URL}/login`, { timeout: 15000 });
    // Verify the page has a title (React app mounted)
    const title = await page.title();
    expect(title).toBeTruthy();
    // Verify login form is present (app rendered, not blank page)
    await expect(page.locator("form")).toBeVisible({ timeout: 10000 });
  });
});
