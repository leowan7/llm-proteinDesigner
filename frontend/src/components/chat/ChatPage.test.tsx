import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ChatPage } from "./ChatPage";

// Mock useLayoutContext which calls useOutletContext — unavailable outside AuthenticatedLayout.
vi.mock("@/components/layout/AuthenticatedLayout", () => ({
  useLayoutContext: vi.fn().mockReturnValue({
    refreshSessions: vi.fn().mockResolvedValue(undefined),
  }),
}));

// Mock session management — avoids real API calls on mount.
vi.mock("@/lib/sessions", () => ({
  listSessions: vi.fn().mockResolvedValue({ sessions: [] }),
  loadSession: vi.fn().mockResolvedValue({ messages: [] }),
  createPersistentSession: vi.fn().mockResolvedValue({ id: "new-session-id" }),
}));

// Mock agent communication — avoids SSE streams in tests.
vi.mock("@/lib/agent", () => ({
  uploadPdbFile: vi.fn().mockResolvedValue({ normalized_path: "/tmp/test.pdb" }),
  sendMessage: vi.fn().mockResolvedValue(undefined),
}));

// Mock job status subscription — avoids WebSocket/SSE in tests.
vi.mock("@/lib/jobs", () => ({
  subscribeToJobStatus: vi.fn().mockReturnValue(() => {}),
  cancelJob: vi.fn().mockResolvedValue(undefined),
  getJob: vi.fn().mockResolvedValue(null),
}));

describe("ChatPage (smoke test)", () => {
  function renderChatPage(sessionId = "test-session-abc") {
    return render(
      <MemoryRouter initialEntries={[`/chat/${sessionId}`]}>
        <Routes>
          <Route path="/chat/:sessionId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("renders without crashing", () => {
    const { container } = renderChatPage();
    expect(container).toBeTruthy();
  });

  it("renders the chat input area", () => {
    const { container } = renderChatPage();
    // ChatInput renders a textarea for message input
    const textarea = container.querySelector("textarea");
    expect(textarea).toBeInTheDocument();
  });

  it("renders the context panel placeholder text on desktop", () => {
    const { container } = renderChatPage();
    expect(container.textContent).toContain("Current context");
  });
});
