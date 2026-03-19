/**
 * ChatInput — multi-line message input with PDB file drag-drop support.
 *
 * Features:
 * - shadcn Textarea with 44px min height (iOS touch target), expands to 120px
 * - Drag-drop zone for .pdb / .cif files (border shifts to --primary on hover)
 * - File attachment pill: filename + size + remove button
 * - Send button (lucide SendHorizontal) on right edge
 * - Enter to send, Shift+Enter for newline
 * - Disabled state while agent is processing (SSE stream active)
 */

import { useState, useRef, useCallback, type KeyboardEvent, type DragEvent } from "react";
import { SendHorizontal } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

interface ChatInputProps {
  onSend: (message: string, file?: File) => void;
  isProcessing: boolean;
}

/** Format file size in human-readable KB */
function formatFileSize(bytes: number): string {
  return `${Math.round(bytes / 1024)} KB`;
}

export function ChatInput({ onSend, isProcessing }: ChatInputProps) {
  const [text, setText] = useState("");
  const [attachedFile, setAttachedFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /** Check if a dragged file is an accepted PDB or mmCIF format */
  function isAcceptedFile(file: File): boolean {
    const name = file.name.toLowerCase();
    return name.endsWith(".pdb") || name.endsWith(".cif") || name.endsWith(".mmcif");
  }

  const handleDragOver = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const files = Array.from(e.dataTransfer.items);
      const hasPdb = files.some(
        (item) =>
          item.kind === "file" &&
          (item.type === "chemical/x-pdb" ||
            item.type === "" ||
            item.getAsFile()?.name?.match(/\.(pdb|cif|mmcif)$/i)),
      );
      if (hasPdb || files.length > 0) {
        setIsDragOver(true);
      }
    },
    [],
  );

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file && isAcceptedFile(file)) {
      setAttachedFile(file);
    }
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text, attachedFile, isProcessing],
  );

  function handleSend() {
    const trimmed = text.trim();
    if (!trimmed && !attachedFile) return;
    if (isProcessing) return;

    onSend(trimmed, attachedFile ?? undefined);
    setText("");
    setAttachedFile(null);
  }

  const canSend = (text.trim().length > 0 || attachedFile !== null) && !isProcessing;

  return (
    <div className="border-t border-border bg-background px-4 py-3">
      {/* Drag-drop zone wraps the entire input area */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative rounded-xl border transition-colors ${
          isDragOver
            ? "border-primary bg-primary/5"
            : "border-input bg-background"
        }`}
      >
        {/* Textarea */}
        <Textarea
          ref={textareaRef}
          value={isDragOver ? "" : text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            isDragOver
              ? "Drop PDB file to upload"
              : "Describe your design goal, paste a PDB ID, or drop a .pdb / .cif file"
          }
          disabled={isProcessing}
          rows={1}
          className={`
            min-h-[44px] max-h-[120px] resize-none
            border-0 shadow-none focus-visible:ring-0
            pr-12 py-3 bg-transparent
            text-base placeholder:text-muted-foreground
            disabled:opacity-50
          `}
        />

        {/* Send button — positioned inside the textarea container */}
        <Button
          variant="default"
          size="icon"
          aria-label="Send message"
          onClick={handleSend}
          disabled={!canSend}
          className="absolute right-2 bottom-2 h-9 w-9 bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
        >
          <SendHorizontal className="h-4 w-4" />
        </Button>
      </div>

      {/* File attachment pill */}
      {attachedFile && (
        <div className="mt-2 flex items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full bg-secondary px-3 py-1 text-sm text-foreground">
            <span>{attachedFile.name}</span>
            <span className="text-muted-foreground">· {formatFileSize(attachedFile.size)}</span>
            <button
              onClick={() => setAttachedFile(null)}
              className="text-muted-foreground hover:text-foreground transition-colors leading-none"
              aria-label="Remove attachment"
            >
              ×
            </button>
          </span>
        </div>
      )}
    </div>
  );
}
