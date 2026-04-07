/**
 * MessageList — scrollable list of chat messages with auto-scroll behavior.
 *
 * Renders:
 * - GreetingCard at the top (always shown)
 * - Alternating UserMessage and AgentMessage bubbles
 * - Typing indicator (three animated dots) while the SSE stream is active
 * - Status line showing the most recent status event
 *
 * Auto-scrolls to the bottom on new message append. Preserves scroll position
 * when the user scrolls up to review history (does not force-scroll in that case).
 */

import { useEffect, useRef, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { GreetingCard } from "./GreetingCard";
import { UserMessage } from "./UserMessage";
import { AgentMessage } from "./AgentMessage";
import type { ChatMessage, ChatCard } from "@/lib/agent";

interface MessageListProps {
  messages: ChatMessage[];
  isProcessing: boolean;
  statusText: string;
  warningsAcknowledged: boolean;
  onAction: (value: string) => void;
  /** Called with job ID when the ReviewCard successfully dispatches a job. */
  onJobLaunched: (jobId: string) => void;
  onEditParams: () => void;
  onAcknowledgeWarnings: () => void;
  onUseDifferentStructure: () => void;
  /** Called when an example prompt is clicked in GreetingCard (D-21). */
  onPromptClick?: (prompt: string) => void;
  /** Screen reader announcement for new assistant messages only (WCAG). */
  lastAnnouncedMessage?: string;
}

/** Determines if any card in a message is a ReviewCard (controls isValidating state) */
function hasReviewCard(cards?: ChatCard[]): boolean {
  return cards?.some((c) => c.type === "review") ?? false;
}

export function MessageList({
  messages,
  isProcessing,
  statusText,
  warningsAcknowledged,
  onAction,
  onJobLaunched,
  onEditParams,
  onAcknowledgeWarnings,
  onUseDifferentStructure,
  onPromptClick,
  lastAnnouncedMessage,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  /**
   * Tracks whether the user has scrolled up to review history.
   * If true, we do not force-scroll on new messages.
   */
  const isUserScrolledUp = useRef(false);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    // Consider "scrolled up" if more than 100px from the bottom
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    isUserScrolledUp.current = distanceFromBottom > 100;
  }, []);

  // Auto-scroll to bottom when messages change, unless user has scrolled up
  useEffect(() => {
    if (!isUserScrolledUp.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isProcessing]);

  return (
    <ScrollArea className="flex-1 overflow-y-auto" ref={scrollContainerRef as React.RefObject<HTMLDivElement>}>
      <div className="py-4 space-y-1" onScroll={handleScroll}>
        <GreetingCard onPromptClick={onPromptClick} />

        {messages.map((message) => {
          if (message.role === "user") {
            return <UserMessage key={message.id} content={message.content} />;
          }
          return (
            <AgentMessage
              key={message.id}
              content={message.content}
              cards={message.cards}
              actions={message.actions}
              onAction={onAction}
              onJobLaunched={onJobLaunched}
              onEditParams={onEditParams}
              onAcknowledgeWarnings={onAcknowledgeWarnings}
              onUseDifferentStructure={onUseDifferentStructure}
              warningsAcknowledged={warningsAcknowledged}
              isValidating={isProcessing && hasReviewCard(message.cards)}
            />
          );
        })}

        {/* Typing indicator — shown while SSE stream is active */}
        {isProcessing && (
          <div className="flex justify-start px-4 py-1">
            <div className="rounded-2xl rounded-tl-sm bg-card ring-1 ring-foreground/10 px-4 py-3">
              <div className="flex items-center gap-1">
                <span
                  className="w-2 h-2 rounded-full bg-muted-foreground animate-bounce"
                  style={{ animationDelay: "0ms" }}
                />
                <span
                  className="w-2 h-2 rounded-full bg-muted-foreground animate-bounce"
                  style={{ animationDelay: "150ms" }}
                />
                <span
                  className="w-2 h-2 rounded-full bg-muted-foreground animate-bounce"
                  style={{ animationDelay: "300ms" }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Status line — most recent SSE status event */}
        {isProcessing && statusText && (
          <div className="px-4">
            <p className="text-sm text-muted-foreground">{statusText}</p>
          </div>
        )}

        {/* Screen reader announcement for new messages only.
            aria-live="polite" announces after the current interaction completes.
            aria-atomic="true" reads the whole announcement as one unit.
            Does NOT announce the full message history on initial load —
            only the lastAnnouncedMessage prop, which is set by ChatPage
            after the SSE `done` event. */}
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="sr-only"
        >
          {lastAnnouncedMessage}
        </div>

        {/* Scroll anchor */}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
