import { Page } from "@playwright/test";

/**
 * SettingsPage — page object for the /settings route.
 *
 * SettingsPage uses shadcn Tabs component with 4 tabs:
 * - Account
 * - Billing
 * - Usage
 * - Notifications
 *
 * TabsTrigger elements use role="tab" and contain the tab name text.
 * The active tab has aria-selected="true".
 */
export class SettingsPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/settings");
  }

  /**
   * Returns the text of the currently active tab.
   * Tabs with aria-selected="true" is the current tab.
   */
  async getActiveTab(): Promise<string | null> {
    const activeTab = this.page.locator('[role="tab"][aria-selected="true"]');
    return activeTab.textContent();
  }

  /**
   * Click a tab by its display name.
   * Matches the exact tab names from SettingsPage.tsx:
   * "Account", "Billing", "Usage", "Notifications"
   */
  async clickTab(name: string) {
    await this.page.click(`[role="tab"]:has-text("${name}")`);
  }
}
