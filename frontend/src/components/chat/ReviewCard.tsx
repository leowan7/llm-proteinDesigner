/**
 * ReviewCard — pre-launch confirmation card showing the full job specification.
 *
 * The critical human-in-the-loop gate before dispatching a GPU job. Displays
 * tool selection rationale, target structure, parameters, and estimated cost.
 * Requires validation to pass (and warnings acknowledged if any) before
 * the Launch Job button becomes active.
 *
 * Launch flow:
 * 1. Call getCostEstimate on mount to show the range before launch.
 * 2. On "Launch Job" click: check getPaymentStatus().
 *    - No payment method → redirect to Stripe Checkout via createCheckoutSession().
 *    - Has payment method → POST /jobs/launch, then call onJobLaunched(jobId).
 * 3. On Stripe return with ?setup=success: auto-retry launch.
 *    On ?setup=cancelled: show inline alert.
 */

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import type { ReviewData } from "@/lib/agent";
import {
  getCostEstimate,
  getPaymentStatus,
  createCheckoutSession,
  launchJob,
} from "@/lib/jobs";
import type { CostEstimate } from "@/lib/jobs";

interface ReviewCardProps {
  data: ReviewData;
  onJobLaunched: (jobId: string) => void;
  onEdit: () => void;
  disabled: boolean;
  warningsAcknowledged: boolean;
  /** When true, auto-retry launch after Stripe Checkout success return. */
  autoRetryAfterSetup?: boolean;
  /** When true, show the "payment cancelled" alert immediately. */
  setupCancelled?: boolean;
}

export function ReviewCard({
  data,
  onJobLaunched,
  onEdit,
  disabled,
  warningsAcknowledged,
  autoRetryAfterSetup = false,
  setupCancelled = false,
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
    validation_results,
    can_proceed,
    has_warnings,
  } = data;

  const [costEstimate, setCostEstimate] = useState<CostEstimate | null>(null);
  const [estimateError, setEstimateError] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [paymentAlertVisible, setPaymentAlertVisible] = useState(setupCancelled);
  const [launchError, setLaunchError] = useState<string | null>(null);

  // Launch is available when: validation complete, can proceed, warnings acknowledged (if any)
  const validationComplete = validation_results.length > 0;
  const warningsOk = !has_warnings || warningsAcknowledged;
  const canLaunch = !disabled && !launching && validationComplete && can_proceed && warningsOk;

  // Number of designs to estimate — use parameters.num_designs if available
  const numDesigns = typeof parameters.num_designs === "number" ? parameters.num_designs : 1;

  // Markup percentage for the breakdown display
  const markupPercent = 30;

  // Fetch cost estimate on mount
  useEffect(() => {
    getCostEstimate(tool, numDesigns)
      .then((estimate) => setCostEstimate(estimate))
      .catch(() => setEstimateError(true));
  }, [tool, numDesigns]);

  /**
   * Core launch logic: check payment method, then POST /jobs/launch.
   * Shared between the button click and the auto-retry after Stripe setup.
   */
  const doLaunch = useCallback(async () => {
    setLaunching(true);
    setLaunchError(null);
    setPaymentAlertVisible(false);

    try {
      // 1. Check payment method
      const { has_payment_method } = await getPaymentStatus();

      if (!has_payment_method) {
        // Redirect to Stripe Checkout for card setup
        const { url } = await createCheckoutSession(window.location.href);
        window.location.href = url;
        // Navigation away — no need to reset launching state
        return;
      }

      // 2. POST /jobs/launch — job_id must come from the wizard data
      // The job_id is embedded in the ReviewData via the agent (stored as parameters.job_id)
      const jobId = parameters.job_id as string | undefined;
      if (!jobId) {
        throw new Error("Job ID not available — please restart the wizard.");
      }

      let result: { job_id: string; status: string };
      try {
        result = await launchJob(jobId);
      } catch (err) {
        if (err instanceof Error && err.message === "payment_required") {
          // Payment method disappeared between check and dispatch (race) — redirect
          const { url } = await createCheckoutSession(window.location.href);
          window.location.href = url;
          return;
        }
        throw err;
      }

      onJobLaunched(result.job_id);
    } catch (err) {
      setLaunchError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
      setLaunching(false);
    }
  }, [parameters.job_id, onJobLaunched]);

  // Auto-retry launch when returning from successful Stripe Checkout
  useEffect(() => {
    if (autoRetryAfterSetup && canLaunch) {
      void doLaunch();
    }
    // Only fire once on mount when autoRetryAfterSetup is true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRetryAfterSetup]);

  // Show payment cancelled alert when prop is set
  useEffect(() => {
    if (setupCancelled) {
      setPaymentAlertVisible(true);
    }
  }, [setupCancelled]);

  function renderCostSection() {
    if (estimateError) {
      return (
        <div>
          <p className="text-sm text-muted-foreground">Estimated GPU cost</p>
          <p className="text-sm text-muted-foreground italic">Estimate unavailable</p>
        </div>
      );
    }

    if (!costEstimate) {
      return (
        <div>
          <p className="text-sm text-muted-foreground">Estimated GPU cost</p>
          <p className="text-sm text-muted-foreground">Loading estimate...</p>
        </div>
      );
    }

    const { low, high } = costEstimate;
    // Markup-inclusive totals (estimate already includes markup from the backend)
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground mb-1">Estimated GPU cost</p>
        {/* Cost breakdown rows */}
        <div className="space-y-1">
          <div className="flex justify-between items-baseline gap-4">
            <span className="text-sm text-muted-foreground">GPU compute</span>
            <span className="text-sm font-mono text-foreground">
              ${(low / (1 + markupPercent / 100)).toFixed(2)}–${(high / (1 + markupPercent / 100)).toFixed(2)}
            </span>
          </div>
          <div className="flex justify-between items-baseline gap-4">
            <span className="text-sm text-muted-foreground">Platform fee</span>
            <span className="text-sm font-mono text-foreground">{markupPercent}% platform fee</span>
          </div>
          <div className="flex justify-between items-baseline gap-4">
            <span className="text-sm text-muted-foreground font-medium">Total</span>
            <span className="text-sm font-mono font-medium text-foreground">
              ${low.toFixed(2)}–${high.toFixed(2)} estimated
            </span>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          Actual charge is based on GPU time consumed and may differ from this estimate.
        </p>
      </div>
    );
  }

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
              {Object.entries(parameters)
                .filter(([key]) => key !== "job_id") // Don't display internal job_id
                .map(([key, value]) => {
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

        {/* Cost estimate with breakdown */}
        {renderCostSection()}

        {/* Payment required alert (shown after Stripe setup cancelled) */}
        {paymentAlertVisible && (
          <Alert>
            <AlertDescription>
              A payment method is required to launch jobs. Add a payment method to continue.
            </AlertDescription>
          </Alert>
        )}

        {/* Launch error */}
        {launchError && (
          <Alert>
            <AlertDescription>{launchError}</AlertDescription>
          </Alert>
        )}

        {/* Action buttons */}
        <div className="flex gap-3 pt-1">
          <Button
            variant="default"
            className="flex-1 bg-primary text-primary-foreground hover:bg-primary/90"
            onClick={() => void doLaunch()}
            disabled={!canLaunch}
          >
            {launching ? "Launching..." : "Launch Job"}
          </Button>
          <Button variant="outline" onClick={onEdit} disabled={disabled || launching}>
            {disabled ? "Validating..." : "Edit parameters"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
