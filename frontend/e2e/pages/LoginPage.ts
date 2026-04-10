import { Page } from "@playwright/test";

/**
 * LoginPage — page object for the /login route.
 *
 * The login form uses react-hook-form with Zod validation.
 * Inputs are rendered via shadcn Input (renders as <input>).
 * The form field names are registered via react-hook-form's name prop,
 * which sets the HTML name attribute on the underlying input.
 */
export class LoginPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/login");
  }

  /**
   * Fill credentials and submit. Waits for navigation to /chat
   * (the post-login redirect target in Login.tsx).
   */
  async login(email: string, password: string) {
    await this.page.goto("/login");
    await this.page.fill('input[name="email"]', email);
    await this.page.fill('input[name="password"]', password);
    await this.page.click('button[type="submit"]');
    await this.page.waitForURL(/\/(chat|$)/, { timeout: 10000 });
  }

  /**
   * Returns the text content of the error message element.
   * Login.tsx renders errors as a <p> with class text-destructive.
   */
  async getErrorMessage(): Promise<string | null> {
    const locator = this.page.locator(".text-destructive").first();
    await locator.waitFor({ timeout: 5000 });
    return locator.textContent();
  }
}
