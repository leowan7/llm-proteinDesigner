/**
 * ExpiryWarningBanner — shown at the top of /jobs/{id} when results are
 * within 7 days of the 30-day R2 expiry window.
 *
 * After expiry, PDB files are permanently deleted. The job record itself
 * remains in the database.
 */

import { Alert, AlertDescription } from "@/components/ui/alert";

interface ExpiryWarningBannerProps {
  /** ISO datetime string of when the job completed. */
  completedAt: string;
}

/**
 * Calculates the expiry date as completedAt + 30 days.
 */
function getExpiryDate(completedAt: string): Date {
  const completed = new Date(completedAt);
  const expiry = new Date(completed);
  expiry.setDate(expiry.getDate() + 30);
  return expiry;
}

/**
 * Returns true if the expiry date is within 7 days from now.
 */
function isExpiringWithin7Days(expiryDate: Date): boolean {
  const now = new Date();
  const msUntilExpiry = expiryDate.getTime() - now.getTime();
  const daysUntilExpiry = msUntilExpiry / (1000 * 60 * 60 * 24);
  return daysUntilExpiry >= 0 && daysUntilExpiry <= 7;
}

/**
 * Formats a date as "Month D, YYYY" (e.g. "April 12, 2026").
 */
function formatExpiryDate(date: Date): string {
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function ExpiryWarningBanner({ completedAt }: ExpiryWarningBannerProps) {
  const expiryDate = getExpiryDate(completedAt);

  if (!isExpiringWithin7Days(expiryDate)) {
    return null;
  }

  const expiryDateStr = formatExpiryDate(expiryDate);

  return (
    <Alert variant="default">
      <AlertDescription>
        {/* Exact copy from UI-SPEC */}
        These results expire on {expiryDateStr}. Download your files before then — after
        expiry, the job record remains but PDB files are permanently deleted.
      </AlertDescription>
    </Alert>
  );
}
