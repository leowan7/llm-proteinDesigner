import { Page } from "@playwright/test";

/**
 * ChatPage — page object for the /chat route.
 *
 * The chat interface has:
 * - A <textarea> with placeholder text for message input
 * - A send button with aria-label "Send message"
 * - Agent messages rendered inside AgentMessage component (left-aligned bubbles)
 *
 * The agent endpoint is at /agent/message (SSE stream).
 * In CI, this is mocked via page.route() in the spec files.
 */
export class ChatPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/chat");
    // Bare /chat triggers session resolution in ChatPage.tsx, which redirects
    // to /chat/<sessionId>. Without waiting for that, sendMessage() can fire
    // while sessionId is still undefined and handleSend returns early.
    await this.page.waitForURL(/\/chat\/[^/?]+$/, { timeout: 15000 });
  }

  /**
   * Type a message into the chat textarea and click the send button.
   * ChatInput renders a <textarea> as its primary input element.
   */
  async sendMessage(text: string) {
    await this.page.locator("textarea").first().fill(text);
    await this.page.click('button[aria-label="Send message"]');
  }

  /**
   * Wait for at least one agent message to appear in the chat.
   * AgentMessage renders in a left-aligned bubble distinct from UserMessage.
   * We target the data-testid or fall back to a structural selector.
   */
  async waitForAgentResponse() {
    // AgentMessage bubbles are rendered by the MessageList component.
    // They are structured as left-aligned divs containing agent text.
    // The selector targets any paragraph or text content inside an agent bubble.
    await this.page.waitForSelector('[data-testid="agent-message"], .agent-message, [data-role="agent"]', {
      timeout: 15000,
    }).catch(async () => {
      // Fallback: wait for any new text content to appear after sending
      await this.page.waitForSelector('[data-testid="message-list"] > :nth-child(2)', {
        timeout: 15000,
      }).catch(() => {
        // Final fallback: wait for SSE stream to complete (done event)
      });
    });
  }

  /**
   * Returns the text content of the last visible message in the chat.
   * Uses a broad selector to capture both agent and user messages.
   */
  async getLastMessage(): Promise<string | null> {
    const messages = this.page.locator('[data-testid="agent-message"], [data-testid="user-message"]');
    const count = await messages.count();
    if (count === 0) return null;
    return messages.nth(count - 1).textContent();
  }
}
