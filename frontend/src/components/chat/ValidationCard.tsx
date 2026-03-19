/**
 * ValidationCard — pre-flight checklist before job dispatch.
 *
 * Displays pass/warn/fail results from the validate_preflight tool call.
 * A hard fail blocks launch; warnings require acknowledgment before proceeding.
 */

import { CheckCircle, AlertTriangle, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { ValidationCheck } from "@/lib/agent";

interface ValidationCardData {
  validation_results: ValidationCheck[];
  can_proceed: boolean;
  has_warnings: boolean;
  summary: string;
}

interface ValidationCardProps {
  data: ValidationCardData;
  onAcknowledge: () => void;
  acknowledged: boolean;
}

/** Icon and color for each validation status */
function StatusIcon({ status }: { status: ValidationCheck["status"] }) {
  if (status === "pass") {
    return <CheckCircle className="h-4 w-4 text-green-400 shrink-0" />;
  }
  if (status === "warn") {
    return <AlertTriangle className="h-4 w-4 text-yellow-400 shrink-0" />;
  }
  return <XCircle className="h-4 w-4 text-destructive shrink-0" />;
}

export function ValidationCard({ data, onAcknowledge, acknowledged }: ValidationCardProps) {
  const { validation_results, can_proceed, has_warnings } = data;
  const hasFailures = validation_results.some((r) => r.status === "fail");
  const failMessages = validation_results.filter((r) => r.status === "fail");

  return (
    <Card className="my-2 border-border/50">
      <CardHeader className="px-4 pb-2 pt-4">
        <span className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Pre-flight checks
        </span>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-3">
        {/* Checklist rows */}
        <div className="space-y-2">
          {validation_results.map((check, index) => (
            <div key={index} className="flex items-start gap-2">
              <StatusIcon status={check.status} />
              <div className="min-w-0">
                <span className="text-sm font-medium text-foreground">{check.check_name}</span>
                <p className="text-sm text-muted-foreground">{check.message}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Hard fail alert — blocks launch */}
        {hasFailures && (
          <Alert variant="destructive" className="mt-2">
            <AlertDescription>
              <span className="font-semibold">Cannot proceed — fix the following:</span>
              <ul className="mt-1 ml-4 list-disc space-y-1">
                {failMessages.map((f, i) => (
                  <li key={i}>{f.message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {/* Warn acknowledgment — only shown when warnings exist and no failures */}
        {has_warnings && !hasFailures && can_proceed && (
          <Button
            variant="outline"
            size="sm"
            onClick={onAcknowledge}
            disabled={acknowledged}
            className="mt-1"
          >
            {acknowledged ? "Warnings acknowledged" : "Proceed with warnings"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
