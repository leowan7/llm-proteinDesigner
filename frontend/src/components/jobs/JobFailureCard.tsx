/**
 * JobFailureCard — shown on /jobs/{id} and in chat when a job fails.
 *
 * Displays the failure category and agent guidance on what to fix before
 * resubmitting. No retry button — auto-retry is explicitly out of scope
 * per REQUIREMENTS.md.
 */

import { Card, CardContent, CardHeader } from "@/components/ui/card";

/** Valid failure categories from the backend error_category field. */
type FailureCategory = "GPU timeout" | "Invalid input" | "Provider error" | string;

interface JobFailureCardProps {
  errorCategory: FailureCategory | null;
  /** Agent-generated guidance on what to fix before resubmitting. */
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

export function JobFailureCard({ errorCategory, agentGuidance }: JobFailureCardProps) {
  return (
    <Card className="border-destructive/40">
      <CardHeader className="px-4 pb-2 pt-4">
        <h2 className="text-xl font-semibold text-foreground">Job failed</h2>
        {errorCategory && (
          <p className="text-sm text-muted-foreground">{errorCategory}</p>
        )}
      </CardHeader>
      <CardContent className="px-4 pb-4">
        {agentGuidance ? (
          <div className="text-base text-foreground space-y-2">
            {renderMarkdown(agentGuidance)}
          </div>
        ) : (
          <p className="text-base text-muted-foreground">
            Contact support if the issue persists.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
