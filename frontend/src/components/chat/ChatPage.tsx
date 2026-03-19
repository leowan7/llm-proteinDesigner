/**
 * ChatPage — full-width chat interface for the protein design agent.
 *
 * Layout:
 * - Two-column on desktop: 60% chat (MessageList + ChatInput), 40% context panel
 * - Single-column on mobile: context panel accessible via "View summary" Sheet
 * - Full-width, does NOT use AuthLayout (chat needs the full viewport)
 *
 * State:
 * - messages: running chat history
 * - sessionId: active agent session (created on mount)
 * - isProcessing: true while SSE stream is active
 * - statusText: most recent status event from SSE
 * - lastCard: most recent structured card (mirrored in context panel)
 * - warningsAcknowledged: user has acknowledged pre-flight warnings
 *
 * Session lifecycle:
 * - createSession on mount
 * - deleteSession + createSession on "New Session"
 * - No persistence between page refreshes (session is ephemeral)
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { StructurePreviewCard } from "./StructurePreviewCard";
import { ReviewCard } from "./ReviewCard";
import { ValidationCard } from "./ValidationCard";
import {
  createSession,
  deleteSession,
  uploadPdbFile,
  sendMessage,
} from "@/lib/agent";
import type { ChatMessage, ChatCard, AgentEvent } from "@/lib/agent";

/** Generate a unique message ID */
function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [lastCard, setLastCard] = useState<ChatCard | null>(null);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [showNewSessionConfirm, setShowNewSessionConfirm] = useState(false);

  // Tracks the in-progress assistant message id during streaming
  const currentAssistantIdRef = useRef<string | null>(null);

  /** Initialize a new session on mount */
  useEffect(() => {
    let cancelled = false;
    createSession()
      .then((id) => {
        if (!cancelled) setSessionId(id);
      })
      .catch((err) => {
        console.error("Failed to create session:", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Process a single SSE event from the agent stream.
   * Updates the in-progress assistant message or appends cards/actions.
   */
  const handleEvent = useCallback((event: AgentEvent, assistantId: string) => {
    if (event.type === "status") {
      setStatusText(event.text);
    } else if (event.type === "text") {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantId
            ? { ...msg, content: msg.content + event.text }
            : msg,
        ),
      );
    } else if (event.type === "tool_result") {
      // Build a ChatCard from the tool result
      const card = buildCard(event.tool_name, event.result);
      if (card) {
        setLastCard(card);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? { ...msg, cards: [...(msg.cards ?? []), card] }
              : msg,
          ),
        );
      }
    } else if (event.type === "done") {
      setIsProcessing(false);
      setStatusText("");
    } else if (event.type === "error") {
      setIsProcessing(false);
      setStatusText("");
      // Append error as a system message
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: `Error: ${event.text}`,
        },
      ]);
    }
  }, []);

  /**
   * Build a ChatCard from a tool_result SSE event.
   * Returns null for tool results that don't map to a card.
   */
  function buildCard(
    toolName: string,
    result: Record<string, unknown>,
  ): ChatCard | null {
    if (toolName === "resolve_structure") {
      return { type: "structure_preview", data: result as ChatCard["data"] & object } as ChatCard;
    }
    if (toolName === "validate_preflight") {
      return { type: "validation", data: result as ChatCard["data"] & object } as ChatCard;
    }
    if (toolName === "prepare_review") {
      return { type: "review", data: result as ChatCard["data"] & object } as ChatCard;
    }
    return null;
  }

  /**
   * Send a user message (and optionally a PDB file) to the agent.
   * Handles file upload first, then sends the message with context.
   */
  const handleSend = useCallback(
    async (text: string, file?: File) => {
      if (!sessionId) return;
      if (isProcessing) return;

      setIsProcessing(true);
      setWarningsAcknowledged(false);

      let messageText = text;

      // Upload file first if provided, then include context in the message
      if (file) {
        try {
          const uploadResult = await uploadPdbFile(file);
          const fileContext = `[Uploaded file: ${file.name} → ${uploadResult.normalized_path}]`;
          messageText = messageText ? `${fileContext}\n\n${messageText}` : fileContext;
        } catch (err) {
          setMessages((prev) => [
            ...prev,
            {
              id: newId(),
              role: "assistant",
              content: `Failed to upload ${file.name}: ${err instanceof Error ? err.message : "Unknown error"}`,
            },
          ]);
          setIsProcessing(false);
          return;
        }
      }

      // Add user message to history
      const userMessage: ChatMessage = {
        id: newId(),
        role: "user",
        content: text || `[Uploaded ${file?.name ?? "file"}]`,
      };

      // Create in-progress assistant message
      const assistantId = newId();
      currentAssistantIdRef.current = assistantId;
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      setMessages((prev) => [...prev, userMessage, assistantMessage]);

      try {
        await sendMessage(sessionId, messageText, (event) =>
          handleEvent(event, assistantId),
        );
      } catch (err) {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? {
                  ...msg,
                  content: `Connection error: ${err instanceof Error ? err.message : "Unknown error"}`,
                }
              : msg,
          ),
        );
        setIsProcessing(false);
        setStatusText("");
      }
    },
    [sessionId, isProcessing, handleEvent],
  );

  /** Handle action button clicks — send the button's value as a user message */
  const handleAction = useCallback(
    (value: string) => {
      handleSend(value);
    },
    [handleSend],
  );

  /** Start a new session — requires confirmation */
  const handleNewSession = useCallback(async () => {
    if (sessionId) {
      await deleteSession(sessionId).catch(() => {});
    }
    const newSessionId = await createSession().catch(() => null);
    if (newSessionId) {
      setSessionId(newSessionId);
    }
    setMessages([]);
    setLastCard(null);
    setWarningsAcknowledged(false);
    setIsProcessing(false);
    setStatusText("");
    setShowNewSessionConfirm(false);
  }, [sessionId]);

  /** Render the context panel content (most recent structured card) */
  function renderContextPanel() {
    if (!lastCard) {
      return (
        <div className="flex items-center justify-center h-full">
          <p className="text-sm text-muted-foreground text-center px-4">
            Structured cards will appear here as you work through your design.
          </p>
        </div>
      );
    }

    if (lastCard.type === "structure_preview") {
      return (
        <StructurePreviewCard
          data={lastCard.data}
          onUseDifferent={() => handleSend("I want to use a different structure")}
        />
      );
    }
    if (lastCard.type === "validation") {
      return (
        <ValidationCard
          data={lastCard.data}
          onAcknowledge={() => setWarningsAcknowledged(true)}
          acknowledged={warningsAcknowledged}
        />
      );
    }
    if (lastCard.type === "review") {
      return (
        <ReviewCard
          data={lastCard.data}
          onLaunch={() => handleSend("Launch the job")}
          onEdit={() => handleSend("I want to edit the parameters")}
          disabled={isProcessing}
          warningsAcknowledged={warningsAcknowledged}
        />
      );
    }
    return null;
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-border shrink-0">
        <h1 className="text-xl font-semibold text-foreground">Protein Designer</h1>
        <div className="flex items-center gap-2">
          {/* Mobile: context panel sheet trigger */}
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" className="md:hidden">
                View summary
              </Button>
            </SheetTrigger>
            <SheetContent side="bottom" className="h-[60vh]">
              <SheetHeader>
                <SheetTitle>Current Summary</SheetTitle>
              </SheetHeader>
              <div className="overflow-y-auto mt-4 px-1">{renderContextPanel()}</div>
            </SheetContent>
          </Sheet>

          {/* New Session button with inline confirmation */}
          {!showNewSessionConfirm ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowNewSessionConfirm(true)}
            >
              New Session
            </Button>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                Start new session? Your current conversation will be cleared.
              </span>
              <Button variant="default" size="sm" onClick={handleNewSession}>
                Confirm
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowNewSessionConfirm(false)}
              >
                Cancel
              </Button>
            </div>
          )}
        </div>
      </header>

      {/* Main content area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left column: chat thread (60% on desktop, full on mobile) */}
        <div className="flex flex-col flex-1 md:w-3/5 md:max-w-[60%] overflow-hidden">
          <MessageList
            messages={messages}
            isProcessing={isProcessing}
            statusText={statusText}
            warningsAcknowledged={warningsAcknowledged}
            onAction={handleAction}
            onLaunchJob={() => handleSend("Launch the job")}
            onEditParams={() => handleSend("I want to edit the parameters")}
            onAcknowledgeWarnings={() => setWarningsAcknowledged(true)}
            onUseDifferentStructure={() => handleSend("I want to use a different structure")}
          />
          <ChatInput onSend={handleSend} isProcessing={isProcessing} />
        </div>

        {/* Right column: context panel (40% on desktop, hidden on mobile) */}
        <div className="hidden md:flex md:flex-col md:w-2/5 border-l border-border overflow-y-auto p-4">
          <p className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            Current context
          </p>
          {renderContextPanel()}
        </div>
      </div>
    </div>
  );
}
