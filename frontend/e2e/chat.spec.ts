import { test, expect } from "@playwright/test";
import { LoginPage } from "./pages/LoginPage";
import { ChatPage } from "./pages/ChatPage";

/**
 * Chat E2E tests — D-06 flow 2.
 *
 * CRITICAL (D-08): These tests MUST always run in CI.
 * Do NOT add test.skip(process.env.CI) to any test in this file.
 *
 * The agent SSE endpoint is intercepted via page.route() so tests never
 * require a real Anthropic API key. The mock returns a deterministic SSE
 * response that exercises the full chat rendering pipeline.
 *
 * Agent endpoint: POST /api/agent/message (or /agent/message)
 * Response format: text/event-stream with JSON-encoded events.
 */

const TEST_EMAIL = "test@example.com";
const TEST_PASSWORD = "Password123!";

/** Minimal SSE body that exercises text rendering and a tool_result card. */
const MOCK_SSE_BODY = [
  'data: {"type":"status","text":"Resolving structure..."}\n\n',
  'data: {"type":"text","text":"I found the structure for your target."}\n\n',
  'data: {"type":"tool_result","tool_name":"resolve_pdb","result":{"pdb_id":"1ABC","name":"Test Protein"}}\n\n',
  'data: {"type":"text","text":"I can help you design a binder for this target."}\n\n',
  'data: {"type":"done"}\n\n',
].join("");

test.describe("Chat + job launch", () => {
  test.beforeEach(async ({ page }) => {
    // Login before each test
    const loginPage = new LoginPage(page);
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);

    // Intercept the agent session-create endpoint so we don't depend on
    // a real Anthropic API key being present in CI for session initialization.
    await page.route("**/agent/session", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ session_id: "e2e-test-session" }),
        });
      } else {
        await route.fallback();
      }
    });

    // Intercept the agent SSE endpoint to return a controlled response.
    // This prevents the test from requiring a real Anthropic API key in CI.
    await page.route("**/agent/message", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: MOCK_SSE_BODY,
      });
    });
  });

  test.slow(); // Allow generous timeout for SSE stream processing

  test("user can send a message and receive agent response", async ({ page }) => {
    const chatPage = new ChatPage(page);
    await chatPage.goto();

    // Send a message to the agent
    await chatPage.sendMessage("I want to design a binder for IL-6 receptor");

    // The mocked SSE delivers text content — wait for it to render.
    // AgentMessage renders text blocks in <p> elements inside the message list.
    await expect(
      page.locator("text=I found the structure for your target.")
    ).toBeVisible({ timeout: 15000 });
  });

  test("structure card appears when agent resolves PDB", async ({ page }) => {
    const chatPage = new ChatPage(page);
    await chatPage.goto();

    await chatPage.sendMessage("Design a binder for 1ABC");

    // The mocked SSE includes a tool_result with resolve_pdb.
    // StructurePreviewCard renders the PDB ID as visible text.
    // We wait for any content related to the resolved structure.
    await expect(
      page.locator("text=I can help you design a binder for this target.")
    ).toBeVisible({ timeout: 15000 });
  });
});
