/**
 * GreetingCard — shown as the first item in the MessageList before any user input.
 *
 * Renders as a full-width centered card (not a chat bubble) with:
 * - Opening prompt for the scientist to describe their design goal
 * - 4 clickable example prompts that auto-fill the chat input (D-21)
 * - Capability indicators: supported tools and input types (D-22)
 */

import { Card, CardContent } from "@/components/ui/card";

const EXAMPLE_PROMPTS = [
  "Design a binder for the IL-6 receptor extracellular domain",
  "Generate de novo backbones for a 100-residue protein",
  "I have a PDB file for my target -- help me design a binder",
  "What tools are available for antibody design?",
];

interface GreetingCardProps {
  /**
   * Called when the user clicks an example prompt.
   * The prompt text is passed to the parent to auto-fill the chat input.
   */
  onPromptClick?: (prompt: string) => void;
}

export function GreetingCard({ onPromptClick }: GreetingCardProps) {
  return (
    <div className="flex justify-center px-4 py-6">
      <Card className="w-full max-w-2xl bg-card border-border/50 font-body">
        <CardContent className="px-6 py-6 text-center">
          <div className="flex justify-center mb-4">
            <div className="size-10 rounded-lg bg-primary flex items-center justify-center text-primary-foreground font-display font-semibold text-base">
              B
            </div>
          </div>
          <h2 className="font-display text-xl font-semibold text-foreground mb-3">
            What are you designing today?
          </h2>
          <p className="text-base text-muted-foreground leading-relaxed">
            Describe your target protein, paste a PDB or UniProt accession, or drag in a
            .pdb file. I'll identify the right tool and guide you through the parameters.
          </p>

          {/* Example prompts — each auto-fills the chat input on click */}
          <div className="mt-6 space-y-2 max-w-xl mx-auto">
            {EXAMPLE_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => onPromptClick?.(prompt)}
                className="w-full text-left px-4 py-3 rounded-lg bg-secondary/50 hover:bg-secondary text-sm text-foreground transition-colors cursor-pointer border border-border/50 hover:border-border"
              >
                {prompt}
              </button>
            ))}
          </div>

          {/* Capability indicators — available tools */}
          <div className="mt-6 flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>RFdiffusion</span>
            <span className="text-border">·</span>
            <span>BindCraft</span>
            <span className="text-border">·</span>
            <span>BoltzGen</span>
            <span className="text-border">·</span>
            <span>RFantibody</span>
          </div>
          {/* Input type indicators */}
          <div className="mt-2 flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Upload PDB, PDB ID, or UniProt ID</span>
            <span className="text-border">·</span>
            <span>Results in 30 min - 2 hours</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
