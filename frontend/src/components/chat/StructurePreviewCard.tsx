/**
 * StructurePreviewCard — displays metadata for a resolved protein structure.
 *
 * Shows all chains with their protein names, resolution, method, and residue
 * counts. Users can select which chain to target for design.
 */

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { StructureSummary, ChainInfo } from "@/lib/agent";

interface StructurePreviewCardProps {
  data: StructureSummary;
  onUseDifferent?: () => void;
  onChainSelected?: (chainId: string) => void;
  /** Controlled selected chain — when provided, overrides internal state */
  selectedChainOverride?: string;
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

export function StructurePreviewCard({ data, onUseDifferent, onChainSelected, selectedChainOverride }: StructurePreviewCardProps) {
  const pdb_id = data.pdb_id ?? "Unknown";
  const protein_name = data.protein_name ?? "Unknown protein";
  const resolution = data.resolution ?? null;
  const method = data.method ?? "";
  const chains: ChainInfo[] = data.chains ?? [];
  const normalization_changes = data.normalization_changes ?? [];
  const defaultChain = data.selected_chain ?? (chains[0]?.id || "A");

  const [internalChain, setInternalChain] = useState(defaultChain);
  const selectedChain = selectedChainOverride ?? internalChain;

  function handleChainSelect(chainId: string) {
    setInternalChain(chainId);
    onChainSelected?.(chainId);
  }

  // Total residues across all chains
  const totalResidues = chains.length > 0
    ? chains.reduce((sum, c) => sum + (c.residue_count || 0), 0)
    : data.residue_count ?? 0;

  // Organism — take from first chain that has it, or from top-level data
  const organism = chains.find((c) => c.organism)?.organism || data.organism || "";

  return (
    <Card className="my-2 border-border/50 font-body">
      <CardContent className="px-4 py-4 space-y-3">
        {/* PDB ID and protein name */}
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm text-foreground font-semibold">{pdb_id}</span>
          <span className="text-base text-foreground">{protein_name}</span>
        </div>

        {/* Entry metadata */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Resolution</span>
          <span className="text-foreground">{formatResolution(resolution, method)}</span>
          <span>Method</span>
          <span className="text-foreground">{method || "—"}</span>
          {organism && (
            <>
              <span>Organism</span>
              <span className="text-foreground italic">{organism}</span>
            </>
          )}
          <span>Total residues</span>
          <span className="text-foreground">{totalResidues}</span>
        </div>

        {/* Chain list with selection */}
        {chains.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-sm text-muted-foreground">
              Chains ({chains.length})
            </p>
            <div className="space-y-1">
              {chains.map((chain) => {
                const isSelected = chain.id === selectedChain;
                return (
                  <button
                    key={chain.id}
                    onClick={() => handleChainSelect(chain.id)}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${
                      isSelected
                        ? "bg-primary/15 border border-primary/40"
                        : "bg-secondary/50 border border-transparent hover:bg-secondary"
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <div className="flex items-baseline gap-2 min-w-0">
                        <span className={`font-mono font-semibold shrink-0 ${isSelected ? "text-primary" : "text-foreground"}`}>
                          {chain.id}
                        </span>
                        <span className="text-muted-foreground truncate">
                          {chain.name}
                        </span>
                      </div>
                      <span className="text-muted-foreground shrink-0 tabular-nums">
                        {chain.residue_count} res
                      </span>
                    </div>
                    {isSelected && (
                      <span className="text-xs text-primary mt-0.5 block">
                        Target chain
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Fallback: no per-chain data */}
        {chains.length === 0 && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>Chain</span>
            <span className="text-foreground font-mono">{selectedChain}</span>
          </div>
        )}

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
