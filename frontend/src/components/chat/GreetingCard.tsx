/**
 * GreetingCard — shown as the first item in the MessageList before any user input.
 *
 * Renders as a full-width centered card (not a chat bubble) with the opening
 * prompt for the scientist to describe their design goal.
 */

import { Card, CardContent } from "@/components/ui/card";

export function GreetingCard() {
  return (
    <div className="flex justify-center px-4 py-6">
      <Card className="w-full max-w-2xl bg-card border-border/50">
        <CardContent className="px-6 py-6 text-center">
          <h2 className="text-xl font-semibold text-foreground mb-3">
            What are you designing today?
          </h2>
          <p className="text-base text-muted-foreground leading-relaxed">
            Describe your target protein, paste a PDB or UniProt accession, or drag in a .pdb
            file. I'll identify the right tool and guide you through the parameters.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
