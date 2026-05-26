import { test, expect } from "@playwright/test";
import { LoginPage } from "./pages/LoginPage";

/**
 * Job status + results E2E tests — D-06 flow 3.
 *
 * Tests login then navigate to the job history and job detail pages.
 * Job status SSE tests are flaky in E2E (09-RESEARCH.md Pitfall 4),
 * so we use generous timeouts and only verify page structure, not live SSE events.
 *
 * Security note (T-09-04): Uses only the test@example.com seed account.
 */

const TEST_EMAIL = "test@example.com";
const TEST_PASSWORD = "Password123!";

test.describe("Job status + results", () => {
  test.beforeEach(async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
  });

  test("job history page loads and shows table or empty state", async ({ page }) => {
    await page.goto("/jobs");

    // JobHistoryPage renders either a <table> (has jobs) or an empty state
    // with a CTA to /chat. Either is a valid loaded state.
    const table = page.locator("table");

    // Use Promise.race pattern via expect with multiple locators
    await expect(table.or(page.locator('text=No jobs'))).toBeVisible({ timeout: 10000 });
  });

  test("job page loads for a valid job ID or shows not-found state", async ({ page }) => {
    await page.goto("/jobs");

    // If jobs table rows exist, click the first job link to verify detail page loads
    const jobLink = page.locator("table tbody tr a").first();
    const jobLinkCount = await jobLink.count();

    if (jobLinkCount > 0) {
      await jobLink.click();
      // JobPage shows a JobStatusCard — verify the page has rendered
      await expect(page.locator("text=Loading job...").or(page.locator('[data-testid="job-status"]'))).toBeVisible({ timeout: 10000 });
    } else {
      // No jobs — navigate directly to a non-existent job and verify error state.
      // JobPage renders <p class="text-base text-destructive">{loadError}</p>.
      // Avoid mixing CSS selectors with Playwright `text=` in one string
      // (which is not valid CSS); use .or() chaining instead.
      await page.goto("/jobs/not-a-real-uuid");
      const errorParagraph = page.locator(".text-destructive")
        .or(page.locator("text=Failed to load job"))
        .or(page.locator("text=not found"));
      await expect(errorParagraph.first()).toBeVisible({ timeout: 10000 });
    }
  });
});
