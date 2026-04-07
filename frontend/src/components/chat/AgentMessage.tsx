/**
 * AgentMessage — left-aligned chat bubble for agent responses.
 *
 * Renders markdown content (bold, inline code, bullet lists) using a lightweight
 * regex-based approach — no external markdown library required for the limited
 * subset used in agent responses.
 *
 * Below the text, optionally renders:
 * - Action buttons (outline variant, max 4) for confirmation steps
 * - Inline structured cards (StructurePreviewCard, ReviewCard, ValidationCard)
 */

import { StructurePreviewCard } from "./StructurePreviewCard";
import { ReviewCard } from "./ReviewCard";
import { ValidationCard } from "./ValidationCard";
import type { ChatCard } from "@/lib/agent";

interface AgentMessageProps {
  content: string;
  cards?: ChatCard[];
  /** Called with the launched job ID after the ReviewCard dispatches a job. */
  onJobLaunched?: (jobId: string) => void;
  onEditParams?: () => void;
  onAcknowledgeWarnings?: () => void;
  onUseDifferentStructure?: () => void;
  onChainSelected?: (chainId: string) => void;
  selectedChain?: string;
  warningsAcknowledged?: boolean;
  isValidating?: boolean;
  /** When true, ReviewCard auto-retries launch after Stripe setup success. */
  autoRetryAfterSetup?: boolean;
  /** When true, ReviewCard shows the payment cancelled alert. */
  setupCancelled?: boolean;
}

/**
 * Converts a limited subset of markdown to React elements.
 *
 * Supported:
 * - **bold** → <strong>
 * - `inline code` → <code>
 * - Lines starting with "- " → unordered list items
 *
 * All other content is rendered as plain text paragraphs.
 */
function renderMarkdown(text: string): React.ReactNode {
  // Split into paragraphs / blocks by double newlines
  const blocks = text.split(/\n\n+/);

  return blocks.map((block, blockIndex) => {
    const lines = block.split("\n");
    const isListBlock = lines.every((l) => l.trimStart().startsWith("- ") || l.trim() === "");

    if (isListBlock) {
      const listItems = lines.filter((l) => l.trimStart().startsWith("- "));
      return (
        <ul key={blockIndex} className="list-disc ml-4 space-y-1 my-1">
          {listItems.map((item, i) => (
            <li key={i}>{renderInline(item.replace(/^\s*-\s+/, ""))}</li>
          ))}
        </ul>
      );
    }

    return (
      <p key={blockIndex} className="leading-relaxed">
        {renderInline(block)}
      </p>
    );
  });
}

/**
 * Renders inline markdown tokens (**bold** and `code`) within a text string.
 * Returns an array of React nodes with the appropriate elements.
 */
function renderInline(text: string): React.ReactNode[] {
  // Pattern matches **bold** or `code` segments
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={i} className="font-mono text-sm bg-muted px-1 py-0.5 rounded">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

export function AgentMessage({
  content,
  cards,
  onJobLaunched,
  onEditParams,
  onAcknowledgeWarnings,
  onUseDifferentStructure,
  onChainSelected,
  selectedChain,
  warningsAcknowledged = false,
  isValidating = false,
  autoRetryAfterSetup = false,
  setupCancelled = false,
}: AgentMessageProps) {
  return (
    <div className="flex justify-start px-4 py-1">
      <div className="max-w-[85%] w-full">
        {/* Message bubble */}
        {content && (
          <div className="rounded-2xl rounded-tl-sm bg-card ring-1 ring-foreground/10 px-4 py-3 text-base text-foreground space-y-2 font-body">
            {renderMarkdown(content)}
          </div>
        )}


        {/* Inline structured cards */}
        {cards && cards.length > 0 && (
          <div className="mt-2 space-y-2">
            {cards.map((card, i) => {
              if (card.type === "structure_preview") {
                return (
                  <StructurePreviewCard
                    key={i}
                    data={card.data}
                    onUseDifferent={onUseDifferentStructure}
                    onChainSelected={onChainSelected}
                    selectedChainOverride={selectedChain}
                  />
                );
              }
              if (card.type === "validation") {
                return (
                  <ValidationCard
                    key={i}
                    data={card.data}
                    onAcknowledge={() => onAcknowledgeWarnings?.()}
                    acknowledged={warningsAcknowledged}
                  />
                );
              }
              if (card.type === "review") {
                return (
                  <ReviewCard
                    key={i}
                    data={card.data}
                    onJobLaunched={(jobId) => onJobLaunched?.(jobId)}
                    onEdit={() => onEditParams?.()}
                    disabled={isValidating}
                    warningsAcknowledged={warningsAcknowledged}
                    autoRetryAfterSetup={autoRetryAfterSetup}
                    setupCancelled={setupCancelled}
                  />
                );
              }
              return null;
            })}
          </div>
        )}
      </div>
    </div>
  );
}
