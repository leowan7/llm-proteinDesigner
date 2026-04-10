import { Page, expect } from "@playwright/test";

/**
 * JobPage — page object for the /jobs/:id route.
 *
 * The job detail page shows:
 * - JobStatusCard with a status badge (queued, running, complete, failed, cancelled)
 * - RunSummaryCard (when complete or cancelled)
 * - CandidateCard list (when complete)
 * - JobFailureCard (when failed)
 *
 * Status badge is rendered by the StatusBadge component inside JobStatusCard.
 */
export class JobPage {
  constructor(private page: Page) {}

  async goto(jobId: string) {
    await this.page.goto(`/jobs/${jobId}`);
  }

  /**
   * Returns the text content of the job status badge.
   * JobStatusCard renders a Badge component showing the current status.
   * StatusBadge component uses role="status" or can be targeted by text.
   */
  async getStatus(): Promise<string | null> {
    const badge = this.page.locator('[data-testid="job-status"], [role="status"]').first();
    return badge.textContent();
  }

  /**
   * Poll until the status badge text matches the expected status string.
   * Uses expect with timeout for retry logic.
   */
  async waitForStatus(status: string) {
    const badge = this.page.locator('[data-testid="job-status"], [role="status"]').first();
    await expect(badge).toContainText(status, { timeout: 30000 });
  }
}
