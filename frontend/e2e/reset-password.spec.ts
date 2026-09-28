import { test, expect, type Page } from "@playwright/test";

/**
 * Landing on the reset page from a Supabase recovery email. The two auth
 * calls are mocked, so no Supabase or backend is needed.
 *
 * A redirect loop is caught by counting history writes rather than waiting
 * for the browser to stop it.
 */

const RECOVERY_URL = "/reset-password/confirm#access_token=a&refresh_token=b&type=recovery";

async function countHistoryWrites(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { historyWrites: number };
    w.historyWrites = 0;
    for (const method of ["pushState", "replaceState"] as const) {
      const original = History.prototype[method];
      History.prototype[method] = function (this: History, ...args: Parameters<History["pushState"]>) {
        w.historyWrites += 1;
        return original.apply(this, args);
      };
    }
  });
  return () => page.evaluate(() => (window as unknown as { historyWrites: number }).historyWrites);
}

async function mockResetApi(page: Page) {
  const json = (body: object) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  // Held back so an unfixed loop has time to spin before the hash is cleared.
  await page.route("**/auth/exchange-token", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await route.fulfill(json({ message: "Token exchanged" }));
  });
  await page.route("**/auth/update-password", (route) =>
    route.fulfill(json({ message: "Password updated." })),
  );
}

test.describe("Password reset landing", () => {
  test("a recovery link opens the form without a redirect loop", async ({ page }) => {
    const historyWrites = await countHistoryWrites(page);
    await mockResetApi(page);
    await page.goto(RECOVERY_URL);

    await expect(page.getByLabel("New password", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/reset-password\/confirm$/);
    expect(await historyWrites()).toBeLessThanOrEqual(10);
  });

  test("saving the new password goes to sign in with a notice", async ({ page }) => {
    await mockResetApi(page);
    await page.goto(RECOVERY_URL);

    await page.getByLabel("New password", { exact: true }).fill("Passw0rd!12");
    await page.getByLabel("Confirm password", { exact: true }).fill("Passw0rd!12");
    await page.getByRole("button", { name: "Set new password" }).click();

    await expect(page).toHaveURL(/\/login$/);
    await expect(
      page.getByText("Your password has been updated. Sign in with your new password."),
    ).toBeVisible();
  });

  test("an expired link says so and stays on the reset page", async ({ page }) => {
    await page.goto(
      "/reset-password/confirm#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    );

    await expect(page.getByText(/expired or was already used/)).toBeVisible();
    await expect(page).toHaveURL(/\/reset-password\/confirm/);
  });
});
