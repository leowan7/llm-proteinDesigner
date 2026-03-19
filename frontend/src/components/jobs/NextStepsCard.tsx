/**
 * NextStepsCard — agent-generated post-job guidance card.
 *
 * Read-only card shown at the bottom of /jobs/{id} when complete and
 * next_steps content exists. Renders markdown using the same regex-based
 * inline renderer as AgentMessage.tsx.
 */

import { Card, CardContent, CardHeader } from "@/components/ui/card";

interface NextStepsCardProps {
  nextSteps: string;
}

/**
 * Renders inline markdown tokens (**bold** and `code`) within a text string.
 * Matches the pattern from AgentMessage.tsx — no external library required.
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
 * Supports bold, inline code, and bullet lists — same subset as AgentMessage.tsx.
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

export function NextStepsCard({ nextSteps }: NextStepsCardProps) {
  return (
    <Card className="border-border/50">
      <CardHeader className="px-4 pb-2 pt-4">
        <h2 className="text-xl font-semibold text-foreground">Recommended next steps</h2>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        <div className="text-base text-foreground space-y-2">{renderMarkdown(nextSteps)}</div>
      </CardContent>
    </Card>
  );
}
