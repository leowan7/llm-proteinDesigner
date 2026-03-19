/**
 * UserMessage — right-aligned chat bubble for messages the scientist sends.
 *
 * Renders plain text (no markdown — user messages are typically short and literal).
 */

interface UserMessageProps {
  content: string;
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end px-4 py-1">
      <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-secondary text-foreground px-4 py-2 text-base leading-relaxed">
        {content}
      </div>
    </div>
  );
}
