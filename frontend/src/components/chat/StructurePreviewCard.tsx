/**
 * StructurePreviewCard — displays metadata for a resolved protein structure.
 *
 * Shown inline in the chat thread after a structure is resolved from a PDB ID,
 * UniProt accession, or uploaded file. Includes a collapsible normalization
 * summary and an override action to switch structures.
 */

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { StructureSummary } from "@/lib/agent";

interface StructurePreviewCardProps {
  data: Partial<StructureSummary> & Record<string, unknown>;
  onUseDifferent?: () => void;
}

/**
 * Formats resolution value for display.
 * Returns "N/A (NMR)" for null resolution (NMR structures report no single
 * resolution value), or "X.XX Å" for crystallographic/EM structures.
 */
function formatResolution(resolution: number | null | undefined, method: string | undefined): string {
  if (resolution === null || resolution === undefined) {
    return method?.toUpperCase().includes("NMR") ? "N/A (NMR)" : "N/A";
  }
  return `${resolution.toFixed(2)} Å`;
}

export function StructurePreviewCard({ data, onUseDifferent }: StructurePreviewCardProps) {
  // Data may come from raw tool results with missing fields — default everything
  const pdb_id = data.pdb_id ?? (data as Record<string, unknown>).pdb_id as string ?? "Unknown";
  const protein_name = data.protein_name ?? "Unknown protein";
  const resolution = data.resolution ?? null;
  const method = data.method ?? "";
  const chain_count = data.chain_count ?? 0;
  const selected_chain = data.selected_chain ?? "—";
  const residue_count = data.residue_count ?? 0;
  const normalization_changes = data.normalization_changes ?? [];

  return (
    <Card className="my-2 border-border/50">
      <CardContent className="px-4 py-4 space-y-2">
        {/* PDB ID and protein name */}
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm text-foreground font-semibold">{pdb_id}</span>
          <span className="text-base text-foreground">{protein_name}</span>
        </div>

        {/* Metadata grid */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Resolution</span>
          <span className="text-foreground">{formatResolution(resolution, method)}</span>
          <span>Method</span>
          <span className="text-foreground">{method}</span>
          <span>Chains</span>
          <span className="text-foreground">
            {chain_count} {chain_count === 1 ? "chain" : "chains"} — using chain{" "}
            <span className="font-mono">{selected_chain}</span>
          </span>
          <span>Residues</span>
          <span className="text-foreground">{residue_count}</span>
        </div>

        {/* Normalization changes — collapsible, closed by default */}
        {normalization_changes.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground transition-colors select-none">
              {normalization_changes.length} normalization{" "}
              {normalization_changes.length === 1 ? "change" : "changes"} applied
            </summary>
            <ul className="mt-2 ml-4 list-disc space-y-1 text-muted-foreground">
              {normalization_changes.map((change, index) => (
                <li key={index}>{change}</li>
              ))}
            </ul>
          </details>
        )}

        {/* Override action */}
        <div className="pt-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={onUseDifferent}
            className="text-muted-foreground hover:text-foreground px-0"
          >
            Use a different structure
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
