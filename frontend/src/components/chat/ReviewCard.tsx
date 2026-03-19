/**
 * ReviewCard — pre-launch confirmation card showing the full job specification.
 *
 * The critical human-in-the-loop gate before dispatching a GPU job. Displays
 * tool selection rationale, target structure, parameters, and estimated cost.
 * Requires validation to pass (and warnings acknowledged if any) before
 * the Launch Job button becomes active.
 */

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { ReviewData } from "@/lib/agent";

interface ReviewCardProps {
  data: ReviewData;
  onLaunch: () => void;
  onEdit: () => void;
  disabled: boolean;
  warningsAcknowledged: boolean;
}

export function ReviewCard({
  data,
  onLaunch,
  onEdit,
  disabled,
  warningsAcknowledged,
}: ReviewCardProps) {
  const {
    design_goal,
    tool,
    rationale,
    target_pdb_id,
    target_chain,
    hotspot_residues,
    parameters,
    parameter_descriptions,
    estimated_cost_usd,
    validation_results,
    can_proceed,
    has_warnings,
  } = data;

  // Launch is available when: validation complete, can proceed, and warnings acknowledged (if any)
  const validationComplete = validation_results.length > 0;
  const warningsOk = !has_warnings || warningsAcknowledged;
  const canLaunch = !disabled && validationComplete && can_proceed && warningsOk;

  // Format cost — rough estimate, not a guarantee
  const costDisplay = `~$${estimated_cost_usd.toFixed(2)}`;

  return (
    <Card className="my-2 ring-2 ring-primary/30 border-border/50">
      <CardHeader className="px-4 pb-2 pt-4">
        <span className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Job Review
        </span>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-4">
        {/* Design goal */}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Design goal</p>
          <p className="text-base text-foreground">{design_goal}</p>
        </div>

        <Separator />

        {/* Tool selected */}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Tool</p>
          <p className="text-base text-foreground font-medium">{tool}</p>
          <p className="text-sm text-muted-foreground mt-1">{rationale}</p>
        </div>

        {/* Target structure */}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Target structure</p>
          <p className="text-base font-mono text-foreground">
            {target_pdb_id} / chain {target_chain}
          </p>
        </div>

        {/* Hotspot residues */}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Hotspot residues</p>
          <p className="text-base font-mono text-foreground">
            {hotspot_residues.length > 0 ? hotspot_residues.join(", ") : "None specified"}
          </p>
        </div>

        {/* Parameters */}
        {Object.keys(parameters).length > 0 && (
          <div>
            <p className="text-sm text-muted-foreground mb-2">Parameters</p>
            <div className="space-y-1">
              {Object.entries(parameters).map(([key, value]) => {
                const desc = parameter_descriptions?.[key];
                const label = desc?.label ?? key;
                return (
                  <div key={key} className="flex justify-between items-baseline gap-4">
                    <span className="text-sm text-muted-foreground shrink-0">{label}</span>
                    <span className="text-sm font-mono text-foreground text-right">
                      {String(value)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <Separator />

        {/* Estimated cost */}
        <div className="flex justify-between items-center">
          <p className="text-sm text-muted-foreground">Estimated cost</p>
          <p className="text-base font-semibold text-foreground">{costDisplay}</p>
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 pt-1">
          <Button
            variant="default"
            className="flex-1 bg-primary text-primary-foreground hover:bg-primary/90"
            onClick={onLaunch}
            disabled={!canLaunch}
          >
            {disabled ? "Validating..." : "Launch Job"}
          </Button>
          <Button variant="outline" onClick={onEdit} disabled={disabled}>
            {disabled ? "Validating..." : "Edit parameters"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
