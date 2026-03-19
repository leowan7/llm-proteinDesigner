/**
 * BindCraftZeroOutputCard — shown when BindCraft completes with 0 designs passing filters.
 *
 * This is NOT a failure card. BindCraft zero output is expected behavior when filter
 * criteria are strict. No destructive colors. Includes agent guidance on next steps.
 */

import { Card, CardContent, CardHeader } from "@/components/ui/card";

interface BindCraftZeroOutputCardProps {
  /** Agent-generated guidance on loosening parameters or next steps. */
  agentGuidance?: string;
}

/**
 * Renders inline markdown tokens (**bold** and `code`).
 * Matches the regex pattern from AgentMessage.tsx.
 */
function renderInline(text: string): React.ReactNode[] {
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

/**
 * Converts a limited subset of markdown to React elements.
 */
function renderMarkdown(text: string): React.ReactNode {
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

export function BindCraftZeroOutputCard({ agentGuidance }: BindCraftZeroOutputCardProps) {
  return (
    <Card className="border-border/50">
      <CardHeader className="px-4 pb-2 pt-4">
        <h2 className="text-xl font-semibold text-foreground">No designs passed filters</h2>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-3">
        {/* Exact copy from UI-SPEC — do not modify */}
        <p className="text-base text-foreground">
          BindCraft completed successfully but 0 designs passed the active filters. This is
          expected behavior when filter criteria are strict. You were charged for GPU compute
          time consumed.
        </p>

        {/* Agent guidance on loosening parameters */}
        {agentGuidance && (
          <div className="text-base text-foreground space-y-2 pt-1">
            {renderMarkdown(agentGuidance)}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
