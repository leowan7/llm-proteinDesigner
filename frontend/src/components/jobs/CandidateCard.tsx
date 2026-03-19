/**
 * CandidateCard — individual design candidate with tool-native scores and PDB download.
 *
 * One card per candidate, ranked by primary score. Score grid adapts based on
 * the tool that generated the design.
 */

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

interface CandidateCardProps {
  rank: number;
  scores: Record<string, number>;
  tool: string;
  downloadUrl: string;
}

interface ScoreField {
  key: string;
  label: string;
  unit?: string;
}

/**
 * Returns the tool-native score fields to display for a given tool.
 * Only shows scores that are meaningful for that tool's output.
 */
function getScoreFields(tool: string): ScoreField[] {
  const normalized = tool.toLowerCase();
  if (normalized === "bindcraft") {
    return [
      { key: "binding_energy", label: "Binding energy", unit: "kcal/mol" },
      { key: "iPAE", label: "iPAE" },
    ];
  }
  if (normalized === "boltzgen") {
    return [{ key: "confidence", label: "Confidence" }];
  }
  // rfdiffusion and rfantibody
  return [
    { key: "pAE", label: "pAE" },
    { key: "pLDDT", label: "pLDDT" },
  ];
}

/**
 * Formats a numeric score value for display.
 * Shows 3 decimal places for small values, 1 for large, to match tool output conventions.
 */
function formatScore(value: number | undefined): string {
  if (value === undefined || value === null) return "—";
  if (Math.abs(value) < 10) return value.toFixed(3);
  return value.toFixed(1);
}

export function CandidateCard({ rank, scores, tool, downloadUrl }: CandidateCardProps) {
  const scoreFields = getScoreFields(tool);

  return (
    <Card className="border-border/50">
      <CardHeader className="px-4 pb-2 pt-4 flex flex-row items-center justify-between">
        <span className="text-sm font-semibold text-foreground">#{rank}</span>
        <a href={downloadUrl} download>
          <Button variant="outline" size="sm" className="h-9">
            Download PDB
          </Button>
        </a>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        {/* Score grid — tool-native metrics only */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          {scoreFields.map((field) => (
            <div key={field.key} className="space-y-0.5">
              <p className="text-sm text-muted-foreground">
                {field.label}
                {field.unit && (
                  <span className="text-xs ml-1">({field.unit})</span>
                )}
              </p>
              <p className="font-mono text-sm text-foreground">
                {formatScore(scores[field.key])}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
